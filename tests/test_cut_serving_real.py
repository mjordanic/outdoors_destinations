"""A cut of the real Europe and US export (``dayhike cut-serving``, ADR-0082) holds only what it keeps,
and ranks as the uncut export with only its side ticked.

Slow, opt-in (DAYHIKE_REAL_DATA=1, and DAYHIKE_CUT_DIR, a cut written by ``dayhike cut-serving``);
the uncut export is DAYHIKE_SERVING_DIR (default DAYHIKE_DATA_DIR/web-serving). A cut to ``europe``
or ``us`` is checked side by side; a cut file's (the demo's) Rankings of DayHikes only, as its
SuggestedLoops near its edges may walk where it keeps no trail.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tarfile
from pathlib import Path

import numpy as np
import pytest
import rebuild_checks as rc

from dayhike.cut import tile_box
from dayhike.serving import REGION_SPLIT_LON, ServingData
from dayhike.similarity import CORE_MEASURES

CUT_DIR = Path(os.environ["DAYHIKE_CUT_DIR"]) if os.environ.get("DAYHIKE_CUT_DIR") else None
pytestmark = [*rc.real_data, pytest.mark.skipif(CUT_DIR is None, reason="no cut: set DAYHIKE_CUT_DIR")]
WEIGHTS = {name: 100.0 for name in CORE_MEASURES}
WISHES = ({"length": 8_000, "elevation_gain": 600}, {"length": 15_000, "highest": 2_500, "temperature": 12})
SEEDS = 25  # DayHikes ranked from, drawn from the cut's
LOOP_SEEDS = 4  # of which ranked with SuggestedLoops too


@pytest.fixture(scope="module")
def whole() -> ServingData:
    return ServingData(rc.SERVING_DIR)


@pytest.fixture(scope="module")
def cut() -> ServingData:
    return ServingData(CUT_DIR)


@pytest.fixture(scope="module")
def side(whole: ServingData, cut: ServingData) -> str | None:
    """``europe`` or ``us`` for a cut to one side, None for a cut file's."""
    suffix = cut.meta["data_tag"].removeprefix(f"{whole.meta['data_tag']}-")
    return {"eu": "europe", "us": "us"}.get(suffix)


def _seeds(cut: ServingData) -> list[int]:
    return np.random.default_rng(0).choice(cut.store.ids, SEEDS, replace=False).tolist()


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def test_a_cut_keeps_hikes_of_the_whole_export_with_their_rows(whole: ServingData, cut: ServingData) -> None:
    rows = np.searchsorted(whole.store.ids, cut.store.ids)

    assert np.array_equal(whole.store.ids[rows], cut.store.ids)
    assert np.array_equal(np.asarray(whole.trailheads)[rows], np.asarray(cut.trailheads))
    assert np.array_equal(np.asarray(whole.store.facts)[:, rows], np.asarray(cut.store.facts), equal_nan=True)
    assert cut.meta["dayhikes"] == len(cut.store.ids)
    with sqlite3.connect(f"file:{CUT_DIR / 'hikes.sqlite'}?mode=ro&immutable=1", uri=True) as db:
        assert [i for (i,) in db.execute("SELECT id FROM hikes ORDER BY id")] == cut.store.ids.tolist()


def test_a_cut_to_one_side_holds_nothing_of_the_other(cut: ServingData, side: str | None) -> None:
    if side is None:
        pytest.skip("a cut file's cut")
    west = side == "us"
    assert cut.regions == [side]
    with sqlite3.connect(f"file:{CUT_DIR / 'trailgraph.sqlite'}?mode=ro&immutable=1", uri=True) as db:
        for table, lon in (("junctions", "longitude"), ("amenities", "longitude"), ("segment_boxes", "west"), ("segment_boxes", "east")):
            other = db.execute(f"SELECT count(*) FROM {table} WHERE ({lon} < ?) != ?", (REGION_SPLIT_LON, west)).fetchone()[0]
            assert other == 0, (table, lon)
    with tarfile.open(CUT_DIR / "valhalla" / "valhalla_tiles.tar") as tar:
        boxes = [tile_box(m.name)[2] for m in tar if m.name.endswith(".gph")]
    assert boxes and all((box.east <= REGION_SPLIT_LON) == west and (box.west >= REGION_SPLIT_LON) != west for box in boxes)


@pytest.mark.parametrize("return_to_start", [True, False])
def test_a_cut_ranks_as_the_whole_export_with_only_its_hikes_allowed(
    whole: ServingData, cut: ServingData, side: str | None, return_to_start: bool
) -> None:
    allowed = whole.region_rows([side]) if side else np.searchsorted(whole.store.ids, cut.store.ids)
    for seed in [*_seeds(cut), *WISHES]:
        expected = whole.rank(seed, WEIGHTS, allowed_rows=allowed, return_to_start=return_to_start)
        found = cut.rank(seed, WEIGHTS, return_to_start=return_to_start)
        assert _same(list(found), list(expected)), seed
        assert found.other_areas == expected.other_areas, seed


def test_a_cut_to_one_side_lists_the_same_suggested_loops(whole: ServingData, cut: ServingData, side: str | None) -> None:
    if side is None:
        pytest.skip("a cut file's cut")
    allowed = whole.region_rows([side])
    compared = 0
    for seed in _seeds(cut)[:LOOP_SEEDS]:
        expected = whole.rank(seed, WEIGHTS, allowed_rows=allowed, loops=True)
        found = cut.rank(seed, WEIGHTS, loops=True)
        # A search stopped by its time budget (LOOP_BUDGET_S) finds what the machine's speed let it.
        if expected.loop_search["stopped"] or found.loop_search["stopped"]:
            continue
        assert _same(list(found), list(expected)), seed
        compared += 1
    assert compared


def test_a_cut_shows_each_hike_as_the_whole_export_does(whole: ServingData, cut: ServingData) -> None:
    for dayhike_id in _seeds(cut):
        assert _same(cut.detail(dayhike_id), whole.detail(dayhike_id)), dayhike_id
