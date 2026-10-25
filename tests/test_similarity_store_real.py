"""The similarity store of a full Catalog: same DayHikes, sound Calibration, refused when stale.

Slow, opt-in (DAYHIKE_REAL_DATA=1); reads DAYHIKE_DATA_DIR (default data/)
after build-similarity.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import numpy as np
import pytest
import rebuild_checks as rc

from dayhike import Catalog, SimilarityStore, StaleSimilarity, geometry_store_path_for, similarity_dir_for
from dayhike.similarity import (
    _CALIBRATION_VERSION,
    _FACTS_VERSION,
    CALIBRATION_KEYS,
    CALIBRATION_PAIRS,
    CALIBRATION_QUANTILES,
    FACT_COLUMNS,
    JOINT_PAIRS,
    LANDCOVER_SHARE_COLUMNS,
    PROFILE_POINTS,
    calibrate_similarity,
    catalog_digest,
)

pytestmark = rc.real_data
CLIMATE_COLUMNS = tuple(
    name for name in FACT_COLUMNS if name.split("_")[0] in ("temperature", "precipitation", "solar")
)
# Largest NaN share per fact column after a Europe-coverage rebuild; columns not
# named: 0. The 2026-09-18 Catalog had: difficulty 0.660, southness 0.097, the
# elevation columns 0.00033 (53 DayHikes off the DEM), surface 0.00016.
NAN_BUDGETS = {
    "difficulty_rank": 0.67,
    "southness": 0.10,
    **{name: 0.001 for name in ("elevation_gain_m", "highest_m", "lowest_m", "climb_grade", "steepest_km_m")},
    **{name: 0.001 for name in ("paved_share", "gravel_share", "natural_share", "rock_share", "tarmac_approach_m")},
    **{name: 0.01 for name in (*LANDCOVER_SHARE_COLUMNS, *CLIMATE_COLUMNS)},
}


@pytest.fixture(scope="module")
def store() -> SimilarityStore:
    return SimilarityStore.open(rc.catalog_path())


@pytest.fixture(scope="module")
def catalog_ids() -> np.ndarray:
    catalog = rc.connect_ro(rc.catalog_path())
    try:
        return np.array([i for (i,) in catalog.execute("SELECT id FROM dayhikes ORDER BY id")])
    finally:
        catalog.close()


def _meta() -> dict:
    return json.loads((similarity_dir_for(rc.catalog_path()) / "meta.json").read_text())


def test_store_ids_equal_catalog_ids_in_order(store: SimilarityStore, catalog_ids: np.ndarray) -> None:
    geometry = rc.connect_ro(geometry_store_path_for(rc.catalog_path()))
    try:
        geometry_ids = [i for (i,) in geometry.execute("SELECT id FROM dayhikes ORDER BY id")]
    finally:
        geometry.close()

    assert np.array_equal(store.ids, catalog_ids)
    assert np.array_equal(store.ids, geometry_ids)


def test_store_shapes_dtypes_and_meta_versions(store: SimilarityStore, catalog_ids: np.ndarray) -> None:
    n = len(catalog_ids)
    meta = _meta()

    assert store.facts.shape == (len(FACT_COLUMNS), n) and store.facts.dtype == np.float32
    assert store.profile.shape == (n, PROFILE_POINTS)
    assert store.quantiles.shape == (len(CALIBRATION_KEYS), CALIBRATION_QUANTILES)
    assert store.pairs.shape == (JOINT_PAIRS, len(CALIBRATION_KEYS))
    assert (meta["facts_version"], meta["calibration_version"]) == (_FACTS_VERSION, _CALIBRATION_VERSION)
    assert meta["fact_columns"] == list(FACT_COLUMNS)
    assert meta["dayhikes"] == n
    assert CALIBRATION_PAIRS * 0.999 < meta["calibration_pairs"] <= CALIBRATION_PAIRS


def test_calibration_quantiles_are_monotone_and_in_unit_interval(store: SimilarityStore) -> None:
    quantiles = np.asarray(store.quantiles)
    pairs = np.asarray(store.pairs)

    assert [CALIBRATION_KEYS[row] for row in np.flatnonzero(np.isnan(quantiles).any(axis=1))] == []
    assert (np.diff(quantiles, axis=1) >= 0).all()
    assert ((quantiles >= 0) & (quantiles <= 1)).all()
    scored = pairs[~np.isnan(pairs)]
    assert ((scored >= 0) & (scored <= 1)).all()


def test_fact_nan_budgets(store: SimilarityStore) -> None:
    shares = {name: float(np.isnan(store.column(name)).mean()) for name in FACT_COLUMNS}
    profile_share = float(np.isnan(np.asarray(store.profile)).any(axis=1).mean())
    print("nan shares", {name: round(share, 4) for name, share in shares.items() if share})

    assert {name: share for name, share in shares.items() if share > NAN_BUDGETS.get(name, 0.0)} == {}
    assert profile_share <= 0.001


def test_calibration_reproduces_from_the_stored_facts(tmp_path: Path) -> None:
    built = similarity_dir_for(rc.catalog_path())
    copy = similarity_dir_for(tmp_path / "catalog.sqlite")
    copy.mkdir()
    for name in ("ids.npy", "facts.npy", "profile.npy", "meta.json"):
        shutil.copyfile(built / name, copy / name)

    calibrate_similarity(tmp_path / "catalog.sqlite")

    for name in ("quantiles.npy", "pairs.npy"):
        assert (copy / name).read_bytes() == (built / name).read_bytes(), name


def test_store_was_built_from_this_catalog() -> None:
    meta_path = similarity_dir_for(rc.catalog_path()) / "meta.json"
    newest = max(
        rc.catalog_path().stat().st_mtime, geometry_store_path_for(rc.catalog_path()).stat().st_mtime
    )

    assert meta_path.stat().st_mtime > newest
    assert _meta()["catalog_digest"] == catalog_digest(rc.catalog_path())
    assert Catalog.open(rc.catalog_path()).similarity.ids.size == _meta()["dayhikes"]


def test_stale_store_is_refused(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    shutil.copyfile(rc.catalog_path(), catalog_path)
    similarity_dir_for(catalog_path).mkdir()
    for path in similarity_dir_for(rc.catalog_path()).iterdir():
        shutil.copyfile(path, similarity_dir_for(catalog_path) / path.name)
    meta_path = similarity_dir_for(catalog_path) / "meta.json"
    meta = json.loads(meta_path.read_text())

    connection = sqlite3.connect(catalog_path)
    connection.execute("UPDATE feature_vectors SET elevation_gain_m = elevation_gain_m + 1 WHERE rowid = 1")
    connection.commit()
    connection.close()
    with pytest.raises(StaleSimilarity, match="build-similarity"):
        Catalog.open(catalog_path).similarity
    meta_path.write_text(json.dumps({**meta, "facts_version": "x"}))
    with pytest.raises(StaleSimilarity, match="build-similarity"):
        SimilarityStore.open(catalog_path)
    meta_path.write_text(json.dumps({**meta, "calibration_version": "x"}))
    with pytest.raises(StaleSimilarity, match="--calibrate-only"):
        SimilarityStore.open(catalog_path)

    connection = sqlite3.connect(catalog_path)
    (gone,) = connection.execute("SELECT MAX(id) FROM dayhikes").fetchone()
    for table in ("climate_months", "amenity_facts", "feature_vectors", "trailheads"):
        connection.execute(f"DELETE FROM {table} WHERE dayhike_id = ?", (gone,))
    connection.execute("DELETE FROM dayhikes WHERE id = ?", (gone,))
    connection.commit()
    connection.close()
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(StaleSimilarity, match="other DayHikes"):
        Catalog.open(catalog_path).similarity
