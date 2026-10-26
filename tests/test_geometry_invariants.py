"""Structural invariants of a full GeometryStore next to its Catalog.

Slow, opt-in (DAYHIKE_REAL_DATA=1); reads DAYHIKE_DATA_DIR (default data/).
The main-walk checks stay in tests/test_real_data.py.
"""

from __future__ import annotations

import sqlite3

import pytest
import rebuild_checks as rc

from dayhike import geometry_store_path_for

pytestmark = rc.real_data


@pytest.fixture(scope="module")
def geometry() -> sqlite3.Connection:
    connection = rc.connect_ro(geometry_store_path_for(rc.catalog_path()))
    yield connection
    connection.close()


def test_geometry_store_holds_exactly_the_catalog_dayhikes(geometry: sqlite3.Connection) -> None:
    catalog = rc.connect_ro(rc.catalog_path())
    try:
        diff = rc.compare_tables(catalog, geometry, "dayhikes", ["id"], ["name", "name_origin", "length_m", "difficulty"])
    finally:
        catalog.close()

    assert diff.equal, diff.report()


def test_vertex_seq_is_dense_from_zero(geometry: sqlite3.Connection) -> None:
    gaps = geometry.execute(
        "SELECT dayhike_id, MIN(seq), MAX(seq), COUNT(*) FROM vertices GROUP BY dayhike_id "
        "HAVING MIN(seq) != 0 OR MAX(seq) + 1 != COUNT(*) LIMIT 20"
    ).fetchall()

    assert gaps == []


def test_geometry_meta_has_the_osm_worldcover_and_chelsa_attributions(geometry: sqlite3.Connection) -> None:
    from dayhike.climate import CHELSA_ATTRIBUTION
    from dayhike.landcover import WORLDCOVER_ATTRIBUTION

    meta = dict(geometry.execute("SELECT key, value FROM geometry_meta"))

    assert meta["attribution"] == "© OpenStreetMap contributors"
    assert meta["worldcover_attribution"] == WORLDCOVER_ATTRIBUTION
    assert meta["chelsa_attribution"] == CHELSA_ATTRIBUTION
    assert "worldclim_attribution" not in meta
