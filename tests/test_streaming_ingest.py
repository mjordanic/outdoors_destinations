from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest

from dayhike import Catalog, build_catalog, geometry_store_path_for

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
STAGES = ("filter", "relations", "ways", "nodes", "assemble", "write")


def test_build_catalog_reports_ingest_stages(tmp_path: Path) -> None:
    messages: list[str] = []
    catalog = build_catalog(
        FIXTURE, tmp_path / "catalog.sqlite", progress=messages.append
    )

    stages = [message.split(":", 1)[0] for message in messages]
    for stage in STAGES:
        assert stage in stages, messages
    assert "access" not in stages, messages
    assert catalog.ingest_report.kept == 4
    assert catalog.ingest_report.dropped_unnamed == 0


def test_build_catalog_reports_loading_the_work_file_before_assembling(
    tmp_path: Path,
) -> None:
    messages: list[str] = []
    build_catalog(FIXTURE, tmp_path / "catalog.sqlite", progress=messages.append)

    nodes_done = next(
        i for i, message in enumerate(messages) if message.startswith("nodes:")
    )
    assemble = [
        message for message in messages[nodes_done:] if message.startswith("assemble:")
    ]
    assert assemble[0] == "assemble: loading work file", messages


def test_ingest_resumes_after_progress_callback_raises(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    work_path = catalog_path.with_name(catalog_path.name + ".work")

    def kill_on_assemble(message: str) -> None:
        if message.startswith("assemble:"):
            raise RuntimeError("killed")

    with pytest.raises(RuntimeError, match="killed"):
        build_catalog(FIXTURE, catalog_path, progress=kill_on_assemble)

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

    def kill_on_assemble(message: str) -> None:
        if message.startswith("assemble:"):
            raise RuntimeError("killed")

    with pytest.raises(RuntimeError, match="killed"):
        build_catalog(FIXTURE, catalog_path, progress=kill_on_assemble)

    messages: list[str] = []
    catalog = build_catalog(
        FIXTURE, catalog_path, progress=messages.append, fresh=True
    )

    assert catalog.ingest_report.kept == 4
    assert not any("resumed, already complete" in message for message in messages)


@pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium-tool")
def test_a_pbf_ingest_runs_osmium_only_for_the_hiking_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _sorted_pbf(FIXTURE, tmp_path / "catalog.osm.pbf")
    osmium_commands: list[str] = []
    run = subprocess.run

    def record(cmd, *args, **kwargs):
        if Path(cmd[0]).name == "osmium":
            osmium_commands.append(cmd[1])
        return run(cmd, *args, **kwargs)

    monkeypatch.setattr("dayhike.ingest.subprocess.run", record)
    catalog = build_catalog(source, tmp_path / "catalog.sqlite")

    assert osmium_commands == ["tags-filter"]
    assert catalog.ingest_report.kept == 4
    assert list(tmp_path.glob("catalog.sqlite.access*")) == []


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


# Schema 3 had no member roles; schema 4 had the drivable-road access tables.
@pytest.mark.parametrize("old_schema", ["3", "4"])
def test_a_work_file_of_an_older_schema_is_not_resumed(
    tmp_path: Path, old_schema: str
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    work_path = catalog_path.with_name(catalog_path.name + ".work")

    def kill_on_assemble(message: str) -> None:
        if message.startswith("assemble:"):
            raise RuntimeError("killed")

    with pytest.raises(RuntimeError, match="killed"):
        build_catalog(FIXTURE, catalog_path, progress=kill_on_assemble)
    with sqlite3.connect(work_path) as conn:
        conn.execute("UPDATE meta SET value = ? WHERE key = 'schema'", (old_schema,))

    messages: list[str] = []
    build_catalog(FIXTURE, catalog_path, progress=messages.append)

    assert not any("resumed, already complete" in message for message in messages)
