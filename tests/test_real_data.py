"""Checks on the real local data under data/, not on the OSM fixture.

Run after an ingest, the three sample commands and notebook 05:

    DAYHIKE_REAL_DATA=1 uv run pytest tests/test_real_data.py

Skipped without DAYHIKE_REAL_DATA=1. The baseline checks compare with
data/baseline/ (catalog.sqlite, catalog.geometry.sqlite and
catalog.similarity_facts.pkl copied before the re-ingest that added the
WaterSources other than drinking water) and are skipped without it.
DAYHIKE_REAL_CATALOG points the checks at another Catalog; the notebook check
always reads data/catalog.sqlite, as the notebook does.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

import numpy as np
import osmium
import pandas as pd
import pytest

from dayhike import geometry_store_path_for
from dayhike.osm import amenity_kind
from dayhike.similarity import similarity_facts_path_for

REPO = Path(__file__).resolve().parents[1]
CATALOG = Path(os.environ.get("DAYHIKE_REAL_CATALOG", REPO / "data" / "catalog.sqlite"))
GEOMETRY = geometry_store_path_for(CATALOG)
FACTS = similarity_facts_path_for(CATALOG)
BASELINE = REPO / "data" / "baseline" / "catalog.sqlite"
BASELINE_GEOMETRY = geometry_store_path_for(BASELINE)
BASELINE_FACTS = similarity_facts_path_for(BASELINE)
EXTRACT = REPO / "data" / "osm" / "europe-latest.catalog.osm.pbf"
REGION_LAT = (42.3, 46.9)  # notebook 05 region
REGION_LON = (13.3, 19.5)
OLD_KINDS = ("hut", "parking", "restaurant_cafe", "drinking_water", "viewpoint", "peak", "shelter")
NEW_WATER_KINDS = ("spring", "water_tap", "water_point")
WATER_COLUMNS = ("water_count", "dry_stretch_m")

pytestmark = pytest.mark.skipif(
    os.environ.get("DAYHIKE_REAL_DATA") != "1", reason="real data: set DAYHIKE_REAL_DATA=1"
)
needs_baseline = pytest.mark.skipif(not BASELINE.is_file(), reason="no data/baseline/")


def _read(path: Path, sql: str, params: tuple = ()) -> pd.DataFrame:
    connection = sqlite3.connect(path)
    try:
        return pd.read_sql_query(sql, connection, params=params)
    finally:
        connection.close()


def _count(path: Path, sql: str) -> int:
    return int(_read(path, sql).iloc[0, 0])


# The data as it is now.


def test_every_catalog_dayhike_has_a_geometry_of_at_least_two_vertices() -> None:
    catalog_ids = _read(CATALOG, "SELECT id FROM dayhikes ORDER BY id")["id"].tolist()
    geometry_ids = _read(GEOMETRY, "SELECT id FROM dayhikes ORDER BY id")["id"].tolist()
    vertices = _read(
        GEOMETRY,
        "SELECT dayhike_id, COUNT(*) AS n FROM vertices GROUP BY dayhike_id ORDER BY dayhike_id",
    )

    assert geometry_ids == catalog_ids
    assert vertices["dayhike_id"].tolist() == catalog_ids
    assert vertices["n"].min() >= 2


def test_geometry_store_holds_every_amenity_kind_with_the_water_sources() -> None:
    kinds = set(_read(GEOMETRY, "SELECT DISTINCT kind FROM amenities")["kind"])

    assert kinds == set(OLD_KINDS + NEW_WATER_KINDS)


def test_no_stored_water_source_is_tagged_not_drinkable() -> None:
    assert (
        _count(
            GEOMETRY,
            "SELECT COUNT(*) FROM amenities WHERE kind IN ('spring', 'water_tap', 'water_point') "
            "AND json_extract(tags, '$.drinking_water') = 'no'",
        )
        == 0
    )


def test_springs_and_taps_tagged_as_drinking_water_stay_drinking_water() -> None:
    as_new_kind = _count(
        GEOMETRY,
        "SELECT COUNT(*) FROM amenities WHERE kind IN ('spring', 'water_tap', 'water_point') "
        "AND json_extract(tags, '$.amenity') = 'drinking_water'",
    )
    as_drinking_water = _count(
        GEOMETRY,
        "SELECT COUNT(*) FROM amenities WHERE kind = 'drinking_water' "
        "AND (json_extract(tags, '$.natural') = 'spring' "
        "OR json_extract(tags, '$.man_made') = 'water_tap')",
    )

    assert as_new_kind == 0
    assert as_drinking_water > 0


@pytest.mark.skipif(not EXTRACT.is_file(), reason="no filtered Europe extract")
def test_every_water_source_node_in_the_extract_is_in_the_geometry_store(
    tmp_path: Path,
) -> None:
    # osmium picks the candidate nodes on its own; only the kind rule is ours.
    candidates = tmp_path / "water.osm.pbf"
    subprocess.run(
        [
            "osmium",
            "tags-filter",
            str(EXTRACT),
            "n/natural=spring",
            "n/man_made=water_tap",
            "n/amenity=water_point",
            "--omit-referenced",
            "-o",
            str(candidates),
            "--no-progress",
        ],
        check=True,
    )
    expected = {}
    for node in osmium.FileProcessor(str(candidates), osmium.osm.NODE):
        kind = amenity_kind(dict(node.tags))
        if kind in NEW_WATER_KINDS and node.location.valid():
            expected[f"n{node.id}"] = kind
    stored = _read(
        GEOMETRY,
        "SELECT osm_key, kind FROM amenities "
        "WHERE kind IN ('spring', 'water_tap', 'water_point') AND osm_key LIKE 'n%'",
    )

    assert expected
    assert dict(zip(stored["osm_key"], stored["kind"])) == expected


def test_facts_table_has_water_facts_for_each_region_dayhike() -> None:
    facts = pd.read_pickle(FACTS)
    region = _read(
        CATALOG,
        "SELECT dayhike_id FROM trailheads WHERE latitude BETWEEN ? AND ? "
        "AND longitude BETWEEN ? AND ? ORDER BY dayhike_id",
        (*REGION_LAT, *REGION_LON),
    )

    assert sorted(facts.index) == region["dayhike_id"].tolist()
    assert facts[list(WATER_COLUMNS)].notna().all().all()
    # DryStretch is part of the walk; with no WaterSource it is the whole walk.
    assert (facts["dry_stretch_m"] <= facts["length_m"] * (1 + 1e-6)).all()
    dry = facts[facts["water_count"] == 0]
    assert np.allclose(dry["dry_stretch_m"], dry["length_m"], rtol=1e-3)


def test_notebook_05a_passes_every_verification_on_the_real_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_demonstration_notebooks import NOTEBOOKS, _notebook_text

    # The notebook falls back to the fixture under pytest; let it read data/.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("MPLBACKEND", "Agg")
    output, _, _ = _notebook_text(NOTEBOOKS / "05a_similarity.ipynb")

    assert "facts read from" in output
    verify = [line.split("\t") for line in output.splitlines() if line.startswith("verify\t")]
    assert verify
    assert [fields for fields in verify if fields[-1] != "PASS"] == []
    assert f"verify_summary\tpass={len(verify)}\tfail=0" in output


# Compared with data/baseline/: the same OSM snapshot, so only the WaterSources
# may differ.


@needs_baseline
@pytest.mark.parametrize(
    "table, key",
    [
        ("dayhikes", "id"),
        ("trailheads", "dayhike_id"),
        ("catalog_meta", "key"),
        ("feature_vectors", "dayhike_id"),
        ("amenity_facts", "dayhike_id, kind"),
        ("climate_months", "dayhike_id, month"),
        ("scaler", "position"),
        ("scaler_month", "month, position"),
    ],
)
def test_reingest_keeps_the_catalog_table(table: str, key: str) -> None:
    sql = f"SELECT * FROM {table} ORDER BY {key}"
    now, before = _read(CATALOG, sql), _read(BASELINE, sql)

    pd.testing.assert_frame_equal(now, before, check_exact=False, rtol=1e-9)


@needs_baseline
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id, name, name_origin, length_m, unconnected_m, difficulty, members "
        "FROM dayhikes ORDER BY id",
        "SELECT dayhike_id, COUNT(*) AS vertices, SUM(node_id) AS node_ids, "
        "SUM(latitude) AS latitude, SUM(longitude) AS longitude, "
        "COUNT(ele) AS heights, SUM(ele) AS ele, "
        "COUNT(landcover) AS landcover, SUM(landcover) AS landcover_codes "
        "FROM vertices GROUP BY dayhike_id ORDER BY dayhike_id",
        "SELECT dayhike_id, COUNT(*) AS ways, SUM(in_chain) AS in_chain "
        "FROM ways GROUP BY dayhike_id ORDER BY dayhike_id",
        "SELECT COUNT(*) AS rows, SUM(temperature_c) AS temperature_c, "
        "SUM(precipitation_mm) AS precipitation_mm, SUM(solar_radiation) AS solar_radiation "
        "FROM vertex_climate",
    ],
    ids=["dayhikes", "vertices", "ways", "vertex_climate"],
)
def test_reingest_keeps_the_geometry_and_its_samples(sql: str) -> None:
    now, before = _read(GEOMETRY, sql), _read(BASELINE_GEOMETRY, sql)

    pd.testing.assert_frame_equal(now, before, check_exact=False, rtol=1e-9)


@needs_baseline
def test_reingest_keeps_every_amenity_and_adds_only_water_sources() -> None:
    connection = sqlite3.connect(GEOMETRY)
    try:
        connection.execute("ATTACH DATABASE ? AS base", (str(BASELINE_GEOMETRY),))
        columns = "osm_key, kind, latitude, longitude"
        lost = connection.execute(
            f"SELECT COUNT(*) FROM (SELECT {columns} FROM base.amenities "
            f"EXCEPT SELECT {columns} FROM main.amenities)"
        ).fetchone()[0]
        added = dict(
            connection.execute(
                f"SELECT kind, COUNT(*) FROM (SELECT {columns} FROM main.amenities "
                f"EXCEPT SELECT {columns} FROM base.amenities) GROUP BY kind"
            )
        )
    finally:
        connection.close()

    assert lost == 0
    assert set(added) == set(NEW_WATER_KINDS)


@needs_baseline
def test_facts_other_than_water_are_unchanged() -> None:
    now, before = pd.read_pickle(FACTS), pd.read_pickle(BASELINE_FACTS)
    plain = [
        column
        for column in before.columns
        if column not in (*WATER_COLUMNS, "geometry", "profile_shape")
    ]

    assert list(now.columns) == list(before.columns)
    assert now.index.equals(before.index)
    pd.testing.assert_frame_equal(
        pd.DataFrame(now[plain]), pd.DataFrame(before[plain]), check_exact=False, rtol=1e-9
    )
    assert now.geometry.geom_equals(before.geometry).all()
    for shape, old_shape in zip(now["profile_shape"], before["profile_shape"]):
        assert (shape is None) == (old_shape is None)
        if shape is not None:
            assert np.allclose(shape, old_shape)


@needs_baseline
def test_water_sources_only_add_water() -> None:
    now, before = pd.read_pickle(FACTS), pd.read_pickle(BASELINE_FACTS)
    unchanged = now["water_count"] == before["water_count"]

    assert (now["water_count"] >= before["water_count"]).all()
    assert (now["dry_stretch_m"] <= before["dry_stretch_m"] + 1e-6).all()
    assert np.allclose(
        now.loc[unchanged, "dry_stretch_m"], before.loc[unchanged, "dry_stretch_m"]
    )
    assert (now["water_count"] == 0).mean() < (before["water_count"] == 0).mean()
