"""The web demo's serving export of a full Catalog holds what the page needs, and no more.

Slow, opt-in (DAYHIKE_REAL_DATA=1); reads DAYHIKE_SERVING_DIR (default
DAYHIKE_DATA_DIR/web-serving), written by ``dayhike export-serving`` from the
Catalog in DAYHIKE_DATA_DIR. The golden rankings come from
DAYHIKE_REFERENCE_DIR/golden/.
"""

from __future__ import annotations

import json
import math
import sqlite3
import zlib

import numpy as np
import pytest
import rebuild_checks as rc
import shapely

from dayhike import SimilarityStore, geometry_store_path_for, similarity_dir_for
from dayhike.catalog import EARTH_RADIUS_M
from dayhike.serving import LINE_TOLERANCE_M, POINT_COLUMNS, ServingData, decode_polyline

pytestmark = rc.real_data
# Measured on the 2026-09-18 Catalog: hikes.sqlite 154 MB, similarity 88 MB,
# snaps 4.5 MB, Trailheads 2.6 MB; the highway tile tar 790 MB.
DATA_BUDGET_BYTES = 300_000_000
TAR_BUDGET_BYTES = 900_000_000
# Encoded polylines keep 5 decimals: up to ~0.8 m of rounding on top of the tolerance.
LINE_SLACK_M = 1.5
LINE_SAMPLE = 2_000
EXPORTED_SUFFIXES = {".npy", ".json", ".sqlite", ".npz", ".tar"}


@pytest.fixture(scope="module")
def data() -> ServingData:
    return ServingData(rc.SERVING_DIR)


def test_export_ids_equal_the_similarity_store(data: ServingData) -> None:
    catalog = rc.connect_ro(rc.catalog_path())
    try:
        ids = [i for (i,) in catalog.execute("SELECT id FROM dayhikes ORDER BY id")]
        trailheads = np.array(
            catalog.execute("SELECT latitude, longitude FROM trailheads ORDER BY dayhike_id").fetchall()
        )
    finally:
        catalog.close()
    store = SimilarityStore.open_for(rc.catalog_path(), ids)

    assert np.array_equal(data.store.ids, store.ids)
    assert np.array_equal(np.asarray(data.trailheads), trailheads)
    assert data.meta["dayhikes"] == len(store.ids)
    for path in similarity_dir_for(rc.catalog_path()).iterdir():
        assert (rc.SERVING_DIR / "similarity" / path.name).read_bytes() == path.read_bytes(), path.name
    with sqlite3.connect(f"file:{rc.SERVING_DIR / 'hikes.sqlite'}?mode=ro&immutable=1", uri=True) as db:
        assert [i for (i,) in db.execute("SELECT id FROM hikes ORDER BY id")] == store.ids.tolist()


def test_export_size_budget() -> None:
    sizes = {path: path.stat().st_size for path in rc.SERVING_DIR.rglob("*") if path.is_file()}
    tar = rc.SERVING_DIR / "valhalla" / "valhalla_tiles.tar"
    print({str(path.relative_to(rc.SERVING_DIR)): size for path, size in sizes.items()})

    assert sum(size for path, size in sizes.items() if path != tar) <= DATA_BUDGET_BYTES
    assert sizes[tar] <= TAR_BUDGET_BYTES


def _metres(lat: np.ndarray, lon: np.ndarray, lat0: float) -> np.ndarray:
    scale = math.cos(math.radians(lat0))
    return np.column_stack([np.radians(lon) * scale, np.radians(lat)]) * EARTH_RADIUS_M


