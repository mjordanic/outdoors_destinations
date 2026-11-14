"""The sources register and the Source of every DayHike (ADR-0060)."""

import re
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dayhike import build_catalog, build_similarity
from dayhike.serving import ServingData, export_serving
from dayhike.sources import (
    COLUMNS,
    COMMERCIAL_OK,
    KINDS,
    ODBL_COMPATIBLE,
    REGISTER_PATH,
    STATUSES,
    osm_source_ref,
    read_register,
    require_used,
    used_source_ids,
)
from dayhike.web import create_app

from test_built_hikes import FIXTURE, RIDGE_WALK_ID, ridge_stage

DATA_LICENSES = Path(__file__).parents[1] / "DATA-LICENSES.md"


def test_every_register_row_has_its_fields_and_allowed_values() -> None:
    with open(REGISTER_PATH, newline="", encoding="utf-8") as handle:
        assert tuple(handle.readline().strip().split(",")) == COLUMNS
    rows = read_register()

    assert len({row["id"] for row in rows}) == len(rows)
    for row in rows:
        where = row["id"]
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", row["id"]), where
        for column in ("name", "kind", "url", "licence", "licence_url", "status", "reason", "checked"):
            assert row[column].strip(), (where, column)
        assert row["kind"] in KINDS, where
        assert row["odbl_compatible"] in ODBL_COMPATIBLE, where
        assert row["commercial_ok"] in COMMERCIAL_OK, where
        assert row["status"] in STATUSES, where
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["checked"]), where
        for column in ("url", "licence_url"):
            assert row[column].startswith("https://"), (where, column)
        assert row["issue"] == "" or re.fullmatch(r"#\d+", row["issue"]), where


def test_every_used_source_is_in_data_licenses() -> None:
    licences = DATA_LICENSES.read_text()

    for row in read_register():
        if row["status"] == "used":
            assert row["name"] in licences, row["id"]


def test_openstreetmap_is_a_used_source() -> None:
    assert "osm" in used_source_ids()


def test_a_source_the_register_does_not_list_as_used_is_refused(tmp_path: Path) -> None:
    register = tmp_path / "sources.csv"
    shutil.copyfile(REGISTER_PATH, register)

    require_used({"osm"}, register)
    with pytest.raises(ValueError, match="not-a-source"):
        require_used({"osm", "not-a-source"}, register)
    rejected = next(row["id"] for row in read_register(register) if row["status"] == "rejected")
    with pytest.raises(ValueError, match=rejected):
        require_used({rejected}, register)


def test_an_osm_hike_points_at_its_relation_or_where_it_was_built_from() -> None:
    assert osm_source_ref(1001, "relation", (), None) == "relation/1001"
    # A DayStage is cut from one tour; a NetworkHike chains legs from its start node.
    assert osm_source_ref(2**40 + 5, "stage", (1001,), "2:102.3") == "relation/1001"
    assert osm_source_ref(2**40 + 6, "network", (7, 8), "42:-9.43,10.44") == "node/42"


@pytest.fixture(scope="module")
def catalog_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("sources")
    path = directory / "catalog.sqlite"
    build_catalog(FIXTURE, path, sources=[ridge_stage])
    build_similarity(path, directory / "dem")
    return path


@pytest.fixture(scope="module")
def export(catalog_path: Path) -> Path:
    return export_serving(catalog_path, catalog_path.parent / "web-serving")


def test_ingest_records_the_source_of_every_dayhike(catalog_path: Path) -> None:
    with sqlite3.connect(catalog_path) as catalog:
        rows = catalog.execute("SELECT id, kind, source, source_ref FROM dayhikes").fetchall()

    assert {source for _, _, source, _ in rows} == {"osm"}
    for dayhike_id, kind, _, source_ref in rows:
        expected = f"relation/{dayhike_id}" if kind == "relation" else f"relation/{RIDGE_WALK_ID}"
        assert source_ref == expected


def test_the_export_gives_every_hike_a_used_source(export: Path) -> None:
    with sqlite3.connect(export / "hikes.sqlite") as hikes:
        rows = hikes.execute("SELECT source, source_ref FROM hikes").fetchall()

    assert rows
    assert all(source and source_ref for source, source_ref in rows)
    assert {source for source, _ in rows} <= used_source_ids()


def test_the_api_returns_a_hike_s_source_and_a_link_to_its_record(export: Path) -> None:
    client = TestClient(create_app(ServingData(export), None))

    assert client.get(f"/api/hikes/{RIDGE_WALK_ID}").json()["source"] == {
        "id": "osm",
        "ref": f"relation/{RIDGE_WALK_ID}",
        "url": f"https://www.openstreetmap.org/relation/{RIDGE_WALK_ID}",
    }
    (stage,) = client.get("/api/search", params={"q": "stage 2"}).json()["results"]
    assert client.get(f"/api/hikes/{stage['id']}").json()["source"]["ref"] == f"relation/{RIDGE_WALK_ID}"


def test_a_catalog_and_an_export_from_before_sources_read_as_openstreetmap(catalog_path: Path, tmp_path: Path) -> None:
    shutil.copytree(catalog_path.parent, tmp_path / "old", ignore=shutil.ignore_patterns("web-serving"))
    old = tmp_path / "old" / catalog_path.name
    with sqlite3.connect(old) as catalog:
        for column in ("source_ref", "source"):
            catalog.execute(f"ALTER TABLE dayhikes DROP COLUMN {column}")
    export = export_serving(old, tmp_path / "web-serving")
    with sqlite3.connect(export / "hikes.sqlite") as hikes:
        assert hikes.execute("SELECT DISTINCT source FROM hikes").fetchall() == [("osm",)]
        for column in ("source_ref", "source"):
            hikes.execute(f"ALTER TABLE hikes DROP COLUMN {column}")

    assert ServingData(export).detail(RIDGE_WALK_ID)["source"]["ref"] == f"relation/{RIDGE_WALK_ID}"
