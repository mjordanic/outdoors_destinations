"""USGS 3DEP 1 arc-second beside a world DEM: tiles, edges and the DEM of every hike (ADR-0063)."""

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from dayhike import build_catalog, build_similarity, sample_dem
from dayhike.dem import (
    COPERNICUS,
    FABDEM,
    USGS_3DEP,
    Bounds,
    dem_tiles,
    fetch_dem,
    point_heights,
    sample_elevations,
    sample_elevations_and_sources,
)
from dayhike.serving import ServingData, export_serving, name_dem
from dayhike.similarity import dem_heights
from dayhike.sources import used_source_ids
from dayhike.web import create_app, site_pages

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
EUROPE_BOX = {"west": 14.99, "south": 44.99, "east": 16.01, "north": 46.01}
CELLS = 12  # cells per degree of the test tiles
OVERLAP = 6  # cells a 3DEP tile runs past each 1° edge


def _write_3dep_tile(dem_dir: Path, tile_id: str, z: float, *, nodata_west_half: bool = False) -> Path:
    """A 3DEP-shaped tile: named by its northwest corner, NAD83, ``OVERLAP`` cells past each edge."""
    import rasterio
    from rasterio.transform import from_origin

    from dayhike.dem import _parse_tile

    south, west = _parse_tile(tile_id)
    size = CELLS + 2 * OVERLAP
    cell = 1 / CELLS
    data = np.full((size, size), z, dtype="float32")
    if nodata_west_half:
        data[:, : size // 2] = -999999.0
    path = dem_dir / USGS_3DEP.tile_file(tile_id)
    with rasterio.open(
        path, "w", driver="GTiff", height=size, width=size, count=1, dtype="float32", crs="EPSG:4269",
        transform=from_origin(west - OVERLAP * cell, south + 1 + OVERLAP * cell, cell, cell), nodata=-999999.0,
    ) as dataset:  # fmt: skip
        dataset.write(data, 1)
    return path


def _write_world_tile(path: Path, z: float, **box: float) -> None:
    from test_dem import _write_constant_tif

    _write_constant_tif(path, **box, z=z)


def test_a_3dep_tile_is_named_and_found_by_its_northwest_corner() -> None:
    assert USGS_3DEP.tile_file("N39W106") == "USGS_1_n40w106.tif"
    assert USGS_3DEP.tile_url("N39W106") == (
        "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1/TIFF/current/n40w106/USGS_1_n40w106.tif"
    )
    assert USGS_3DEP.tile_of_file("USGS_1_n40w106.tif") == "N39W106"
    # Alaska runs past 180°W; the Equator tile row is named n01.
    assert USGS_3DEP.tile_of_file(USGS_3DEP.tile_file("N60W150")) == "N60W150"
    assert USGS_3DEP.tile_file("N00W080") == "USGS_1_n01w080.tif"
    assert USGS_3DEP.tile_of_file("USGS_1_n01w080.tif") == "N00W080"
    assert USGS_3DEP.tile_file("S02W170") == "USGS_1_s01w170.tif"
    assert USGS_3DEP.tile_of_file("USGS_1_s01w170.tif") == "S02W170"
    # The world sources keep their names.
    assert FABDEM.tile_of_file("N45E015_FABDEM_V1-2.tif") == "N45E015"
    assert COPERNICUS.tile_of_file("Copernicus_DSM_COG_10_S09_00_W072_00_DEM.tif") == "S09W072"


def test_fetch_dem_downloads_the_3dep_tiles_of_a_box_and_skips_those_usgs_lacks(tmp_path: Path) -> None:
    recorded: list[str] = []

    def retrieve(url: str, filename: str) -> None:
        recorded.append(url)
        if "n41w106" in url:
            raise FileNotFoundError(url)
        Path(filename).write_bytes(b"tif-bytes")

    paths = fetch_dem(
        tmp_path, Bounds(south=39.5, west=-105.9, north=40.5, east=-105.1), source=USGS_3DEP, retrieve=retrieve
    )

    assert sorted(recorded) == [USGS_3DEP.tile_url("N39W106"), USGS_3DEP.tile_url("N40W106")]
    assert paths == [tmp_path / "USGS_1_n40w106.tif"]


def test_fetch_dem_command_takes_3dep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dayhike import dem
    from dayhike.cli import main

    recorded: list[str] = []

    def fake_download(url: str, filename: str) -> None:
        recorded.append(url)
        Path(filename).write_bytes(b"tif-bytes")

    monkeypatch.setattr(dem, "_download", fake_download)

    assert main(["fetch-dem", "--source", "3dep", "--dest", str(tmp_path), "--bounds", "40.2,-105.7,40.3,-105.6"]) == 0
    assert recorded == [USGS_3DEP.tile_url("N40W106")]


def test_a_point_reads_its_own_3dep_tile_not_the_neighbour_s_overlap(tmp_path: Path) -> None:
    # Each tile runs 6 cells into its neighbours; a point reads the tile of its own 1° cell.
    _write_3dep_tile(tmp_path, "N39W106", 2000.0)
    _write_3dep_tile(tmp_path, "N40W106", 3000.0)
    _write_3dep_tile(tmp_path, "N39W105", 1500.0)
    points = [
        (39.999, -105.5),  # just south of 40 °N: its own tile, though the northern one covers it too
        (40.0, -105.5),  # on the edge: the tile it is the southwest corner of
        (40.001, -105.5),
        (39.5, -105.0001),  # just west of 105 °W
        (39.5, -105.0),  # on it: the eastern tile
        (39.0, -106.0),  # the tile's own southwest corner
        (38.5, -105.5),  # south of every tile, inside the overlap of none it is bucketed to
    ]

    expected = [2000.0, 3000.0, 3000.0, 2000.0, 1500.0, 2000.0, None]
    assert sample_elevations(tmp_path, points) == expected
    heights = point_heights(tmp_path)
    assert heights is not None and heights(points) == expected
    lat, lon = np.array(points).T
    assert np.array_equal(dem_heights(tmp_path, lat, lon), np.array([np.nan if v is None else v for v in expected]), equal_nan=True)


def test_3dep_nodata_reads_as_uncovered(tmp_path: Path) -> None:
    _write_3dep_tile(tmp_path, "N39W106", 2000.0, nodata_west_half=True)

    assert sample_elevations(tmp_path, [(39.5, -105.9), (39.5, -105.1)]) == [None, 2000.0]


def test_3dep_nodata_reads_the_world_tile_of_the_point(tmp_path: Path) -> None:
    # A 3DEP tile reaching past the border holds nodata there; the world tile covers it.
    _write_3dep_tile(tmp_path, "N39W106", 2000.0, nodata_west_half=True)
    _write_world_tile(tmp_path / "N39W106_FABDEM_V1-2.tif", 2010.0, west=-106.01, south=38.99, east=-104.99, north=40.01)
    points = [(39.5, -105.9), (39.5, -105.1)]

    heights, sources = sample_elevations_and_sources(tmp_path, points)
    assert heights == [2010.0, 2000.0]
    assert sources == [FABDEM, USGS_3DEP]
    point = point_heights(tmp_path)
    assert point is not None and point(points) == [2010.0, 2000.0]
    lat, lon = np.array(points).T
    assert dem_heights(tmp_path, lat, lon).tolist() == [2010.0, 2000.0]


def test_3dep_is_read_where_it_has_a_tile_and_the_world_source_elsewhere(tmp_path: Path) -> None:
    _write_3dep_tile(tmp_path, "N39W106", 2000.0)
    _write_world_tile(tmp_path / "Copernicus_DSM_COG_10_N39_00_W106_00_DEM.tif", 2010.0, west=-106.01, south=38.99, east=-104.99, north=40.01)
    _write_world_tile(tmp_path / "Copernicus_DSM_COG_10_N40_00_W106_00_DEM.tif", 3010.0, west=-106.01, south=39.99, east=-104.99, north=41.01)
    _write_world_tile(tmp_path / "Copernicus_DSM_COG_10_N45_00_E015_00_DEM.tif", 987.0, **EUROPE_BOX)

    assert {tile: [source for source, _ in files] for tile, files in dem_tiles(tmp_path).items()} == {
        "N39W106": [USGS_3DEP, COPERNICUS],
        "N40W106": [COPERNICUS],
        "N45E015": [COPERNICUS],
    }
    heights, sources = sample_elevations_and_sources(tmp_path, [(39.5, -105.5), (40.5, -105.5), (45.2, 15.1), (0.0, 0.0)])
    assert heights == [2000.0, 3010.0, 987.0, None]
    assert sources == [USGS_3DEP, COPERNICUS, COPERNICUS, None]


def test_two_world_sources_still_do_not_mix_beside_3dep(tmp_path: Path) -> None:
    _write_3dep_tile(tmp_path, "N39W106", 2000.0)
    _write_world_tile(tmp_path / "N45E015_FABDEM_V1-2.tif", 1.0, **EUROPE_BOX)
    _write_world_tile(tmp_path / "Copernicus_DSM_COG_10_N45_00_E015_00_DEM.tif", 1.0, **EUROPE_BOX)

    with pytest.raises(ValueError, match="FABDEM and Copernicus GLO-30"):
        sample_elevations(tmp_path, [(39.5, -105.5)])


# ---------- a two-region build: Europe on FABDEM, the US on 3DEP ----------


def _two_region_fixture(root: Path) -> Path:
    """The catalog fixture with Valley Path moved from Croatia to Colorado."""
    text = FIXTURE.read_text().replace(
        '<node id="10" lat="45.2000000000" lon="15.1000000000"', '<node id="10" lat="40.2000000000" lon="-105.6000000000"'
    ).replace('<node id="11" lat="45.2449660803" lon="15.1000000000"', '<node id="11" lat="40.2449660803" lon="-105.6000000000"')
    assert "-105.6" in text
    path = root / "two_regions.osm"
    path.write_text(text)
    return path


def _sample_trailgraph(catalog_path: Path, dem_dir: Path) -> None:
    from dayhike.trailgraph import sample_trailgraph

    missing = catalog_path.parent / "missing"
    sample_trailgraph(catalog_path, dem_dir=dem_dir, landcover_dir=missing, climate_dir=missing)


@pytest.fixture(scope="module")
def two_regions(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("two_regions")
    catalog_path = root / "catalog.sqlite"
    build_catalog(_two_region_fixture(root), catalog_path)
    dem_dir = root / "dem"
    dem_dir.mkdir()
    _write_world_tile(dem_dir / "N45E015_FABDEM_V1-2.tif", 1400.0, **EUROPE_BOX)
    _write_world_tile(dem_dir / "N47E018_FABDEM_V1-2.tif", 1400.0, west=17.99, south=46.99, east=19.01, north=48.01)
    _write_3dep_tile(dem_dir, "N40W106", 3000.0)
    assert sample_dem(catalog_path, dem_dir) > 0
    _sample_trailgraph(catalog_path, dem_dir)
    build_similarity(catalog_path, dem_dir)
    return catalog_path


def _dem_by_name(catalog_path: Path) -> dict[str, str | None]:
    with sqlite3.connect(catalog_path) as catalog:
        return dict(catalog.execute("SELECT name, dem_source FROM dayhikes"))


def test_a_two_region_build_records_the_dem_of_every_hike(two_regions: Path) -> None:
    dems = _dem_by_name(two_regions)

    assert dems["Valley Path"] == "usgs-3dep"
    assert dems["Ridge Walk"] == "fabdem"
    assert set(dems.values()) <= {"fabdem", "usgs-3dep"}
    assert {"fabdem", "usgs-3dep"} <= used_source_ids()
    with sqlite3.connect(two_regions) as catalog:
        (valley_low,) = catalog.execute(
            "SELECT f.elevation_min_m FROM feature_vectors f JOIN dayhikes d ON d.id = f.dayhike_id WHERE d.name = 'Valley Path'"
        ).fetchone()
    assert valley_low == pytest.approx(3000.0)


def test_a_hike_read_from_two_dems_records_both_in_one_order() -> None:
    from dayhike.dem import dem_source_value

    assert dem_source_value({"usgs-3dep", "fabdem"}) == "fabdem,usgs-3dep"
    assert dem_source_value({"copernicus-glo30"}) == "copernicus-glo30"


def test_the_export_of_a_two_region_build_credits_each_dem(two_regions: Path) -> None:
    export = export_serving(two_regions, two_regions.parent / "web-serving")
    meta = json.loads((export / "meta.json").read_text())

    assert "Credit: U.S. Geological Survey" in meta["attribution"]["usgs_3dep"]
    assert "FABDEM" in meta["attribution"]["fabdem"]
    assert "copernicus" not in meta["attribution"]
    with sqlite3.connect(export / "hikes.sqlite") as hikes:
        assert "dem_source" not in {row[1] for row in hikes.execute("PRAGMA table_info(hikes)")}

    client = TestClient(create_app(ServingData(export), None))
    valley = next(row for row in client.get("/api/search", params={"q": "valley"}).json()["results"])
    assert "dem_source" not in client.get(f"/api/hikes/{valley['id']}").json()
    credits = client.get("/credits.html").text
    assert "FABDEM V1-2" in credits and "Credit: U.S. Geological Survey" in credits
    sources = {measure["name"]: measure["source"] for measure in client.get("/api/measures").json()["measures"]}
    assert sources["highest"] == "FABDEM or USGS 3DEP elevation along the line, or a summited peak's height as mapped in OpenStreetMap (ele)."


def _one_dem_build(tmp_path: Path) -> Path:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    dem_dir = tmp_path / "dem"
    dem_dir.mkdir()
    _write_world_tile(dem_dir / "N45E015_FABDEM_V1-2.tif", 1400.0, **EUROPE_BOX)
    sample_dem(catalog_path, dem_dir)
    _sample_trailgraph(catalog_path, dem_dir)
    build_similarity(catalog_path, dem_dir)
    return catalog_path


def test_a_build_on_one_dem_credits_only_it(tmp_path: Path) -> None:
    catalog_path = _one_dem_build(tmp_path)

    export = export_serving(catalog_path, tmp_path / "web-serving")

    assert set(_dem_by_name(catalog_path).values()) <= {"fabdem", None}
    assert list(json.loads((export / "meta.json").read_text())["attribution"])[:2] == ["osm", "fabdem"]


def test_only_hikes_read_from_copernicus_are_drawn_smoothed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dayhike import serving
    from dayhike.catalog import geometry_store_path_for

    catalog_path = _one_dem_build(tmp_path)
    # Copernicus tiles in the DEM folder that no hike read still credit it in the GeometryStore.
    with sqlite3.connect(geometry_store_path_for(catalog_path)) as geo:
        geo.execute("INSERT OR REPLACE INTO geometry_meta (key, value) VALUES ('copernicus_attribution', 'Copernicus')")
    smoothed: list[bool] = []
    drawn = serving.drawn_profile
    monkeypatch.setattr(serving, "drawn_profile", lambda *walk, smooth: smoothed.append(smooth) or drawn(*walk, smooth=smooth))

    export = export_serving(catalog_path, tmp_path / "web-serving")

    assert smoothed and not any(smoothed)
    assert "copernicus" not in json.loads((export / "meta.json").read_text())["attribution"]

    with sqlite3.connect(catalog_path) as catalog:
        catalog.execute("UPDATE dayhikes SET dem_source = 'copernicus-glo30' WHERE name = 'Ridge Walk'")
        ids = [row[0] for row in catalog.execute("SELECT id FROM dayhikes WHERE dem_source IS NOT NULL ORDER BY id")]
        (ridge,) = catalog.execute("SELECT id FROM dayhikes WHERE name = 'Ridge Walk'").fetchone()
    smoothed.clear()
    export_serving(catalog_path, tmp_path / "web-serving")

    assert len(smoothed) == len(ids)
    assert [hike for hike, smooth in zip(ids, smoothed) if smooth] == [ridge]


def test_the_pages_and_texts_credit_3dep_beside_a_world_dem_or_alone() -> None:
    beside = site_pages(None, usgs_3dep=True)
    alone = site_pages(None, fabdem=False, usgs_3dep=True)
    default = site_pages(None)

    for name in ("index", "terms", "credits"):
        assert "FABDEM" in beside[name] and "USGS" in beside[name]
        assert "FABDEM" not in alone[name] and "USGS" in alone[name]
        assert "USGS 3DEP" not in default[name]
    assert "In the United States they are computed from USGS 3DEP" in beside["terms"]
    assert "The elevation profiles and the values derived from elevation (heights, gain, profile shape, steepness, " \
        "slope aspect) are computed from USGS 3DEP 1 arc-second" in alone["terms"]
    assert "they are computed" not in alone["terms"]
    with_copernicus = site_pages(None, copernicus=True, usgs_3dep=True)["credits"]
    assert "Copernicus DEM GLO-30" in with_copernicus and "Credit: U.S. Geological Survey" in with_copernicus

    text = "FABDEM, a 30 m elevation map with forests and buildings removed: heights (from FABDEM)."
    assert name_dem(text, {"fabdem": "x"}) == text
    assert name_dem(text, {"usgs_3dep": "x"}) == (
        "USGS 3DEP 1″, a 30 m bare-earth elevation map of the United States: heights (from USGS 3DEP)."
    )
    assert name_dem(text, {"copernicus": "x", "usgs_3dep": "x"}) == (
        "Copernicus GLO-30, a 30 m elevation map of the surface, tree tops and roofs included, or USGS 3DEP 1″, "
        "a 30 m bare-earth elevation map of the United States: heights (from Copernicus GLO-30 or USGS 3DEP)."
    )
