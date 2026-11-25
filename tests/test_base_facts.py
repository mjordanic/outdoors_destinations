import json
import shutil
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from dayhike.bases import REACHES, base_facts, export_bases
from dayhike.serving import ServingData
from dayhike.similarity import SimilarityStore, similarity_dir_for
from test_bases import _store
from test_export_bases import _build, _valhalla
from test_export_bases import _export as _bases_fixture
from test_highways import _stop
from test_serving import catalog_path, export  # noqa: F401 (fixtures)

# Six hikes in four HikeGroups: group 0 holds hikes 0 and 1, group 1 hike 2, group 2
# hikes 3 and 4, group 3 hike 5. Hike 4 is a Twin of hike 3, so it is counted once, as hike 3.
# Hike h is 10 * h + month degrees warm in each month; hike 2's temperature is unknown.
GROUP_OF_HIKE = np.array([0, 0, 1, 2, 2, 3])
TWINS = np.array([0, 1, 2, 3, 3, 5])


def _month_store(tmp_path: Path) -> SimilarityStore:
    hikes = len(GROUP_OF_HIKE)
    columns = {
        f"temperature_c_{month:02d}": np.array([10.0 * h + month for h in range(hikes)]) for month in range(1, 13)
    }
    for month in range(1, 13):
        columns[f"temperature_c_{month:02d}"][2] = np.nan
    columns["temperature_c_01"][4] = 99.0  # the Twin's own value is never read
    _store(tmp_path, columns)
    directory = similarity_dir_for(tmp_path / "catalog.sqlite")
    np.save(directory / "twins.npy", TWINS)
    return SimilarityStore.open_dir(directory)


def _places(tmp_path: Path, rows: list[tuple[int, int | None]]) -> Path:
    path = tmp_path / "places.sqlite"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE places (id INTEGER PRIMARY KEY, name TEXT, country TEXT, population INTEGER, lat REAL, lon REAL)"
    )
    db.executemany("INSERT INTO places VALUES (?, 'P', 'GB', ?, 0, 0)", rows)
    db.commit()
    db.close()
    return path


def _reach(groups: list[int], minutes: list[int]) -> tuple[np.ndarray, np.ndarray]:
    return np.array(groups, dtype=np.int32), np.array(minutes, dtype=np.uint8)


def test_month_temperatures_are_the_median_of_the_hikes_in_each_reach(tmp_path: Path) -> None:
    store = _month_store(tmp_path)
    places = _places(tmp_path, [(1, 4_658), (2, 500)])
    reached = [_reach([0, 1, 2, 3], [10, 15, 25, 50]), _reach([3], [16])]

    facts = base_facts(store, places, np.array([1, 2]), reached, GROUP_OF_HIKE)

    assert REACHES == (15, 30, 60)
    months = np.arange(1, 13)
    assert facts.month_temperatures.shape == (2, 3, 12)
    # Within 15 min: hikes 0 and 1 (hike 2 has no temperature); 30 min adds hike 3 (its Twin
    # once); 60 min adds hike 5.
    np.testing.assert_allclose(facts.month_temperatures[0, 0], 5.0 + months)
    np.testing.assert_allclose(facts.month_temperatures[0, 1], 10.0 + months)
    np.testing.assert_allclose(facts.month_temperatures[0, 2], 20.0 + months)
    # The second Base has no hikes within 15 minutes, hike 5 from 30.
    assert np.isnan(facts.month_temperatures[1, 0]).all()
    np.testing.assert_allclose(facts.month_temperatures[1, 1], 50.0 + months)
    np.testing.assert_allclose(facts.month_temperatures[1, 2], 50.0 + months)


def test_a_missing_population_stays_missing(tmp_path: Path) -> None:
    store = _month_store(tmp_path)
    places = _places(tmp_path, [(1, 4_658), (2, 0), (3, None)])  # GeoNames writes 0 for unknown
    reached = [_reach([0], [5])] * 4

    facts = base_facts(store, places, np.array([1, 2, 3, 4]), reached, GROUP_OF_HIKE)

    assert facts.population[0] == 4_658
    assert np.isnan(facts.population[1:]).all()


def test_export_bases_writes_the_base_facts_and_versions(tmp_path: Path) -> None:
    out = _bases_fixture(tmp_path)

    _build(out)

    bases = out / "bases"
    np.testing.assert_array_equal(np.load(bases / "population.npy"), [4_658, 500])
    assert np.load(bases / "month_temperatures.npy").shape == (2, len(REACHES), 12)
    meta = json.loads((bases / "meta.json").read_text())
    store_meta = json.loads((out / "similarity" / "meta.json").read_text())
    assert meta["format"] == 2
    assert meta["facts_version"] == store_meta["facts_version"]
    assert {"version", "hike_types_version", "dayhikes"} <= meta.keys()


def _with_bases(export: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:  # noqa: F811
    """A copy of the serving fixture export with one Base of 1,000 people among its few hikes."""
    out = tmp_path / "web-serving"
    shutil.copytree(export, out)
    lat, lon = np.load(out / "trailheads.npy").reshape(-1, 2).mean(axis=0)
    db = sqlite3.connect(_places(out, [(1, 1_000)]))
    db.execute("UPDATE places SET lat = ?, lon = ?", (float(lat), float(lon)))
    db.commit()
    db.close()
    monkeypatch.setattr("dayhike.bases.MIN_HIKE_GROUPS", 1)
    server, url, _ = _valhalla()
    try:
        export_bases(out, url)
    finally:
        _stop(server)
    return out


def test_serving_data_opens_the_bases_folder_when_present(
    export: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert ServingData(export).bases is None

    data = ServingData(_with_bases(export, tmp_path, monkeypatch))

    meta = json.loads((data.directory / "bases" / "meta.json").read_text())
    assert data.bases is not None
    assert data.bases.meta == meta
    assert data.bases.place_ids.tolist() == [1]
    assert data.bases.population.tolist() == [1_000]
    assert data.bases.month_temperatures.shape == (1, len(REACHES), 12)
    assert len(data.bases.group_types) == meta["hike_groups"]
    assert data.bases.type_table.shape[0] == data.bases.type_table.shape[1]


@pytest.mark.parametrize("stale", ["dayhikes", "facts_version", "format"])
def test_serving_data_leaves_out_bases_made_for_other_hikes(
    export: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stale: str,
) -> None:
    out = _with_bases(export, tmp_path, monkeypatch)
    meta_path = out / "bases" / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta[stale] = 0 if stale == "format" else "other"
    meta_path.write_text(json.dumps(meta))

    assert ServingData(out).bases is None


def test_serving_data_leaves_out_bases_made_from_earlier_catalog_values(
    export: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = _with_bases(export, tmp_path, monkeypatch)
    assert ServingData(out).bases is not None
    # The same DayHikes' similarity facts rebuilt from re-sampled Catalog values.
    store_meta_path = out / "similarity" / "meta.json"
    store_meta = json.loads(store_meta_path.read_text())
    store_meta["catalog_digest"] = "re-sampled"
    store_meta_path.write_text(json.dumps(store_meta))

    assert ServingData(out).bases is None
