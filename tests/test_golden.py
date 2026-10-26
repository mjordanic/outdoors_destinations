"""Golden facts about well-known DayHikes hold on a full Catalog.

Slow, opt-in (DAYHIKE_REAL_DATA=1); reads DAYHIKE_DATA_DIR (default data/).
The pair checks also compare with DAYHIKE_REFERENCE_DIR/golden/pairs.tsv.
Ids, not names: a Seed name can be ambiguous Europe-wide.
"""

from __future__ import annotations

import pytest
import rebuild_checks as rc

from dayhike import Catalog, SimilarityStore

pytestmark = rc.real_data
# id -> published summit height (m). FABDEM is bare earth and smooths peaks,
# so elevation_max_m may sit up to 80 m below it (and 10 m above).
SUMMITS = {
    4004229: 1_345,  # Ben Nevis Mountain Path
    19446684: 2_864,  # Aljažev dom - Kredarica - Triglav
    3360396: 2_499,  # Czarny Staw Pod Rysami - Rysy
    15661826: 2_351,  # Montanha do Pico
    11340787: 1_465,  # Le Puy de Dôme
    2235103: 1_862,  # PR 1.2 - Vereda do Pico Ruivo
}
BEN_NEVIS_PATH, BEN_NEVIS_TRAIL, OLYMPIAROUTE = 4004229, 16127475, 17991231


@pytest.fixture(scope="module")
def store() -> SimilarityStore:
    # Through the Catalog, so a store that does not belong to it is refused.
    return Catalog.open(rc.catalog_path()).similarity


def test_famous_summits_have_their_height() -> None:
    catalog = rc.connect_ro(rc.catalog_path())
    try:
        heights = dict(
            catalog.execute(
                f"SELECT dayhike_id, elevation_max_m FROM feature_vectors "
                f"WHERE dayhike_id IN ({','.join('?' * len(SUMMITS))})",
                list(SUMMITS),
            )
        )
    finally:
        catalog.close()

    assert {
        dayhike_id: heights.get(dayhike_id)
        for dayhike_id, published in SUMMITS.items()
        if heights.get(dayhike_id) is None or not published - 80 <= heights[dayhike_id] <= published + 10
    } == {}


@pytest.mark.parametrize(("seed", "reverse"), [(17085818, 17085817), (17085819, 17085816)])
def test_a_route_and_its_reverse_are_each_others_best_match(store, seed: int, reverse: int) -> None:
    similarity, raw, rank = rc.similarity_between(store, seed, reverse, rc.GEOMETRY_WEIGHTS)

    assert rank == 1
    assert raw == pytest.approx(1.0, abs=1e-3)
    assert similarity >= 0.98


def test_expected_similarity_ordering(store) -> None:
    trail, _, trail_rank = rc.similarity_between(store, BEN_NEVIS_PATH, BEN_NEVIS_TRAIL, rc.ALTITUDE_WEIGHTS)
    other, _, _ = rc.similarity_between(store, BEN_NEVIS_PATH, OLYMPIAROUTE, rc.ALTITUDE_WEIGHTS)

    assert trail > other + 0.2
    assert trail_rank <= rc.GOLDEN_TOP


@rc.needs_reference
def test_golden_pairs_reproduce(store) -> None:
    sets = rc.golden_weight_sets()
    for row in rc.read_tsv(rc.REFERENCE_DIR / "golden" / "pairs.tsv"):
        similarity, raw, rank = rc.similarity_between(store, int(row["seed"]), int(row["other"]), sets[row["set"]])
        assert similarity == pytest.approx(float(row["similarity"]), abs=1e-6), row
        assert raw == pytest.approx(float(row["raw"]), abs=1e-6), row
        assert rank == int(row["rank"]), row
