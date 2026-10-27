import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from dayhike import (
    MEASURE_NAMES,
    AmbiguousSeedName,
    Catalog,
    StaleSimilarity,
    UnknownSeedName,
    build_catalog,
    build_similarity,
    rank_dayhikes,
)
from dayhike.similarity import (
    CORE_MEASURES,
    DEFAULT_WEIGHTS,
    FACT_COLUMNS,
    calibrate_similarity,
    score_rows,
    similarity_dir_for,
)

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
RIDGE_WALK_ID = 1001
VALLEY_PATH_ID = 1002


def _catalog(tmp_path: Path, lengths: dict[int, float] | None = None) -> Catalog:
    """The fixture Catalog with its similarity facts, optionally other lengths."""
    path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    if lengths:
        connection = sqlite3.connect(path)
        connection.executemany(
            "UPDATE feature_vectors SET length_m = ? WHERE dayhike_id = ?",
            [(length, dayhike_id) for dayhike_id, length in lengths.items()],
        )
        connection.commit()
        connection.close()
    build_similarity(path, tmp_path / "dem")
    return Catalog.open(path)


# Ridge Walk 10 km; the others at 9, 5 and 2 km.
LENGTHS = {1002: 5_000.0, 1003: 9_000.0, 1007: 2_000.0}


def test_one_seed_ranks_every_other_dayhike_and_omits_the_seed(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)

    ranking = rank_dayhikes(catalog, ["Ridge Walk"])

    ids = [item.dayhike.id for item in ranking.items]
    assert sorted(ids) == sorted(hike.id for hike in catalog.dayhikes if hike.id != RIDGE_WALK_ID)
    assert all(0.0 <= item.score <= 1.0 for item in ranking.items)
    assert [item.score for item in ranking.items] == sorted(
        (item.score for item in ranking.items), reverse=True
    )


def test_one_weighted_measure_orders_by_that_measure(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, LENGTHS)

    ranking = rank_dayhikes(catalog, ["Ridge Walk"], {"length": 100})

    assert [item.dayhike.id for item in ranking.items] == [1003, 1002, 1007]


def test_a_measure_at_weight_0_is_excluded(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, LENGTHS)

    only_length = rank_dayhikes(catalog, ["Ridge Walk"], {"length": 100})
    with_zero = rank_dayhikes(catalog, ["Ridge Walk"], {"length": 100, "hut_along": 0})

    assert with_zero == only_length


def test_weights_shift_the_ranking_between_measures(tmp_path: Path) -> None:
    # PS-1 (1007) is the shortest, but it shares Ridge Walk's Peak.
    catalog = _catalog(tmp_path, LENGTHS)
    facts_path = similarity_dir_for(catalog.path) / "facts.npy"
    facts = np.load(facts_path)
    facts[FACT_COLUMNS.index("peak_summited"), 3] = 1.0
    np.save(facts_path, facts)
    calibrate_similarity(catalog.path)
    catalog = Catalog.open(catalog.path)

    # One way: the facts changed above are the one-way ones.
    by_length = rank_dayhikes(catalog, ["Ridge Walk"], {"length": 100, "peak_summited": 10}, return_to_start=False)
    by_peak = rank_dayhikes(catalog, ["Ridge Walk"], {"length": 10, "peak_summited": 100}, return_to_start=False)

    assert by_length.items[0].dayhike.id == 1003
    assert by_peak.items[0].dayhike.id == 1007


def test_allowed_ids_restrict_the_candidates(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, LENGTHS)

    ranking = rank_dayhikes(
        catalog, ["Ridge Walk"], {"length": 100}, allowed_ids={1002, 1007, RIDGE_WALK_ID}
    )

    assert [item.dayhike.id for item in ranking.items] == [1002, 1007]
    full = rank_dayhikes(catalog, ["Ridge Walk"], {"length": 100})
    full = {item.dayhike.id: item.score for item in full.items}
    assert [item.score for item in ranking.items] == [full[1002], full[1007]]


def test_limit_keeps_the_best_items_with_the_same_scores(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, LENGTHS)

    full = rank_dayhikes(catalog, ["Ridge Walk"], {"length": 100})
    best = rank_dayhikes(catalog, ["Ridge Walk"], {"length": 100}, limit=2)

    assert best.items == full.items[:2]


def test_several_seeds_score_the_best_seed_not_the_mean(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, LENGTHS)
    store = catalog.similarity
    weights = {"length": 100.0}
    rows = np.array([2, 3])  # relation:1003 and PS-1

    ridge, _ = score_rows(store, [0], rows, weights)
    valley, _ = score_rows(store, [1], rows, weights)
    both, _ = score_rows(store, [0, 1], rows, weights)

    assert both == pytest.approx(np.maximum(ridge, valley))
    assert both != pytest.approx((ridge + valley) / 2)
    ranking = rank_dayhikes(catalog, ["Ridge Walk", "Valley Path"], weights, return_to_start=False)
    scores = {item.dayhike.id: item.score for item in ranking.items}
    assert scores[1003] == pytest.approx(both[0])


def test_score_is_the_similarity(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, LENGTHS)
    weights = dict.fromkeys(MEASURE_NAMES, 100.0)

    for return_to_start in (True, False):
        ranking = rank_dayhikes(catalog, ["Ridge Walk"], weights, return_to_start=return_to_start)

        store = catalog.similarity.mode(return_to_start)
        rows = np.searchsorted(store.ids, [item.dayhike.id for item in ranking.items])
        similarity, _ = score_rows(store, [0], rows, weights)
        assert [item.score for item in ranking.items] == pytest.approx(similarity.tolist())


