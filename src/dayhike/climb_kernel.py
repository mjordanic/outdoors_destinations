"""``similarity.climb_and_descent`` compiled with numba: one loop for the spike
drop, ``_smooth_noisy`` and the dead-band, with the same float operations in the
same order as the numpy code, so it gives the same bits (ADR-0070).

``similarity`` keeps the numpy code; it runs where numba is not installed, and
the tests compare the two. numpy rounds ``a * b + c`` once (a fused
multiply-add) inside ``np.interp`` and ``np.arange`` on some builds (arm64) and
twice on others (x86-64), so ``FUSED_INTERP`` and ``FUSED_ARANGE`` are read off
numpy at import and the kernel does the same. When numpy matches neither,
``COMPILED`` is False and the numpy code runs.
"""

from __future__ import annotations

import math

import numpy as np


def _numpy_fuses(values: list[float], expected: np.ndarray) -> bool | None:
    """Whether ``expected`` (numpy's ``a * b + c`` per row of ``values``) is the
    fused result, the twice-rounded one, or (None) neither."""
    fused = np.array([math.fma(a, b, c) for a, b, c in values])
    plain = np.array([a * b + c for a, b, c in values])
    if np.array_equal(fused, plain):
        return None  # the probe tells nothing
    if np.array_equal(expected, fused):
        return True
    if np.array_equal(expected, plain):
        return False
    return None


def _probe() -> tuple[bool | None, bool | None]:
    """How numpy's ``interp`` and ``arange`` round on this build: probes where
    the fused and the twice-rounded results differ (``test_climb_kernel``)."""
    rng = np.random.default_rng(20261118)
    xp, fp = np.array([0.0, 3.0]), np.array([0.1, 1.3])
    x = rng.uniform(0.0, 3.0, 256)
    slope = (fp[1] - fp[0]) / (xp[1] - xp[0])
    interp = _numpy_fuses([(slope, v - xp[0], fp[0]) for v in x.tolist()], np.interp(x, xp, fp))
    first = 8.235619224713497  # (first + 10) - first is 9.999999999999998: i * delta rounds
    count = 256
    filled = np.arange(first, first + 10.0 * count - 5.0, 10.0)[2:]
    delta = (first + 10.0) - first
    arange = _numpy_fuses([(float(i), delta, first) for i in range(2, count)], filled)
    return interp, arange


try:
    from llvmlite import ir
    from numba import njit
    from numba.core import cgutils, types
    from numba.extending import intrinsic
except ImportError:  # numba is optional: similarity runs its numpy code
    COMPILED = False
else:
    FUSED_INTERP, FUSED_ARANGE = _probe()
    COMPILED = FUSED_INTERP is not None and FUSED_ARANGE is not None

