import json
from pathlib import Path

import numpy as np
import pytest

from dayhike.bases import hike_types
from dayhike.similarity import (
    _FACTS_VERSION,
    FACT_COLUMNS,
    LANDCOVER_SHARE_COLUMNS,
    SimilarityStore,
    calibrate_similarity,
    similarity_dir_for,
)

# Three kinds of hiking, ten hikes each, a few per cent apart within a kind: short low
# forest walks, long high climbs on bare rock, and lake walks in grass. The facts are
# length, climb, highest point, temperature, lake or sea share, then the land cover.
FACTS = ("length_m", "elevation_gain_m", "highest_m", "temperature_c", "waterside_share")
KINDS = (
    ((4_000, 150, 600, 12, 0.0), "tree_share"),
    ((18_000, 1_400, 2_800, 2, 0.0), "bare_share"),
    ((8_000, 300, 1_200, 9, 0.6), "grass_share"),
)
PER_KIND = 10


def _columns() -> dict[str, np.ndarray]:
    """Core fact columns of ``PER_KIND`` hikes of each kind, kind after kind."""
    rng = np.random.default_rng(7)
    rows = len(KINDS) * PER_KIND
    columns = {name: np.zeros(rows) for name in (*FACTS, *LANDCOVER_SHARE_COLUMNS)}
    for k, (facts, cover) in enumerate(KINDS):
        at = slice(k * PER_KIND, (k + 1) * PER_KIND)
        for name, value in zip(FACTS, facts):
            columns[name][at] = value * rng.uniform(0.97, 1.03, size=PER_KIND)
        columns[cover][at] = 1.0
    return columns


def _store(tmp_path: Path, columns: dict[str, np.ndarray]) -> SimilarityStore:
    """A calibrated similarity directory holding only the given fact columns."""
    rows = len(next(iter(columns.values())))
    catalog_path = tmp_path / "catalog.sqlite"
    directory = similarity_dir_for(catalog_path)
    directory.mkdir(parents=True)
    facts = np.full((len(FACT_COLUMNS), rows), np.nan, dtype=np.float32)
    for name, values in columns.items():
        facts[FACT_COLUMNS.index(name)] = values
    profile = np.full((rows, 64), np.nan, dtype=np.float32)
    for name, values in {"ids": np.arange(rows), "facts": facts, "profile": profile, "twins": np.arange(rows)}.items():
        np.save(directory / f"{name}.npy", values)
        if name in ("facts", "profile"):
            np.save(directory / f"{name}_back.npy", values)
    np.save(directory / "way_back.npy", np.zeros((4, rows), dtype=np.float32))
    (directory / "meta.json").write_text(json.dumps({"facts_version": _FACTS_VERSION}))
    calibrate_similarity(catalog_path)
    return SimilarityStore.open_dir(directory)


@pytest.fixture
def store(tmp_path: Path) -> SimilarityStore:
    return _store(tmp_path, _columns())


def test_like_hikes_share_a_hike_type_and_unlike_ones_do_not(store: SimilarityStore) -> None:
    types = hike_types(store, count=3).types

    by_kind = types.reshape(len(KINDS), PER_KIND)
    assert all(len(set(kind)) == 1 for kind in by_kind)
    assert len(set(by_kind[:, 0])) == len(KINDS)


def test_the_same_store_gives_the_same_hike_types_and_version(tmp_path: Path) -> None:
    first = hike_types(_store(tmp_path / "a", _columns()), count=5)
    again = hike_types(_store(tmp_path / "b", _columns()), count=5)

    assert np.array_equal(first.types, again.types)
    assert np.array_equal(first.table, again.table)
    assert first.version == again.version


def test_the_type_table_is_symmetric_with_1_on_the_diagonal(store: SimilarityStore) -> None:
    table = hike_types(store, count=3).table

    assert table.shape == (3, 3)
    assert np.allclose(table, table.T)
    assert np.allclose(np.diag(table), 1.0)
    assert ((table >= 0) & (table < 1)).sum() == 6  # unlike kinds score below a HikeType against itself


def test_a_hike_missing_a_fact_still_joins_its_kind(tmp_path: Path) -> None:
    columns = _columns()
    columns["highest_m"][0] = np.nan  # a forest walk with no heights
    columns["temperature_c"][:] = np.nan  # no climate anywhere, as in a site built without it

    types = hike_types(_store(tmp_path, columns), count=3).types

    assert types[0] == types[1]
    assert len(set(types.reshape(len(KINDS), PER_KIND)[:, 0])) == len(KINDS)


def test_a_store_with_fewer_distinct_hikes_than_hike_types_gives_one_type_per_kind(tmp_path: Path) -> None:
    columns = {name: np.repeat(values[::PER_KIND], PER_KIND) for name, values in _columns().items()}

    found = hike_types(_store(tmp_path, columns))

    assert found.table.shape == (len(KINDS), len(KINDS))
    assert len(set(found.types.reshape(len(KINDS), PER_KIND)[:, 0])) == len(KINDS)
