"""The ODbL download: the OpenStreetMap part of the hike database, nothing else (issue #122)."""

import logging
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from dayhike import build_catalog, build_similarity
from dayhike.cli import main
from dayhike.osm_download import export_osm_download
from dayhike.serving import ServingData, decode_polyline, export_serving
from dayhike.web import create_app

from test_built_hikes import FIXTURE, RIDGE_WALK_ID
from test_external import EXTERNAL_DIR, SOURCE

# Every column the download may hold: OSM ids and tags, names, kind, geometry and the OSM points along it.
HIKE_COLUMNS = {
    "id", "name", "name_origin", "kind", "route", "parts", "source_ref", "sac_scale",
    "walk_m", "trailhead_lat", "trailhead_lon", "geometry_wkt",
}  # fmt: skip
POINT_COLUMNS = {"hike", "kind", "place", "lat", "lon", "along_m", "off_m", "name"}


@pytest.fixture(scope="module")
def serving(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A serving export with an OpenStreetMap hike and an external one (Sport Ireland)."""
    directory = tmp_path_factory.mktemp("osm-download")
    path = directory / "catalog.sqlite"
    build_catalog(FIXTURE, path, sources=[], external=[SOURCE], external_dir=EXTERNAL_DIR)
    build_similarity(path, directory / "dem")
    return export_serving(path, directory / "web-serving")


@pytest.fixture(scope="module")
def download(serving: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    return export_osm_download(serving, tmp_path_factory.mktemp("download"), data_date="2026-11-15")


def rows(path: Path, sql: str) -> list[tuple]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        return db.execute(sql).fetchall()


def test_the_download_holds_every_osm_hike_and_no_external_one(serving: Path, download: Path) -> None:
    with sqlite3.connect(f"file:{serving / 'hikes.sqlite'}?mode=ro", uri=True) as db:
        osm_ids = {row[0] for row in db.execute("SELECT id FROM hikes WHERE source = 'osm'")}
        external_ids = {row[0] for row in db.execute("SELECT id FROM hikes WHERE source != 'osm'")}

    assert external_ids and RIDGE_WALK_ID in osm_ids  # the fixture has both layers
    assert {row[0] for row in rows(download / "hikes-osm.sqlite", "SELECT id FROM hikes")} == osm_ids
    # An external hike's points are not in it either.
    assert {row[0] for row in rows(download / "hikes-osm.sqlite", "SELECT hike FROM hike_points")} <= osm_ids


def test_the_download_has_the_osm_columns_only(download: Path) -> None:
    file = download / "hikes-osm.sqlite"
    tables = {row[0] for row in rows(file, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    hike_columns = {row[1] for row in rows(file, "PRAGMA table_info(hikes)")}
    point_columns = {row[1] for row in rows(file, "PRAGMA table_info(hike_points)")}

    # No heights, land cover, climate, places or other source's fields: not a column, not a table.
    assert tables == {"hikes", "hike_points", "licence"}
    assert hike_columns == HIKE_COLUMNS
    assert point_columns == POINT_COLUMNS


def test_a_hike_row_carries_its_osm_name_route_line_and_points(serving: Path, download: Path) -> None:
    file = download / "hikes-osm.sqlite"
    name, origin, kind, source_ref, walk_m, wkt, th_lat, th_lon = rows(
        file,
        "SELECT name, name_origin, kind, source_ref, walk_m, geometry_wkt, trailhead_lat, trailhead_lon "
        f"FROM hikes WHERE id = {RIDGE_WALK_ID}",
    )[0]
    with sqlite3.connect(f"file:{serving / 'hikes.sqlite'}?mode=ro", uri=True) as db:
        served_name, served_line, served_ref = db.execute(
            "SELECT name, line, source_ref FROM hikes WHERE id = ?", (RIDGE_WALK_ID,)
        ).fetchone()

    assert (name, source_ref) == (served_name, served_ref)
    assert origin == "osm_name" and kind == "relation" and walk_m > 0
    # The line is the served one, as a WKT LineString in longitude-latitude order.
    line = decode_polyline(served_line)
    assert wkt == "LINESTRING(" + ", ".join(f"{lon:.5f} {lat:.5f}" for lat, lon in line) + ")"
    ids = np.load(serving / "similarity" / "ids.npy").tolist()
    assert (th_lat, th_lon) == tuple(np.load(serving / "trailheads.npy")[ids.index(RIDGE_WALK_ID)])
    # The amenity points along it, named from OSM tags, without their height.
    points = rows(file, f"SELECT kind, place, lat, lon, name FROM hike_points WHERE hike = {RIDGE_WALK_ID}")
    assert points and {place for _, place, *_ in points} <= {"line", "trailhead"}


def test_the_licence_table_and_readme_carry_the_odbl_notice_attribution_and_data_date(download: Path) -> None:
    licence = dict(rows(download / "hikes-osm.sqlite", "SELECT key, value FROM licence"))
    readme = (download / "README.txt").read_text(encoding="utf-8")

    assert licence["licence"] == "Open Database License (ODbL) 1.0"
    assert licence["licence_url"] == "https://opendatacommons.org/licenses/odbl/1-0/"
    assert licence["attribution"] == "© OpenStreetMap contributors"
    assert licence["data_date"] == "2026-11-15"
    assert "not included" in licence["contents"].lower() or "left out" in licence["contents"].lower()
    for text in (licence["licence"], licence["attribution"], licence["data_date"], "hikes-osm.sqlite"):
        assert text in readme
    assert "https://www.openstreetmap.org/copyright" in readme


def test_the_data_date_defaults_to_the_day_the_export_was_written(serving: Path, tmp_path: Path) -> None:
    out = export_osm_download(serving, tmp_path)
    licence = dict(rows(out / "hikes-osm.sqlite", "SELECT key, value FROM licence"))

    assert len(licence["data_date"]) == 10 and licence["data_date"][:2] == "20"


def test_a_second_run_replaces_the_download(serving: Path, tmp_path: Path) -> None:
    export_osm_download(serving, tmp_path, data_date="2026-01-01")
    export_osm_download(serving, tmp_path, data_date="2026-02-02")

    assert dict(rows(tmp_path / "hikes-osm.sqlite", "SELECT key, value FROM licence"))["data_date"] == "2026-02-02"


def test_the_command_writes_the_download_beside_nothing_of_the_export(serving: Path, tmp_path: Path, capsys) -> None:
    assert main(["export-osm-download", "--data", str(serving), "--out", str(tmp_path), "--data-date", "2026-11-15"]) == 0

    assert sorted(p.name for p in tmp_path.iterdir()) == ["README.txt", "hikes-osm.sqlite"]
    assert "hikes-osm.sqlite" in capsys.readouterr().out


def test_the_credits_page_links_the_download_only_when_its_address_is_set(serving: Path) -> None:
    data = ServingData(serving)
    with_link = TestClient(create_app(data, None, download_url="https://maps.example.com/hikes-osm.sqlite")).get("/credits.html").text
    without = TestClient(create_app(data, None)).get("/credits.html").text
    empty = TestClient(create_app(data, None, download_url="")).get("/credits.html").text

    assert 'href="https://maps.example.com/hikes-osm.sqlite"' in with_link
    assert "Download the data" in with_link
    # It says what the file holds and what it leaves out.
    assert "OpenStreetMap part" in with_link and "heights" in with_link
    assert "Download the data" not in without and "hikes-osm" not in without
    assert empty == without


@pytest.mark.parametrize("local", [
    "http://127.0.0.1:8081/hikes-osm.sqlite", "http://localhost:8081/x", "http://[::1]:8081/x", "http://0.0.0.0/x",
    "http://127.0.0.2:8081/x", "http://[::ffff:127.0.0.1]:8081/x", "http://foo.localhost:8081/x",
])
def test_the_credits_page_drops_a_download_address_only_this_machine_can_reach(serving: Path, local: str) -> None:
    page = TestClient(create_app(ServingData(serving), None, download_url=local)).get("/credits.html").text

    assert "Download the data" not in page and "8081" not in page


def test_a_dropped_download_address_is_logged_once_with_its_value(serving: Path, caplog: pytest.LogCaptureFixture) -> None:
    local = "http://127.0.0.1:8081/hikes-osm.sqlite"
    with caplog.at_level(logging.WARNING, logger="dayhike.web"):
        create_app(ServingData(serving), None, download_url=local)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and r.name == "dayhike.web"]
    assert len(warnings) == 1 and local in warnings[0].getMessage()


def test_a_public_download_address_that_only_looks_local_keeps_its_link(serving: Path) -> None:
    page = TestClient(create_app(ServingData(serving), None, download_url="https://localhost.example.com/x")).get("/credits.html").text

    assert "Download the data" in page and "localhost.example.com" in page
