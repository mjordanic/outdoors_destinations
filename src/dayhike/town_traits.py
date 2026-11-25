"""TownTraits: what the Hiker asks of a Base besides its hikes (ADR-0084).

Each TownTrait is ignored, a filter (a Base that fails it is left out before the best hike
matches are chosen) or "like mine" with an importance w of 1, 2 or 3, which multiplies the hike
score by ``1 - (w / 6) * (1 - s)``, s the similarity of the two towns on it. A missing value
costs nothing. Sizes and counts compare as the ratio Measure does, months as the tolerance
Measure does, month by month.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, fields

import numpy as np

TRAITS = ("size", "stays", "eats", "months")
_COUNTS = ("stays", "eats")  # compared on count + 1, so a town with none is still a ratio
IMPORTANCE_CUT = 1 / 6  # of the hike score, per step of importance, for a town wholly unlike
RATIO_POWER = 0.5  # (smaller / larger) ** this: four times bigger is 0.5
MONTHS_TOLERANCE_C = 10.0  # a month this much warmer or colder is wholly unlike
MILD_C = (8.0, 22.0)  # a month is mild when the median temperature of the hikes in Reach is in this


@dataclass(frozen=True)
class TraitSetting:
    """One TownTrait's setting: ignore (the default), a filter, or "like mine".

    ``like_mine`` is the importance, 1 (*a little*), 2 (*matters*) or 3 (*important*), 0 for
    not. A filter is ``at_least`` and ``up_to`` for size and counts, both kept; ``mild_in``
    the months (1-12) the Base must be mild in, for months.
    """

    like_mine: int = 0
    at_least: float | None = None
    up_to: float | None = None
    mild_in: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.like_mine, bool) or self.like_mine not in (0, 1, 2, 3):
            raise ValueError(f"like_mine is an importance of 0 to 3, not {self.like_mine!r}")
        for limit in (self.at_least, self.up_to):
            if limit is not None and (
                isinstance(limit, bool) or not isinstance(limit, int | float) or not math.isfinite(limit) or limit < 0
            ):
                raise ValueError(f"a limit is a number of 0 or more, not {limit!r}")
        if self.at_least is not None and self.up_to is not None and self.at_least > self.up_to:
            raise ValueError(f"at least {self.at_least} is more than up to {self.up_to}")
        if any(isinstance(m, bool) or m not in range(1, 13) for m in self.mild_in):
            raise ValueError(f"mild_in names months 1 to 12, not {self.mild_in!r}")
        if self.like_mine and self.is_filter:
            raise ValueError("a TownTrait is a filter or like mine, not both")

    @property
    def is_filter(self) -> bool:
        return self.at_least is not None or self.up_to is not None or bool(self.mild_in)


@dataclass(frozen=True)
class TownTraits:
    """The Hiker's setting for each TownTrait: town size, places to stay, restaurants, cafes
    and pubs (eats), and the months the Base is mild in."""

    size: TraitSetting = TraitSetting()
    stays: TraitSetting = TraitSetting()
    eats: TraitSetting = TraitSetting()
    months: TraitSetting = TraitSetting()

    def __post_init__(self) -> None:
        for name in TRAITS:
            setting = getattr(self, name)
            if name == "months" and (setting.at_least is not None or setting.up_to is not None):
                raise ValueError("months are filtered by mild_in, not by a number")
            if name != "months" and setting.mild_in:
                raise ValueError(f"{name} is filtered by a number, not by mild_in")

    def settings(self) -> dict[str, TraitSetting]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def ratio_similarity(mine: float, theirs: np.ndarray) -> np.ndarray:
    """``(smaller / larger) ** RATIO_POWER`` of ``mine`` against each of ``theirs``; NaN for a missing one."""
    theirs = np.asarray(theirs, dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        return (np.minimum(mine, theirs) / np.maximum(mine, theirs)) ** RATIO_POWER


def months_similarity(mine: np.ndarray, theirs: np.ndarray) -> np.ndarray:
    """``1 - mean |T_mine - T_theirs| / MONTHS_TOLERANCE_C``, at least 0, over the months both
    have, for each row of ``theirs`` (Bases, 12); NaN when they share no month."""
    gaps = np.abs(np.asarray(theirs, dtype=np.float64) - np.asarray(mine, dtype=np.float64))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # no month in common
        return np.maximum(0.0, 1 - np.nanmean(gaps, axis=-1) / MONTHS_TOLERANCE_C)


def trait_passes(traits: TownTraits, values: Mapping[str, np.ndarray]) -> np.ndarray:
    """Yes/no per Base: does it pass every TownTrait filter. ``values`` holds each trait's value
    per Base, NaN when unknown, (Bases, 12) month temperatures for months. An unknown value
    fails "at least" and passes "up to"; an unknown month is not mild."""
    keep = np.ones(len(values["size"]), dtype=bool)
    for name, setting in traits.settings().items():
        value = np.asarray(values[name], dtype=np.float64)
        if setting.at_least is not None:
            keep &= value >= setting.at_least
        if setting.up_to is not None:
            keep &= ~(value > setting.up_to)
        if setting.mild_in:
            picked = value[:, np.asarray(setting.mild_in) - 1]
            keep &= ((picked >= MILD_C[0]) & (picked <= MILD_C[1])).all(axis=1)
    return keep


def trait_cuts(
    traits: TownTraits, values: Mapping[str, np.ndarray], me: int, bases: np.ndarray
) -> list[tuple[str, np.ndarray]]:
    """For each "like mine" TownTrait, in ``TRAITS`` order, the share of the hike score each of
    ``bases`` loses against the Hiker's Base ``me``: ``(w / 6) * (1 - s)``, 0 for a missing value."""
    cuts = []
    for name, setting in traits.settings().items():
        if not setting.like_mine:
            continue
        value = np.asarray(values[name], dtype=np.float64)
        if name == "months":
            similarity = months_similarity(value[me], value[bases])
        else:
            extra = 1 if name in _COUNTS else 0
            similarity = ratio_similarity(value[me] + extra, value[bases] + extra)
        cut = setting.like_mine * IMPORTANCE_CUT * (1 - similarity)
        cuts.append((name, np.nan_to_num(cut, nan=0.0)))
    return cuts
