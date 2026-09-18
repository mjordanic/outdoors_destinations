from pathlib import Path

import pytest

from dayhike import Catalog, build_catalog

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
    assert catalog.ingest_report.kept == 2
    assert catalog.ingest_report.dropped_unnamed == 1


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

    assert catalog.ingest_report.kept == 2
    assert {hike.name for hike in catalog.dayhikes} == {"Ridge Walk", "Valley Path"}
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

    assert catalog.ingest_report.kept == 2
    assert not any("resumed, already complete" in message for message in messages)