def test_simplified_lines_stay_within_10_m_of_the_geometry(data: ServingData) -> None:
    ids = data.store.ids[:: max(1, len(data.store.ids) // LINE_SAMPLE)][:LINE_SAMPLE].tolist()
    geometry = rc.connect_ro(geometry_store_path_for(rc.catalog_path()))
    far = {}
    try:
        with sqlite3.connect(f"file:{rc.SERVING_DIR / 'hikes.sqlite'}?mode=ro&immutable=1", uri=True) as db:
            for dayhike_id in ids:
                (line,) = db.execute("SELECT line FROM hikes WHERE id = ?", (dayhike_id,)).fetchone()
                vertices = np.array(
                    geometry.execute(
                        "SELECT latitude, longitude FROM vertices WHERE dayhike_id = ? ORDER BY seq",
                        (dayhike_id,),
                    ).fetchall()
                )
                kept = np.array(decode_polyline(line))
                lat0 = float(vertices[:, 0].mean())
                full = shapely.linestrings(_metres(vertices[:, 0], vertices[:, 1], lat0))
                simple = shapely.linestrings(_metres(kept[:, 0], kept[:, 1], lat0))
                distance = shapely.hausdorff_distance(full, simple)
                if distance > LINE_TOLERANCE_M + LINE_SLACK_M:
                    far[dayhike_id] = round(float(distance), 2)
    finally:
        geometry.close()

    assert len(ids) == LINE_SAMPLE
    assert far == {}


def test_export_has_no_raster_no_vertex_climate_no_per_vertex_worldclim(data: ServingData) -> None:
    files = [path for path in rc.SERVING_DIR.rglob("*") if path.is_file()]
    with sqlite3.connect(f"file:{rc.SERVING_DIR / 'hikes.sqlite'}?mode=ro&immutable=1", uri=True) as db:
        tables = rc.schema(db)
        (points,) = db.execute("SELECT points FROM hikes LIMIT 1").fetchone()

    assert {path.suffix for path in files} <= EXPORTED_SUFFIXES
    assert tables == {"hikes": ["id", "name", "name_origin", "sac_scale", "walk_m", "line", "elevation", "points"]}
    assert all(len(point) == len(POINT_COLUMNS) for point in json.loads(zlib.decompress(points)))
    # Climate leaves the export only as the per-DayHike facts of the similarity copy.
    assert data.store.facts.shape[1] == len(data.store.ids)


def test_export_attribution_lists_osm_worldcover_worldclim_fabdem_valhalla(data: ServingData) -> None:
    from dayhike.climate import WORLDCLIM_ATTRIBUTION
    from dayhike.landcover import WORLDCOVER_ATTRIBUTION

    assert data.meta["attribution"] == {
        "osm": "© OpenStreetMap contributors",
        "worldcover": WORLDCOVER_ATTRIBUTION,
        "worldclim": WORLDCLIM_ATTRIBUTION,
    }


@rc.needs_reference
def test_rankings_from_the_export_equal_golden_rankings(data: ServingData) -> None:
    golden: dict[tuple[str, int], list[dict]] = {}
    for row in rc.read_tsv(rc.REFERENCE_DIR / "golden" / "rankings.tsv"):
        golden.setdefault((row["set"], int(row["seed"])), []).append(row)
    sets = rc.golden_weight_sets()

    for (name, seed), rows in golden.items():
        got = data.rank(seed, sets[name], limit=rc.GOLDEN_TOP)
        assert [hike["id"] for hike in got] == [int(row["dayhike_id"]) for row in rows], (name, seed)
        for hike, row in zip(got, rows):
            assert hike["score"] == pytest.approx(float(row["score"]), abs=1e-6)


def test_export_highways_tar_is_the_built_tiles(data: ServingData) -> None:
    built = rc.DATA_DIR / "valhalla" / "highways-eu"
    exported = rc.SERVING_DIR / "valhalla"
    config = json.loads((exported / "valhalla.json").read_text())

    assert (built / "file_hashes.txt").read_text().strip()
    assert rc.tile_counts_in_tar(exported / "valhalla_tiles.tar") == rc.tile_counts_in_tar(
        built / "valhalla_tiles.tar"
    )
    assert (exported / "valhalla_tiles.tar").samefile(built / "valhalla_tiles.tar") or (
        exported / "valhalla_tiles.tar"
    ).read_bytes() == (built / "valhalla_tiles.tar").read_bytes()
    assert config["mjolnir"]["tile_extract"].endswith("/valhalla/valhalla_tiles.tar")
    assert data.meta["drive_time"] is True
