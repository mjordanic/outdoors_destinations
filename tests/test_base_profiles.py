from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from dayhike.bases import REACHES, BaseFacts, HikeTypes, _write_bases, base_profiles
from test_bases import KINDS, PER_KIND
from test_export_bases import _build, _export, _load

HIKES = len(KINDS) * PER_KIND


def _naive(bases: dict[str, np.ndarray], reach: int, hike_mask: np.ndarray) -> list[dict[int, int]]:
    """Each Base's HikeType -> count, hike by hike: a HikeGroup counts once if any of its
    hikes passes and its minutes from the Base are within ``reach``."""
    count = len(bases["place_ids"])
    starts, hikes = bases["group_starts"], bases["group_hikes"]
    group_of_hike = {int(h): g for g in range(len(starts) - 1) for h in hikes[starts[g] : starts[g + 1]]}
    base_of_pair = np.empty(len(bases["pair_groups"]), dtype=int)
    for slot in range(len(bases["base_runs"]) - 1):
        runs = slice(bases["base_runs"][slot], bases["base_runs"][slot + 1] + 1)
        offsets = bases["run_offsets"][runs]
        if len(offsets):
            base_of_pair[offsets[0] : offsets[-1]] = slot % count
    profiles = []
    for base in range(count):
        mine = base_of_pair == base
        in_reach = set(bases["pair_groups"][mine & (bases["pair_minutes"] <= reach)].tolist())
        passing = {group_of_hike[h] for h in np.flatnonzero(hike_mask).tolist()} & in_reach
        profiles.append(dict(Counter(int(bases["group_types"][g]) for g in passing)))
    return profiles


def _as_dicts(profiles) -> list[dict[int, int]]:
    found = []
    for i in range(len(profiles.starts) - 1):
        at = slice(profiles.starts[i], profiles.starts[i + 1])
        assert (np.diff(profiles.types[at]) > 0).all()  # by HikeType, each once
        assert (profiles.counts[at] > 0).all()
        found.append(dict(zip(profiles.types[at].tolist(), profiles.counts[at].tolist())))
    return found


def _random_bases(tmp_path: Path, seed: int) -> dict[str, np.ndarray]:
    """A bases/ folder of random Reach: 40 Bases, 30 HikeTypes, 400 HikeGroups of 1-4 hikes."""
    rng = np.random.default_rng(seed)
    types, groups, bases = 30, 400, 40
    group_types = np.sort(rng.integers(0, types, groups)).astype(np.int16)
    group_of_hike = rng.permutation(np.r_[np.arange(groups), rng.integers(0, groups, 600)])
    reached = []
    for _ in range(bases):
        chosen = np.sort(rng.choice(groups, rng.integers(0, 120), replace=False)).astype(np.int32)
        reached.append((chosen, rng.integers(1, REACHES[-1] + 1, len(chosen)).astype(np.uint8)))
    hike_types = HikeTypes(types=np.zeros(0), middles=np.zeros(0), table=np.eye(types, dtype=np.float32), version="t")
    out = tmp_path / f"random-{seed}"
    out.mkdir()
    ids = np.arange(bases, dtype=np.int64)
    facts = BaseFacts(population=np.zeros(bases), month_temperatures=np.zeros((bases, len(REACHES), 12)))
    store = SimpleNamespace(ids=np.arange(len(group_of_hike)), catalog_digest="t")
    _write_bases(out, ids, np.zeros((bases, 2)), reached, group_of_hike, group_types, hike_types, facts, store)
    return _load(out)


@pytest.mark.parametrize("reach", REACHES)
def test_profiles_equal_a_naive_per_hike_count_on_the_fixture_export(tmp_path: Path, reach: int) -> None:
    out = _export(tmp_path)
    _build(out)
    bases = _load(out)
    rng = np.random.default_rng(reach)

    for share in (0.0, 0.3, 0.7, 1.0):
        hike_mask = rng.random(HIKES) < share
        assert _as_dicts(base_profiles(bases, reach, hike_mask)) == _naive(bases, reach, hike_mask)


@pytest.mark.parametrize("seed", range(5))
def test_profiles_equal_a_naive_per_hike_count_for_random_masks_and_every_reach(tmp_path: Path, seed: int) -> None:
    bases = _random_bases(tmp_path, seed)
    rng = np.random.default_rng(seed)
    hikes = len(bases["group_hikes"])

    for reach in REACHES:
        for share in (0.05, 0.5, 1.0):
            hike_mask = rng.random(hikes) < share
            assert _as_dicts(base_profiles(bases, reach, hike_mask)) == _naive(bases, reach, hike_mask)


def test_a_group_counts_once_when_several_of_its_hikes_pass(tmp_path: Path) -> None:
    out = _export(tmp_path)
    _build(out)
    bases = _load(out)
    one, three = np.zeros(HIKES, dtype=bool), np.zeros(HIKES, dtype=bool)
    one[0] = True
    three[[0, 1, 2]] = True  # hike 0, its Twin and a hike of its kind in its cell: one HikeGroup

    with_one = _as_dicts(base_profiles(bases, 60, one))
    with_three = _as_dicts(base_profiles(bases, 60, three))

    assert with_three == with_one
    assert sum(with_one[0].values()) == 1  # Keswick reaches hike 0's group within the hour


def test_an_unknown_reach_or_a_mask_of_the_wrong_length_is_refused(tmp_path: Path) -> None:
    bases = _random_bases(tmp_path, 0)
    hikes = len(bases["group_hikes"])

    with pytest.raises(ValueError):
        base_profiles(bases, 45, np.ones(hikes, dtype=bool))
    with pytest.raises(ValueError):
        base_profiles(bases, 30, np.ones(hikes - 1, dtype=bool))
