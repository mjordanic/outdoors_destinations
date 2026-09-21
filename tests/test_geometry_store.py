import json
import sqlite3
from pathlib import Path

import pytest

from dayhike import build_catalog, geometry_store_path_for, resolve_seed
from dayhike.catalog import NAME_ORIGIN_SYNTHETIC

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"


def test_build_catalog_writes_geometry_store_with_vertices_ways_and_amenities(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    catalog = build_catalog(FIXTURE, catalog_path)
    geometry_path = geometry_store_path_for(catalog_path)

    assert geometry_path.is_file()
    connection = sqlite3.connect(geometry_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert tables >= {"geometry_meta", "dayhikes", "vertices", "ways", "amenities"}
        ids = {
            row[0] for row in connection.execute("SELECT id FROM dayhikes")
        }
        assert ids == {hike.id for hike in catalog.dayhikes}

        origin, label = connection.execute(
            "SELECT name_origin, name FROM dayhikes WHERE id = 1003"
        ).fetchone()
        assert origin == NAME_ORIGIN_SYNTHETIC
        assert label == "relation:1003"

        vertex_count = connection.execute(
            "SELECT COUNT(*) FROM vertices WHERE dayhike_id = 1001"
        ).fetchone()[0]
        assert vertex_count >= 2

        way_tags = json.loads(
            connection.execute(
                "SELECT tags FROM ways WHERE dayhike_id = 1001 ORDER BY seq LIMIT 1"
            ).fetchone()[0]
        )
        assert way_tags["highway"] == "path"

        kinds = {
            row[0] for row in connection.execute("SELECT DISTINCT kind FROM amenities")
        }
        assert kinds >= {
            "hut",
            "parking",
            "restaurant_cafe",
            "drinking_water",
            "viewpoint",
            "peak",
            "shelter",
        }
        cafe_tags = json.loads(
            connection.execute(
                "SELECT tags FROM amenities WHERE osm_key = 'n50'"
            ).fetchone()[0]
        )
        assert cafe_tags["name"] == "Ridge Cafe"
        assert cafe_tags["amenity"] == "cafe"
        peak_tags = json.loads(
            connection.execute(
                "SELECT tags FROM amenities WHERE kind = 'peak'"
            ).fetchone()[0]
        )
        assert peak_tags["name"] == "Ridge Peak"
    finally:
        connection.close()

    assert resolve_seed(catalog, "relation:1003") == 1003
    assert resolve_seed(catalog, "PS-1") == 1007


def test_geometry_store_skips_unconnected_member_ways(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    catalog = build_catalog(FIXTURE, catalog_path)
    ridge = next(hike for hike in catalog.dayhikes if hike.id == 1001)
    assert ridge.length_m == pytest.approx(10_000.0, abs=0.01)

    connection = sqlite3.connect(geometry_store_path_for(catalog_path))
    try:
        unconnected_m = connection.execute(
            "SELECT unconnected_m FROM dayhikes WHERE id = 1001"
        ).fetchone()[0]
        assert unconnected_m == pytest.approx(1_000.0, abs=0.01)

        vertex_ids = [
            row[0]
            for row in connection.execute(
                "SELECT node_id FROM vertices WHERE dayhike_id = 1001 ORDER BY seq"
            )
        ]
        assert vertex_ids == [1, 2, 4, 3]

        way_flags = list(
            connection.execute(
                "SELECT way_id, in_chain FROM ways WHERE dayhike_id = 1001 ORDER BY seq"
            )
        )
        assert way_flags == [(101, 1), (103, 0), (102, 1)]

        valley_unconnected = connection.execute(
            "SELECT unconnected_m FROM dayhikes WHERE id = 1002"
        ).fetchone()[0]
        assert valley_unconnected == pytest.approx(0.0, abs=0.01)
    finally:
        connection.close()

