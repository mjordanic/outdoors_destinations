import math
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest

from dayhike import Catalog, build_catalog, geometry_store_path_for

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
STAGES = ("filter", "relations", "ways", "nodes", "access", "assemble", "write")


def test_build_catalog_reports_ingest_stages(tmp_path: Path) -> None:
    messages: list[str] = []
    catalog = build_catalog(
        FIXTURE, tmp_path / "catalog.sqlite", progress=messages.append
    )

    stages = [message.split(":", 1)[0] for message in messages]
    for stage in STAGES:
        assert stage in stages, messages
    assert catalog.ingest_report.kept == 4
    assert catalog.ingest_report.dropped_unnamed == 0


def test_build_catalog_reports_loading_the_work_file_before_access(
    tmp_path: Path,
) -> None:
    messages: list[str] = []
    build_catalog(FIXTURE, tmp_path / "catalog.sqlite", progress=messages.append)

    nodes_done = next(
        i for i, message in enumerate(messages) if message.startswith("nodes:")
    )
    access = [message for message in messages[nodes_done:] if message.startswith("access:")]
    assert any("loading work file" in message for message in access), messages
    assert any("endpoint" in message for message in access), messages
    load_at = next(i for i, message in enumerate(access) if "loading work file" in message)
    assert load_at == 0, access