def test_no_weights_weigh_the_core_measures(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, LENGTHS)

    assert {name for name, weight in DEFAULT_WEIGHTS.items() if weight > 0} == set(CORE_MEASURES)
    assert rank_dayhikes(catalog, ["Ridge Walk"]) == rank_dayhikes(
        catalog, ["Ridge Walk"], dict.fromkeys(CORE_MEASURES, 100)
    )


def test_only_the_best_of_twins_is_ranked_and_no_twin_of_a_seed(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, LENGTHS)
    twins_path = similarity_dir_for(catalog.path) / "twins.npy"
    # Valley Path (row 1) is Ridge Walk's Twin; PS-1 (row 3) relation:1003's.
    np.save(twins_path, np.array([0, 0, 2, 2]))
    catalog = Catalog.open(catalog.path)

    ranking = rank_dayhikes(catalog, ["Ridge Walk"], {"length": 100})

    assert [item.dayhike.id for item in ranking.items] == [1003]


def test_ranking_is_deterministic_across_two_builds(tmp_path: Path) -> None:
    first = rank_dayhikes(_catalog(tmp_path / "a"), ["Ridge Walk"])
    second = rank_dayhikes(_catalog(tmp_path / "b"), ["Ridge Walk"])

    assert [(item.dayhike.id, item.score) for item in first.items] == [
        (item.dayhike.id, item.score) for item in second.items
    ]


@pytest.mark.parametrize(
    "weights, message",
    [
        ({"huts": 50}, "unknown Measure"),
        ({"length": 101}, "0-100"),
        ({"length": -1}, "0-100"),
        ({"length": 0, "profile": 0}, "above 0"),
    ],
)
def test_bad_weights_fail(tmp_path: Path, weights, message) -> None:
    catalog = _catalog(tmp_path)

    with pytest.raises(ValueError, match=message):
        rank_dayhikes(catalog, ["Ridge Walk"], weights)


def test_query_month_must_be_1_to_12_and_needs_sampled_climate(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)

    for month in (0, 13, True):
        with pytest.raises(ValueError, match="QueryMonth"):
            rank_dayhikes(catalog, ["Ridge Walk"], month=month)
    with pytest.raises(ValueError, match="climate has not been sampled"):
        rank_dayhikes(catalog, ["Ridge Walk"], month=7)


def test_ranking_needs_the_similarity_facts(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")

    with pytest.raises(StaleSimilarity, match="build-similarity"):
        rank_dayhikes(catalog, ["Ridge Walk"])


def test_ingest_removes_similarity_facts_built_for_the_old_catalog(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    directory = build_similarity(catalog_path, tmp_path / "dem")
    messages: list[str] = []

    catalog = build_catalog(FIXTURE, catalog_path, progress=messages.append)

    assert not directory.exists()
    assert f"removed stale similarity facts {directory}; run `dayhike build-similarity`" in messages
    with pytest.raises(StaleSimilarity, match="build-similarity"):
        rank_dayhikes(catalog, ["Ridge Walk"])


def test_unknown_seed_fails_via_name_resolution(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")

    with pytest.raises(UnknownSeedName, match="Unknown Ridge"):
        rank_dayhikes(catalog, ["Unknown Ridge"])


def test_ambiguous_seed_fails_via_name_resolution(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    catalog = replace(
        catalog,
        dayhikes=tuple(replace(hike, name="Shared Path") for hike in catalog.dayhikes),
    )

    with pytest.raises(AmbiguousSeedName, match="Shared Path"):
        rank_dayhikes(catalog, ["Shared Path"])


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "dayhike", *args], check=False, capture_output=True, text=True
    )


def test_cli_builds_similarity_and_prints_id_name_and_score(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, path)

    built = _cli("build-similarity", "--catalog", str(path), "--dem", str(tmp_path / "dem"))
    assert built.returncode == 0, built.stderr
    assert built.stdout.strip() == str(tmp_path / "catalog.similarity")
    recalibrated = _cli("build-similarity", "--catalog", str(path), "--calibrate-only")
    assert recalibrated.returncode == 0, recalibrated.stderr

    result = _cli("rank", "--catalog", str(path), "--weight", "length=100", "Ridge Walk")
    limited = _cli("rank", "--catalog", str(path), "--limit", "1", "Ridge Walk")

    assert result.returncode == 0, result.stderr
    lines = [line.split("\t") for line in result.stdout.strip().splitlines()]
    assert str(RIDGE_WALK_ID) not in [line[0] for line in lines]
    assert "Valley Path" in [line[1] for line in lines]
    assert all(0.0 <= float(line[2]) <= 1.0 for line in lines)
    assert limited.returncode == 0, limited.stderr
    assert len(limited.stdout.strip().splitlines()) == 1
    # With the way back by default; --one-way compares every hike one way.
    catalog = Catalog.open(path)
    for flags, return_to_start in (((), True), (("--one-way",), False)):
        printed = _cli("rank", "--catalog", str(path), *flags, "--weight", "length=100", "Ridge Walk")
        expected = rank_dayhikes(catalog, ["Ridge Walk"], {"length": 100}, return_to_start=return_to_start)
        assert [line.split("\t")[2] for line in printed.stdout.strip().splitlines()] == [
            str(item.score) for item in expected.items
        ]


def test_cli_rejects_a_bad_weight(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    build_similarity(path, tmp_path / "dem")

    malformed = _cli("rank", "--catalog", str(path), "--weight", "length", "Ridge Walk")
    unknown = _cli("rank", "--catalog", str(path), "--weight", "huts=50", "Ridge Walk")

    assert malformed.returncode == 2
    assert "MEASURE=WEIGHT" in malformed.stderr
    assert unknown.returncode == 2
    assert "unknown Measure" in unknown.stderr
