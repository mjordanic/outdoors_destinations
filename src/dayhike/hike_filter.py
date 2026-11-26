"""HikeFilter: the Hiker's limits on which hikes count in a BaseRanking (ADR-0084).

A filter is a yes/no mask over the Catalog's hikes, built from facts the store already maps.
It applies to each hike, not to its HikeType. The caller folds the mask into HikeGroups (a
group counts when any of its hikes passes).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields

import numpy as np

from dayhike.similarity import MEASURES, SimilarityStore

# Beside water from the Measure's dead band: a lesser share is a cove or a bridge (ADR-0058).
_WATERSIDE_MIN_SHARE = next(m.dead_band for m in MEASURES if m.name == "waterside")
_M_PER_KM = 1_000

# Range filters: link key stem, fact column, and the unit factor from the link's unit to the fact's.
_RANGES = (
    ("climb", "elevation_gain_m", 1),
    ("length", "length_m", _M_PER_KM),
    ("highest", "highest_m", 1),
)
_UNITS = {"climb": "m", "length": "km", "highest": "m"}
_TOGGLES = ("summit", "waterside", "round_trip")


@dataclass(frozen=True)
class HikeFilter:
    """Ranges for climb (m), length (km) and highest point (m), and yes-only toggles.

    A limit left None is open; a toggle left False is off. A range keeps both its ends. A hike
    without the fact fails a limit on it.
    """

    climb_min_m: float | None = None
    climb_max_m: float | None = None
    length_min_km: float | None = None
    length_max_km: float | None = None
    highest_min_m: float | None = None
    highest_max_m: float | None = None
    summit: bool = False
    waterside: bool = False
    round_trip: bool = False

    def is_empty(self) -> bool:
        return not self.to_mapping()

    def to_mapping(self) -> dict[str, float | bool]:
        """The filter as a link carries it (ADR-0073): only the limits set."""
        return {
            f.name: value
            for f in fields(self)
            if (value := getattr(self, f.name)) is not None and value is not False
        }

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object] | None) -> HikeFilter:
        """The filter a link names. Unknown keys and values that are no limit are refused."""
        mapping = mapping or {}
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(mapping) - known)
        if unknown:
            raise ValueError(f"unknown hike filter(s): {', '.join(unknown)}")
        for name, value in mapping.items():
            if name in _TOGGLES:
                if not isinstance(value, bool):
                    raise ValueError(f"{name} is true or false, not {value!r}")
            elif (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} is a number of 0 or more, not {value!r}")
        for stem, _, _ in _RANGES:
            low = mapping.get(f"{stem}_min_{_UNITS[stem]}")
            high = mapping.get(f"{stem}_max_{_UNITS[stem]}")
            if low is not None and high is not None and low > high:
                raise ValueError(f"{stem} runs from {low} to {high}")
        return cls(**mapping)  # type: ignore[arg-type]


def hike_mask(store: SimilarityStore, hike_filter: HikeFilter) -> np.ndarray:
    """Yes/no per hike of the store: does it pass every limit of the filter."""
    mask = np.ones(len(store.ids), dtype=bool)
    for stem, column, factor in _RANGES:
        low = getattr(hike_filter, f"{stem}_min_{_UNITS[stem]}")
        high = getattr(hike_filter, f"{stem}_max_{_UNITS[stem]}")
        values = store.column(column)
        if low is not None:
            mask &= values >= low * factor
        if high is not None:
            mask &= values <= high * factor
    if hike_filter.summit:
        mask &= store.column("peak_summited") == 1
    if hike_filter.waterside:
        mask &= store.column("waterside_share") >= _WATERSIDE_MIN_SHARE
    if hike_filter.round_trip:
        mask &= store.column("round_trip") == 1
    return mask