def test_ingest_resumes_after_progress_callback_raises(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    work_path = catalog_path.with_name(catalog_path.name + ".work")

    def kill_on_access(message: str) -> None:
        if message.startswith("access:"):
            raise RuntimeError("killed")

    with pytest.raises(RuntimeError, match="killed"):
        build_catalog(FIXTURE, catalog_path, progress=kill_on_access)

    assert not catalog_path.exists()
    assert work_path.exists()

    messages: list[str] = []
    catalog = build_catalog(FIXTURE, catalog_path, progress=messages.append)

    assert catalog.ingest_report.kept == 4
    assert {"Ridge Walk", "Valley Path"} <= {hike.name for hike in catalog.dayhikes}
    assert not work_path.exists()
    assert any("resumed, already complete" in message for message in messages)


def test_failed_write_leaves_the_previous_catalog(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    first = build_catalog(FIXTURE, catalog_path)

    def kill_on_write(message: str) -> None:
        if message.startswith("write:"):
            raise RuntimeError("killed")

    with pytest.raises(RuntimeError, match="killed"):
        build_catalog(FIXTURE, catalog_path, progress=kill_on_write)

    catalog = Catalog.open(catalog_path)
    assert [hike.id for hike in catalog.dayhikes] == [hike.id for hike in first.dayhikes]
    assert catalog.ingest_report == first.ingest_report


def test_unused_osm_objects_do_not_change_kept_dayhikes(tmp_path: Path) -> None:
    noisy = tmp_path / "noisy.osm"
    extra = "".join(
        f'  <node id="{9000 + i}" lat="46.0" lon="16.0" version="1"/>\n' for i in range(50)
    )
    extra += """  <way id="9001" version="1">
    <nd ref="9000"/>
    <nd ref="9049"/>
    <tag k="highway" v="residential"/>
  </way>
</osm>
"""
    noisy.write_text(FIXTURE.read_text().replace("</osm>", extra))
    catalog = build_catalog(noisy, tmp_path / "catalog.sqlite")
    fixture = build_catalog(FIXTURE, tmp_path / "fixture.sqlite")

    assert [hike.id for hike in catalog.dayhikes] == [hike.id for hike in fixture.dayhikes]
    assert catalog.ingest_report == fixture.ingest_report


def test_fresh_discards_an_existing_work_file(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"

    def kill_on_access(message: str) -> None:
        if message.startswith("access:"):
            raise RuntimeError("killed")

    with pytest.raises(RuntimeError, match="killed"):
        build_catalog(FIXTURE, catalog_path, progress=kill_on_access)

    messages: list[str] = []
    catalog = build_catalog(
        FIXTURE, catalog_path, progress=messages.append, fresh=True
    )

    assert catalog.ingest_report.kept == 4
    assert not any("resumed, already complete" in message for message in messages)


@pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium-tool")
def test_access_uses_osmium_extract_and_keeps_fixture_dayhikes(tmp_path: Path) -> None:
    messages: list[str] = []
    catalog = build_catalog(
        _sorted_pbf(FIXTURE, tmp_path / "catalog.osm.pbf"),
        tmp_path / "catalog.sqlite",
        progress=messages.append,
    )

    assert any(
        message.startswith("access: extract ") and "->" in message
        for message in messages
    ), messages
    assert catalog.ingest_report.kept == 4
    assert {"Ridge Walk", "Valley Path"} <= {hike.name for hike in catalog.dayhikes}


@pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium-tool")
def test_access_extract_sidecars_are_removed_after_write(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(_sorted_pbf(FIXTURE, tmp_path / "catalog.osm.pbf"), catalog_path)
    assert list(tmp_path.glob("catalog.sqlite.access*")) == []


@pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium-tool")
@pytest.mark.parametrize(
    "fixture",
    [
        FIXTURE,
        Path(__file__).parent / "fixtures" / "trailhead_at_highway.osm",
        Path(__file__).parent / "fixtures" / "trailhead_at_parking.osm",
    ],
)
def test_access_extract_matches_full_source_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixture: Path
) -> None:
    pbf = _sorted_pbf(fixture, tmp_path / "source.osm.pbf")
    extracted = build_catalog(pbf, tmp_path / "extracted.sqlite")
    monkeypatch.setattr("dayhike.ingest.shutil.which", lambda _name: None)
    scanned = build_catalog(pbf, tmp_path / "scanned.sqlite")

    assert _dayhike_signature(extracted) == _dayhike_signature(scanned)
    assert extracted.ingest_report == scanned.ingest_report


@pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium-tool")
def test_a_filtered_copy_made_with_other_tag_filters_is_rebuilt(tmp_path: Path) -> None:
    source = _sorted_pbf(FIXTURE, tmp_path / "source.osm.pbf")
    # A newer filtered copy from a filter list without springs.
    subprocess.run(
        [
            "osmium",
            "tags-filter",
            str(source),
            "r/route=hiking",
            "-o",
            str(tmp_path / "source.catalog.osm.pbf"),
            "--no-progress",
        ],
        check=True,
    )
    catalog_path = tmp_path / "catalog.sqlite"

    messages: list[str] = []
    build_catalog(source, catalog_path, progress=messages.append)
    again: list[str] = []
    build_catalog(source, catalog_path, progress=again.append)

    assert "filter: source.osm.pbf -> source.catalog.osm.pbf" in messages, messages
    connection = sqlite3.connect(geometry_store_path_for(catalog_path))
    try:
        spring = connection.execute(
            "SELECT kind FROM amenities WHERE osm_key = 'n57'"
        ).fetchone()
    finally:
        connection.close()
    assert spring == ("spring",)
    assert any(message.startswith("filter: cached ") for message in again), again


def test_access_extract_boxes_contain_the_trailhead_access_disk() -> None:
    from dayhike.catalog import TRAILHEAD_ACCESS_RADIUS_M
    from dayhike.ingest import _containing_bbox

    radius = TRAILHEAD_ACCESS_RADIUS_M
    for lat, lon in ((0.0, 0.0), (45.0, 15.0), (64.0, 10.0), (-33.0, 18.0)):
        minlon, minlat, maxlon, maxlat = _containing_bbox(lat, lon, radius + radius)
        for bearing in range(0, 360, 15):
            point_lat, point_lon = _destination(lat, lon, radius, bearing)
            assert minlat < point_lat < maxlat
            assert minlon < point_lon < maxlon


def _sorted_pbf(source: Path, dest: Path) -> Path:
    subprocess.run(
        [
            "osmium",
            "sort",
            str(source),
            "-o",
            str(dest),
            "--overwrite",
            "--no-progress",
        ],
        check=True,
    )
    return dest


def _dayhike_signature(catalog: Catalog) -> list[tuple[object, ...]]:
    return [
        (
            hike.id,
            hike.name,
            round(hike.length_m, 3),
            round(hike.trailhead.latitude, 7),
            round(hike.trailhead.longitude, 7),
            hike.trailhead.amenity_facts.parking.count,
        )
        for hike in catalog.dayhikes
    ]


def _destination(
    lat: float, lon: float, distance_m: float, bearing_deg: float
) -> tuple[float, float]:
    from dayhike.catalog import EARTH_RADIUS_M

    angular = distance_m / EARTH_RADIUS_M
    bearing = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular)
        + math.cos(lat1) * math.sin(angular) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular) * math.cos(lat1),
        math.cos(angular) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), (math.degrees(lon2) + 180.0) % 360.0 - 180.0
