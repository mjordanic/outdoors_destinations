"""The compiled ``climb_and_descent`` (``climb_kernel``) gives the same bits as
the numpy code it replaces, on walks with NaN gaps, flat stretches, spikes,
noisy shores and vertices at one spot (ADR-0070)."""

import math

import numpy as np
import pytest

pytest.importorskip("numba")

import dayhike.similarity as similarity
from dayhike import climb_kernel


def _walk(rng: np.random.Generator):
    """A random walk: lat, lon and heights, some of them NaN."""
    size = int(rng.choice([1, 2, 3, 4, 5, int(rng.integers(6, 60)), int(rng.integers(60, 1500))]))
    step = rng.choice([0.0, 1.0, 10.0, 30.0, 120.0, 600.0]) * rng.uniform(0.2, 1.5, size)
    step[rng.random(size) < rng.uniform(0.0, 0.2)] = 0.0  # vertices at one spot
    north = np.cumsum(step) / 111_195.0
    heading = rng.uniform(0.0, 2 * math.pi)
    lat = 46.0 + rng.uniform(-20, 20) + north * math.cos(heading)
    lon = 13.0 + north * math.sin(heading) / math.cos(math.radians(46.0)) + np.cumsum(rng.normal(0.0, 1e-5, size))
    kind = rng.integers(0, 5)
    if kind == 0:  # flat: every height the same, or nearly
        ele = np.full(size, rng.uniform(0, 3000)) + rng.choice([0.0, 0.5]) * rng.random(size)
    elif kind == 1:  # whole metres, as a DEM gives, with spikes
        ele = np.round(800.0 + np.cumsum(rng.normal(0.0, rng.uniform(0.5, 20.0), size)))
    elif kind == 2:  # a steep shore: heights read from the slope and the water in turn
        ele = 1000.0 + rng.choice([0.0, rng.uniform(5.0, 40.0)], size) + np.cumsum(rng.normal(0.0, 1.0, size))
    else:
        ele = 500.0 + np.cumsum(rng.normal(0.0, rng.uniform(0.5, 15.0), size))
    spikes = rng.random(size) < rng.uniform(0.0, 0.3)
    ele[spikes] += rng.choice([-1.0, 1.0], spikes.sum()) * rng.uniform(5.0, 120.0, spikes.sum())
    ele[rng.random(size) < rng.choice([0.0, 0.05, 0.3, 0.9])] = math.nan
    if rng.random() < 0.1:  # a run of NaN at an end
        ele[: int(rng.integers(0, size + 1))] = math.nan
    return lat, lon, ele


def _same_bits(new: list[tuple[float, float]], old: list[tuple[float, float]]) -> bool:
    new_a, old_a = np.array(new, dtype=float), np.array(old, dtype=float)
    return np.array_equal(new_a, old_a, equal_nan=True) and np.array_equal(np.signbit(new_a), np.signbit(old_a))


def test_numpy_rounding_is_read_off_numpy() -> None:
    # The probes tell a fused multiply-add from two roundings, so a numpy build of either kind is matched.
    assert climb_kernel.COMPILED
    assert climb_kernel._probe() == (climb_kernel.FUSED_INTERP, climb_kernel.FUSED_ARANGE)
    assert None not in climb_kernel._probe()


def test_compiled_climb_and_descent_gives_the_numpy_codes_bits() -> None:
    rng = np.random.default_rng(20261118)
    walks = [_walk(rng) for _ in range(3000)]

    new = [similarity.climb_and_descent(*walk) for walk in walks]
    old = [similarity._numpy_climb_and_descent(*walk) for walk in walks]

    assert _same_bits(new, old)
    assert any(math.isnan(climb) for climb, _ in old) and any(climb > 0 for climb, _ in old)


def test_compiled_climb_and_descent_follows_the_rules_as_set(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = np.random.default_rng(20261119)
    walks = [_walk(rng) for _ in range(300)]
    for name, value in (("NOISY_MEAN_M", math.inf), ("SPIKE_RISE_M", 30.0), ("GAIN_HYSTERESIS_M", 2.0)):
        with monkeypatch.context() as patch:
            patch.setattr(similarity, name, value)

            new = [similarity.climb_and_descent(*walk) for walk in walks]
            old = [similarity._numpy_climb_and_descent(*walk) for walk in walks]

        assert _same_bits(new, old), name


def test_climb_and_descent_without_numba_runs_the_numpy_code(monkeypatch: pytest.MonkeyPatch) -> None:
    lat, lon, ele = _walk(np.random.default_rng(3))
    monkeypatch.setattr(climb_kernel, "COMPILED", False)
    called = []
    monkeypatch.setattr(similarity, "_numpy_climb_and_descent", lambda *walk: called.append(walk) or (1.0, 2.0))

    assert similarity.climb_and_descent(lat, lon, ele) == (1.0, 2.0)
    assert len(called) == 1


def test_the_kernels_interp_arange_and_union_are_numpys() -> None:
    rng = np.random.default_rng(20261120)
    for _ in range(2000):
        size = int(rng.integers(2, 60))
        xp = np.cumsum(rng.choice([0.0, 1.0], size) * rng.uniform(0.0, 40.0, size)) + rng.uniform(0.0, 5000.0)
        fp = np.round(rng.normal(1000.0, 300.0, size), int(rng.integers(0, 4)))
        x = np.concatenate([rng.uniform(xp[0] - 1.0, xp[-1] + 1.0, 50), xp])
        interp = np.array([climb_kernel._interp(v, xp, fp, climb_kernel.FUSED_INTERP, 0)[0] for v in x])
        if xp[-1] > xp[0]:
            places = climb_kernel._even_places(xp[0], xp[-1], 10.0, climb_kernel.FUSED_ARANGE)
            assert _same_bits(places, np.arange(xp[0], xp[-1], 10.0))
            assert _same_bits(climb_kernel._union(xp, places), np.union1d(xp, places))
        assert _same_bits(interp, np.interp(x, xp, fp))
