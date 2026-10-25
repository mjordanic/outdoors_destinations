"""The raw downloads under the data directory are complete and are the recorded ones.

Slow, opt-in (DAYHIKE_REAL_DATA=1); reads DAYHIKE_DATA_DIR (default data/).
The snapshot check also needs DAYHIKE_REFERENCE_DIR/raw-manifest.tsv. README
"Clean rebuild" says how these fit the rebuild.
"""

from __future__ import annotations

import math

import pytest
import rebuild_checks as rc

from dayhike import geometry_store_path_for
from dayhike.ingest import CATALOG_TAG_FILTERS

pytestmark = rc.real_data
EUROPE_PBF = "osm/europe-latest.osm.pbf"


@pytest.fixture(scope="module")
def tiles() -> dict:
    geometry = rc.connect_ro(geometry_store_path_for(rc.catalog_path()))
    try:
        return rc.vertex_tiles(geometry)
    finally:
        geometry.close()


@rc.needs_reference
def test_europe_pbf_is_the_recorded_snapshot() -> None:
    expected = rc.read_manifest(rc.REFERENCE_DIR / "raw-manifest.tsv")
    actual = rc.raw_manifest(rc.DATA_DIR, [EUROPE_PBF], hashed=[EUROPE_PBF])

    assert rc.manifest_diff({EUROPE_PBF: expected[EUROPE_PBF]}, actual) == []
    assert actual[EUROPE_PBF][2] == rc.RECORDED_SNAPSHOT_SHA256


def test_every_vertex_tile_has_a_dem_tile_or_is_a_known_upstream_gap(tiles: dict) -> None:
    present = {path.name.split("_FABDEM", 1)[0] for path in (rc.DATA_DIR / "dem").glob("*_FABDEM_V1-2.tif")}
    needed = {rc.corner_id(south, west) for south, west in tiles}

    assert needed - present == rc.KNOWN_DEM_GAPS


def test_every_vertex_tile_has_a_worldcover_tile(tiles: dict) -> None:
    present = {
        path.name.removeprefix("ESA_WorldCover_10m_2021_v200_").removesuffix("_Map.tif")
        for path in (rc.DATA_DIR / "landcover").glob("ESA_WorldCover_*_Map.tif")
    }
    needed = {rc.corner_id(south // 3 * 3, west // 3 * 3) for south, west in tiles}

    assert sorted(needed - present) == []


def test_climate_rasters_are_complete_and_cover_the_vertex_extent(tiles: dict) -> None:
    import rasterio

    paths = sorted((rc.DATA_DIR / "climate").glob("wc2.1_30s_*.tif"))
    names = {path.name for path in paths}
    assert names == {
        f"wc2.1_30s_{variable}_{month:02d}.tif" for variable in ("tavg", "prec", "srad") for month in range(1, 13)
    }
    south = min(extent[1] for extent in tiles.values())
    north = max(extent[2] for extent in tiles.values())
    west = min(extent[3] for extent in tiles.values())
    east = max(extent[4] for extent in tiles.values())
    transforms = set()
    for path in paths:
        with rasterio.open(path) as dataset:
            bounds = dataset.bounds
            transforms.add(tuple(dataset.transform)[:6])
        assert bounds.left <= west and bounds.right >= east, path.name
        assert bounds.bottom <= south and bounds.top >= north, path.name
    (transform,) = transforms
    cell, _, origin_x, _, minus_cell, origin_y = transform
    assert cell == pytest.approx(1 / 120) and minus_cell == pytest.approx(-1 / 120)
    for origin in (origin_x, origin_y):
        assert math.isclose(origin * 120, round(origin * 120), abs_tol=1e-6), origin


def test_catalog_extract_was_made_with_the_current_filter_list() -> None:
    extract = rc.DATA_DIR / "osm" / "europe-latest.catalog.osm.pbf"
    filters = extract.with_name(extract.name + ".filters")

    assert filters.read_text() == "\n".join(CATALOG_TAG_FILTERS) + "\n"
    assert extract.stat().st_mtime > (rc.DATA_DIR / EUROPE_PBF).stat().st_mtime
