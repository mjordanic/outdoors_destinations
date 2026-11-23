"""Every hike name in a Catalog is unique (ADR-0068).

Ingest labels each DayHike by its own rules (ADR-0017, ADR-0066, ...), then
``unique_names`` makes the labels of the whole build unique, whatever kind of
hike they belong to:

- A placeholder name (``is_placeholder``: "Road", "Trail", "Forest Route 2165",
  "392"; and the "Hike" of ingest's "Hike near Somers") says nothing about where a hike is. It becomes
  "Hike near {place}", the place as below, with the name in brackets as its
  code when it holds a number: "Hike near Idyllwild (Forest Route 2165)".
- A name two or more hikes share keeps its words and adds where each starts:
  "Blue Trail near Somers".
- The place is, first, the nearest town, village or hamlet within
  ``PLACE_RADIUS_M`` of the Trailhead; else the nearest named Peak, Hut,
  Viewpoint or lake within it; else the nearest town at any distance. A
  hike whose label still collides tries the next of these.
- Labels still shared after that are numbered, in id order: "Hike near
  Somers (1)", "Hike near Somers (2)".

A name no other hike has, and that is no placeholder, is kept as it is.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import replace

from dayhike.catalog import PLACE_RADIUS_M, DayHike, named_points

# A name of only these words (and codes, see ``_is_code``) names no hike by itself: real examples from the
# US named-trail preview ("Road", "Trail", "Forest Route 2165", "FS Road 5608-210", "National Forest
# Development Road 70", "Connector", "Firebreak", "Jeep Road") and ingest's own "Hike near {place}".
PLACEHOLDER_WORDS = frozenset(
    {
        "road", "rd", "trail", "tr", "trl", "path", "footpath", "foot", "track", "loop", "route", "way", "walk",
        "hike", "connector", "spur", "cutoff", "shortcut", "firebreak", "fire", "access", "jeep", "logging",
        "service", "forest", "national", "development", "fs", "fr", "nf", "nfs", "cr", "sr", "old", "unnamed",
        "bridle", "section", "segment",
    }
)
_NEAR = " near "


def is_placeholder(name: str) -> bool:
    """Whether ``name`` is only ``PLACEHOLDER_WORDS`` and codes (numbers, "700D", single letters).
    A name that starts with a code and has a word too is a name ("1777 Trail", "4T Trail");
    a designation puts its number last ("Trail 122", "Forest Route 2165")."""
    tokens = re.findall(r"\w+", name.casefold())
    words = [word for word in tokens if not _is_code(word)]
    if tokens and words and _is_code(tokens[0]):
        return False
    return bool(name.strip()) and all(word in PLACEHOLDER_WORDS for word in words)


def _is_code(word: str) -> bool:
    return len(word) == 1 or any(character.isdigit() for character in word)


def unique_names(
    hikes: Sequence[DayHike],
    towns: Sequence[tuple[float, float, str]],
    landmarks: Sequence[tuple[float, float, str]],
) -> list[DayHike]:
    """``hikes`` with unique labels (compared without case), each hike in its place.

    ``towns`` are the (lat, lon, name) of named settlements, ``landmarks`` of
    named Peaks, Huts, Viewpoints and lakes. See the module docstring."""
    counts = Counter(hike.name.casefold() for hike in hikes)
    todo = [index for index, hike in enumerate(hikes) if counts[hike.name.casefold()] > 1 or is_placeholder(hike.name)]
    if not todo:
        return list(hikes)
    points = [(hikes[index].trailhead.latitude, hikes[index].trailhead.longitude) for index in todo]
    town_tree = named_points(towns)
    lookups = [(town_tree, PLACE_RADIUS_M), (named_points(landmarks), PLACE_RADIUS_M), (town_tree, math.inf)]
    found = [[None] * len(todo) if tree is None else tree.nearest_each(points, radius) for tree, radius in lookups]
    labels = [hike.name for hike in hikes]
    options: dict[int, list[str]] = {}
    located: set[int] = set()  # hikes whose first label already names where they are
    for position, index in enumerate(todo):
        places = list(dict.fromkeys(place for column in found if (place := column[position]) is not None))
        options[index], names_a_place = _options(hikes[index].name, places)
        if names_a_place:
            located.add(index)
    level = dict.fromkeys(options, 0)
    # A unique name that no rule changes is never changed to make room for another.
    fixed = {
        index
        for index, choices in options.items()
        if counts[labels[index].casefold()] == 1 and choices[0] == labels[index]
    }
    while True:
        for index in options:
            labels[index] = options[index][level[index]]
        moved = False
        for members in _shared(labels).values():
            movable = [i for i in members if i in level and i not in fixed and level[i] + 1 < len(options[i])]
            after = {options[i][level[i] + 1].casefold() for i in movable}
            after |= {labels[i].casefold() for i in members if i not in movable}
            # Hikes that already say where they are and would all move to one other place stay, to be numbered.
            if not movable or (len(after) == 1 and all(i in located or level[i] for i in movable)):
                continue
            for index in movable:
                level[index] += 1
            moved = True
        if not moved:
            break
    taken = {label.casefold() for label in labels}
    for members in _shared(labels).values():
        keep = [index for index in members if index not in options or index in fixed]
        number = 1 if not keep else 2
        for index in sorted(set(members) - set(keep[:1]), key=lambda index: hikes[index].id):
            while f"{labels[index]} ({number})".casefold() in taken:
                number += 1
            labels[index] = f"{labels[index]} ({number})"
            taken.add(labels[index].casefold())
            number += 1
    return [hike if hike.name == label else replace(hike, name=label) for hike, label in zip(hikes, labels)]


def _options(name: str, places: Sequence[str]) -> tuple[list[str], bool]:
    """The labels a hike named ``name`` may take, in order, ``places`` the
    places near it, nearest kind first; and whether the first already names a place."""
    base = name
    for place in places:
        if name.endswith(_NEAR + place):
            base = name[: -len(_NEAR + place)]
            break
    if is_placeholder(base):
        code = f" ({base})" if any(_is_code(word) for word in re.findall(r"\w+", base.casefold())) else ""
        choices = [f"Hike near {place}{code}" for place in places]
    else:
        choices = [name, *(f"{base}{_NEAR}{place}" for place in places)]
    return list(dict.fromkeys(choices)) or [name], bool(places) and (base != name or is_placeholder(base))


def _shared(labels: Sequence[str]) -> dict[str, list[int]]:
    """The indices of each label two or more hikes have, by label without case."""
    by_label: dict[str, list[int]] = defaultdict(list)
    for index, label in enumerate(labels):
        by_label[label.casefold()].append(index)
    return {label: members for label, members in by_label.items() if len(members) > 1}