if COMPILED:

    @intrinsic
    def _fma(typingctx, a, b, c):
        """``a * b + c`` rounded once (LLVM's ``llvm.fma``, exact on every target)."""
        signature = types.float64(types.float64, types.float64, types.float64)

        def codegen(context, builder, sig, args):
            double = ir.DoubleType()
            fma = cgutils.get_or_insert_function(builder.module, ir.FunctionType(double, [double] * 3), "llvm.fma.f64")
            return builder.call(fma, args)

        return signature, codegen

    @njit
    def _bigger(a, b):
        """Python's ``max(a, b)``: ``a`` unless ``b`` is larger."""
        return b if b > a else a

    @njit
    def _smaller(a, b):
        """Python's ``min(a, b)``: ``a`` unless ``b`` is smaller."""
        return b if b < a else a

    @njit
    def _seek(at, x, c):
        """``np.searchsorted(at, x, side="right")``, walked from the last answer
        ``c``: the queries mostly move forward, a few steps at a time."""
        while c < at.size and at[c] <= x:
            c += 1
        while c > 0 and at[c - 1] > x:
            c -= 1
        return c

    @njit
    def _interp(x, xp, fp, fused, c):
        """``np.interp(x, xp, fp)`` for one ``x`` (numpy's ``arr_interp``), and
        ``_seek``'s ``c`` for ``x``."""
        c = _seek(xp, x, c)
        last = xp.size - 1
        if x > xp[last]:
            return fp[last], c
        if x < xp[0]:
            return fp[0], c
        j = c - 1
        if j == last or xp[j] == x:
            return fp[j], c
        slope = (fp[j + 1] - fp[j]) / (xp[j + 1] - xp[j])
        value = _fma(slope, x - xp[j], fp[j]) if fused else slope * (x - xp[j]) + fp[j]
        if math.isnan(value):
            value = _fma(slope, x - xp[j + 1], fp[j + 1]) if fused else slope * (x - xp[j + 1]) + fp[j + 1]
            if math.isnan(value) and fp[j] == fp[j + 1]:
                value = fp[j]
        return value, c

    @njit
    def _areas(at, values):
        """``_line_means``' ``step`` and ``area``."""
        step = np.empty(at.size - 1)
        area = np.empty(at.size)
        area[0] = 0.0
        for k in range(at.size - 1):
            step[k] = at[k + 1] - at[k]
            piece = step[k] * (values[k] + values[k + 1]) / 2
            area[k + 1] = piece if k == 0 else area[k] + piece
        return step, area

    @njit
    def _area_to(at, values, step, area, x, c):
        """``_line_means``' ``area_to`` for one ``x``, and ``_seek``'s ``c`` for ``x``."""
        c = _seek(at, x, c)
        i = min(max(c - 1, 0), at.size - 2)
        into = x - at[i]
        slope = (values[i + 1] - values[i]) / step[i] if step[i] > 0 else 0.0
        return area[i] + into * (values[i] + slope * into / 2), c

    @njit
    def _line_mean(at, values, step, area, start, end, fused, c_start, c_end):
        """``_line_means`` for one ``start`` and ``end``, and ``_seek``'s ``c`` for each."""
        width = end - start
        if width > 0:
            to_end, c_end = _area_to(at, values, step, area, end, c_end)
            to_start, c_start = _area_to(at, values, step, area, start, c_start)
            return (to_end - to_start) / width, c_start, c_end
        value, c_start = _interp(start, at, values, fused, c_start)
        return value, c_start, c_end

    @njit
    def _moving_average(at, values, step, area, x, window_m, fused, c_start, c_end):
        """``_smooth_noisy``'s ``moving_average`` at one ``x``, and ``_seek``'s ``c``
        for each end of its window."""
        half = min(window_m / 2, min(x - at[0], at[at.size - 1] - x))
        return _line_mean(at, values, step, area, x - half, x + half, fused, c_start, c_end)

    @njit
    def _even_places(first, last, step_m, fused):
        """``np.arange(first, last, step_m)`` (numpy fills ``first + i * delta``)."""
        count = max(int(math.ceil((last - first) / step_m)), 0)
        places = np.empty(count)
        if count > 0:
            places[0] = first
        if count > 1:
            places[1] = first + step_m
        delta = places[1] - first if count > 1 else 0.0
        for i in range(2, count):
            places[i] = _fma(float(i), delta, first) if fused else first + i * delta
        return places

    @njit
    def _union(a, b):
        """``np.union1d(a, b)`` of two sorted arrays of finite floats."""
        out = np.empty(a.size + b.size)
        i = j = size = 0
        while i < a.size or j < b.size:
            if j == b.size or (i < a.size and a[i] <= b[j]):
                value = a[i]
                i += 1
            else:
                value = b[j]
                j += 1
            if size == 0 or value != out[size - 1]:
                out[size] = value
                size += 1
        return out[:size]

    @njit
    def _smooth_noisy(at, height, size, mean_m, reach_m, window_m, step_m, fused_interp, fused_arange):
        """``similarity._smooth_noisy`` of the first ``size`` entries, in place."""
        if size < 3 or at[size - 1] <= at[0]:
            return
        at_m, metres = at[:size].copy(), height[:size].copy()
        first, last = at_m[0], at_m[size - 1]
        step, area = _areas(at_m, metres)
        points = _union(at_m, _even_places(first, last, step_m, fused_arange))
        away = np.empty(points.size)
        c_line = c_start = c_end = 0
        for k in range(points.size):
            x = points[k]
            line, c_line = _interp(x, at_m, metres, fused_interp, c_line)
            mean, c_start, c_end = _moving_average(at_m, metres, step, area, x, window_m, fused_interp, c_start, c_end)
            away[k] = abs(line - mean)
        away_step, away_area = _areas(points, away)
        c_near = c_far = c_start = c_end = 0
        for k in range(size):
            stray, c_near, c_far = _line_mean(
                points,
                away,
                away_step,
                away_area,
                max(at_m[k] - reach_m, first),
                min(at_m[k] + reach_m, last),
                fused_interp,
                c_near,
                c_far,
            )
            if stray >= mean_m:
                height[k], c_start, c_end = _moving_average(
                    at_m, metres, step, area, at_m[k], window_m, fused_interp, c_start, c_end
                )

    @njit
    def _one_way(steps, ele, forward, rules, fused_interp, fused_arange):
        """``similarity._one_way_climb_and_descent`` walking ``ele`` forward or
        backward; ``steps`` are the metres between its vertices in that order."""
        rise_m, grade, run_m, hysteresis_m, mean_m, reach_m, window_m, step_m = rules
        n = ele.size
        at = np.empty(n)
        height = np.empty(n)
        size = 0
        along = 0.0
        for k in range(n):
            if k == 1:
                along = steps[0]
            elif k > 1:
                along = along + steps[k - 1]
            value = ele[k] if forward else ele[n - 1 - k]
            if math.isfinite(value):
                at[size] = along
                height[size] = value
                size += 1
        if size == 0:
            return math.nan, math.nan
        # The kept vertices as a stack in ``at`` / ``height``, below ``kept``.
        kept = 1
        for k in range(1, size):
            here_at, here = at[k], height[k]
            while kept >= 2 and abs(here - height[kept - 1]) >= grade * (here_at - at[kept - 1]):
                start = kept - 1
                top = bottom = height[start]
                while True:
                    top, bottom = _bigger(top, height[start]), _smaller(bottom, height[start])
                    before = height[start - 1]
                    if (
                        bottom - _bigger(before, here) >= rise_m or _smaller(before, here) - top >= rise_m
                    ) and abs(height[start] - before) >= grade * (at[start] - at[start - 1]):
                        break
                    start -= 1
                    if start < 1 or at[kept - 1] - at[start] > run_m:
                        start = 0
                        break
                if not start:
                    break
                kept = start
            at[kept] = here_at
            height[kept] = here
            kept += 1
        _smooth_noisy(at, height, kept, mean_m, reach_m, window_m, step_m, fused_interp, fused_arange)
        climb = descent = 0.0
        last = height[0]
        for k in range(kept):
            metres = height[k]
            if metres - last >= hysteresis_m:
                climb += metres - last
                last = metres
            elif last - metres >= hysteresis_m:
                descent += last - metres
                last = metres
        end = height[kept - 1]
        return climb + _bigger(end - last, 0.0), descent + _bigger(last - end, 0.0)

    @njit
    def _both_ways(steps, back_steps, ele, rules, fused_interp, fused_arange):
        climb, descent = _one_way(steps, ele, True, rules, fused_interp, fused_arange)
        back_climb, back_descent = _one_way(back_steps, ele, False, rules, fused_interp, fused_arange)
        return (climb + back_descent) / 2, (descent + back_climb) / 2


def climb_and_descent(
    steps: np.ndarray, back_steps: np.ndarray, ele: np.ndarray, rules: tuple[float, ...]
) -> tuple[float, float]:
    """``similarity.climb_and_descent`` from the metres between the vertices
    walked forward (``steps``) and walked back (``back_steps``), the heights in
    walk order and the rules (``SPIKE_RISE_M``, ``SPIKE_GRADE``, ``SPIKE_RUN_M``,
    ``GAIN_HYSTERESIS_M``, ``NOISY_MEAN_M``, ``NOISY_REACH_M``,
    ``NOISY_WINDOW_M``, ``NOISY_STEP_M``). Only when ``COMPILED``."""
    return _both_ways(
        np.ascontiguousarray(steps, dtype=np.float64),
        np.ascontiguousarray(back_steps, dtype=np.float64),
        np.ascontiguousarray(ele, dtype=np.float64),
        tuple(float(rule) for rule in rules),
        FUSED_INTERP,
        FUSED_ARANGE,
    )
