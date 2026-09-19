import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from dayhike import (
    AmbiguousSeedName,
    Catalog,
    UnknownSeedName,
    build_catalog,
    feature_matrix,
    rank_dayhikes,
    rank_scaled_rows,
    scale_matrix,
)

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
RIDGE_WALK_ID = 1001
VALLEY_PATH_ID = 1002


def test_one_seed_returns_the_other_in_band_dayhike_and_omits_the_seed(
    tmp_path: Path,
) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")

    ranking = rank_dayhikes(catalog, ["Ridge Walk"])

    names = [item.dayhike.name for item in ranking.items]
    assert VALLEY_PATH_ID in {item.dayhike.id for item in ranking.items}
    assert RIDGE_WALK_ID not in {item.dayhike.id for item in ranking.items}
    assert "Ridge Walk" not in names
    assert ranking.items
    assert -1.0 <= ranking.items[0].score <= 1.0


def test_scaler_persists_and_ranking_matches_after_catalog_open(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    built = build_catalog(FIXTURE, path)
    opened = Catalog.open(path)

    first = rank_dayhikes(built, ["Ridge Walk"])
    second = rank_dayhikes(opened, ["Ridge Walk"])

    assert opened.scaler == built.scaler
    assert [item.dayhike.id for item in first.items] == [item.dayhike.id for item in second.items]
    assert [item.score for item in first.items] == [item.score for item in second.items]


def test_two_seeds_score_is_max_not_mean(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    ridge = next(hike for hike in catalog.dayhikes if hike.id == RIDGE_WALK_ID)
    valley = next(hike for hike in catalog.dayhikes if hike.id == VALLEY_PATH_ID)
    near_ridge = replace(ridge, id=9001, name="Near Ridge")
    midpoint = replace(
        valley,
        id=9002,
        name="Mid Path",
        length_m=(ridge.length_m + valley.length_m) / 2,
        feature_vector=replace(
            valley.feature_vector,
            length_m=(ridge.feature_vector.length_m + valley.feature_vector.length_m) / 2,
            elevation_min_m=ridge.feature_vector.elevation_min_m,
            elevation_max_m=ridge.feature_vector.elevation_max_m,
            elevation_gain_m=ridge.feature_vector.elevation_gain_m,
            difficulty_ordinal=1,
        ),
    )
    catalog = replace(catalog, dayhikes=(ridge, valley, near_ridge, midpoint))

    only_ridge = _scores(rank_dayhikes(catalog, ["Ridge Walk"]))
    only_valley = _scores(rank_dayhikes(catalog, ["Valley Path"]))
    both = rank_dayhikes(catalog, ["Ridge Walk", "Valley Path"])
    both_scores = _scores(both)

    assert [item.dayhike.name for item in both.items][0] == "Near Ridge"
    assert both_scores["Near Ridge"] > both_scores["Mid Path"]
    for name in ("Near Ridge", "Mid Path"):
        expected_max = max(only_ridge[name], only_valley[name])
        expected_mean = (only_ridge[name] + only_valley[name]) / 2
        assert both_scores[name] == pytest.approx(expected_max)
        assert both_scores[name] != pytest.approx(expected_mean)


def test_ranking_is_deterministic_across_two_build_and_rank_runs(tmp_path: Path) -> None:
    first = rank_dayhikes(build_catalog(FIXTURE, tmp_path / "a.sqlite"), ["Ridge Walk"])
    second = rank_dayhikes(build_catalog(FIXTURE, tmp_path / "b.sqlite"), ["Ridge Walk"])

    assert [(item.dayhike.id, item.score) for item in first.items] == [
        (item.dayhike.id, item.score) for item in second.items
    ]


def test_rank_scaled_rows_matches_rank_dayhikes(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    ranking = rank_dayhikes(catalog, ["Ridge Walk"])
    scaled = scale_matrix(feature_matrix(catalog.dayhikes), catalog.scaler)
    rows = rank_scaled_rows(
        [hike.id for hike in catalog.dayhikes],
        [hike.name for hike in catalog.dayhikes],
        scaled,
        {RIDGE_WALK_ID},
    )

    assert [(item.dayhike.id, item.dayhike.name, item.score) for item in ranking.items] == [
        row for row in rows
    ]


def test_unknown_seed_still_fails_via_name_resolution(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")

    with pytest.raises(UnknownSeedName, match="Unknown Ridge"):
        rank_dayhikes(catalog, ["Unknown Ridge"])


def test_cli_prints_id_name_and_score(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, path)

    result = subprocess.run(
        [sys.executable, "-m", "dayhike", "rank", "--catalog", str(path), "Ridge Walk"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    ids = [line.split("\t")[0] for line in lines]
    names = [line.split("\t")[1] for line in lines]
    assert str(RIDGE_WALK_ID) not in ids
    assert str(VALLEY_PATH_ID) in ids
    assert "Valley Path" in names
    assert all(-1.0 <= float(line.split("\t")[2]) <= 1.0 for line in lines)


def test_ambiguous_seed_still_fails_via_name_resolution(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    catalog = replace(
        catalog,
        dayhikes=tuple(replace(hike, name="Shared Path") for hike in catalog.dayhikes),
    )

    with pytest.raises(AmbiguousSeedName, match="Shared Path"):
        rank_dayhikes(catalog, ["Shared Path"])


def _scores(ranking) -> dict[str, float]:
    return {item.dayhike.name: item.score for item in ranking.items}
