"""The serving export: the small read-only dataset the web demo answers from.

``export_serving`` runs once where the Catalog, its GeometryStore and the
similarity directory live. It writes a directory a server memory-maps:

- ``similarity/``: a copy of the Catalog's similarity directory (ADR-0013).
- ``trailheads.npy``: Trailhead latitude and longitude, one row per DayHike,
  in the similarity ``ids`` order.
- ``hikes.sqlite``: per DayHike its name, the DayHikeGeometry simplified to
  about ``LINE_TOLERANCE_M`` (Google polyline), its ElevationProfile in true
  metres at ``ELEVATION_POINTS`` equal steps, the metres it climbs and
  descends in the walking direction, and the amenity points the
  Measures count (along the line, Peaks around it, at the Trailhead) as
  zlib-compressed JSON (``POINT_COLUMNS`` per point); and ``hike_boxes``, an
  R*Tree of each simplified line's bounding box, for the hikes in a map view.
- ``trailgraph.sqlite``: the Catalog's TrailGraph (ADR-0018), each Segment
  with its line simplified like the hikes' and the metres it climbs each way,
  when the Catalog has one: the page composes hikes from it, and its OSM node
  and way ids resolve a shared link.
- ``trailgraph.network/``: the network a way back is searched on (``TrailGraph.write_network``),
  as ``.npy`` arrays a server memory-maps, with the TrailGraph.
- ``snaps.npz``: highway snaps of every Trailhead (``HighwaySnaps``), when a
  snap file or a Valhalla URL is given.
- ``valhalla/``: the highways-only tile extract and a ``valhalla.json`` that
  serves it from ``SERVING_ROOT`` inside the container, when given.
- ``places.sqlite``: GeoNames towns of 1,000 people or more within
  ``PLACE_MARGIN_DEG`` of the Trailheads' box, each under every folded name it
  has, and smaller villages within ``VILLAGE_M`` of a Trailhead under their own
  names, for the page's place search, when a GeoNames dump (``allCountries``)
  is given. ``export_places`` writes it beside an existing export too: it reads
  only ``trailheads.npy``, and an export without it still opens.
- ``landmarks.sqlite``: the Landmarks the hike search finds hikes by
  (``export_landmarks``, ADR-0053), each with its nearest hikes; written by
  every export and by ``export_places``. An export without it still opens
  and searches hike names only.
- ``meta.json``: written last, so a directory without it is incomplete.

``ServingData`` opens such a directory without ``Catalog.open``: name search,
place search, the hikes in a map view, hike detail and Ranking all read the memory-mapped arrays and the SQLite file.
The Segments in a map view and a ComposedHike's detail read ``trailgraph.sqlite``; a Ranking
from a ComposedHike or a Wish scores its facts against every DayHike like any Seed's. Detail, pair and
Ranking measure every hike with its way back to the start, or every hike one way (ADR-0019).
With a TrailGraph a Ranking also searches it for SuggestedLoops near its best DayHikes and
lists them among them (``dayhike.loops``, ADR-0020).
"""

from __future__ import annotations

import functools
import heapq
import itertools
import json
import math
import os
import re
import shutil
import sqlite3
import threading
import time
import unicodedata
import zipfile
import zlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from urllib.request import urlretrieve as default_urlretrieve

import numpy as np
import shapely

from dayhike.catalog import (
    EARTH_RADIUS_M,
    KIND_RELATION,
    MAX_CAR_SPEED_MS,
    NAME_ORIGIN_SYNTHETIC,
    MatrixClient,
    Origin,
    Trailhead,
    geometry_store_path_for,
    similarity_dir_for,
    straight_line_distances_m,
    trailgraph_path_for,
)
from dayhike.dem import FABDEM_ATTRIBUTION
from dayhike.extracts import UrlRetrieve
from dayhike.loops import LOOP_SEEDS, LOOP_TRAILHEADS, LoopTarget, find_loops
from dayhike.similarity import (
    ALONG_KINDS,
    CALIBRATION_PAIRS,
    CORRIDOR_M,
    FACT_COLUMNS,
    MEASURES,
    PEAKS_AROUND_M,
    PROFILE_POINTS,
    SEARCH_STEP_M,
    TRAILHEAD_M,
    TWIN_M,
    TWIN_SHARE,
    TWIN_STEP_M,
    AmenityPoints,
    OutsideSeed,
    SimilarityStore,
    StaleSimilarity,
    _calibration_key,
    _check_climate,
    _densify,
    _even_points,
    _haversine_m,
    _measure_weights,
    _path_length_m,
    _resample,
    _step_m,
    are_twins,
    calibrate,
    climb_and_descent,
    measure_rarity,
    nearest_on_line,
    random_pair_shares,
    rank_rows,
    ranked_scores,
    wish_unmeasured,
    score_rows,
)
from dayhike.trailgraph import (
    TRAILHEAD_KINDS,
    WAY_BACK_KINDS,
    ComposedWalk,
    Route,
    TrailGraph,
    WayBack,
    _unit,
    decode_coords,
    round_trip,
    way_back,
)

Progress = Callable[[str], None]
Reach = Callable[[list[tuple[float, float]]], list[float | None]]  # drive hours to places, None out of reach
Seed = int | Route | Mapping[str, float]  # a DayHike id, a ComposedHike, or a Wish (Measure name to value)

SERVING_FORMAT = 5
SERVING_ROOT = "/data"  # where the container finds the export (web/Dockerfile)
LINE_TOLERANCE_M = 10.0
LINE_END_M = 2.0  # a served line's end lies this close to the TrailGraph vertex it was (1e-5 degree kept)
ELEVATION_POINTS = 128
DRAWN_SMOOTH_M = 90.0  # a Copernicus GLO-30 ElevationProfile is drawn smoothed over this (ADR-0030) ...
DRAWN_STEP_M = 10.0  # ... resampled this often first
LOOP_MEASURED = 40  # the SuggestedLoops guessed best that a Ranking measures exactly
LOOP_BUDGET_S = 5.0  # the most a Ranking spends on SuggestedLoops (measured on 1 CPU: ADR-0036)
LOOP_SEARCH_S = 3.5  # of which the search for them at most; the rest measures the loops it found
LOOP_SEED_SHARE = 0.5  # a loop sharing more of its walk with the Seed, or of the Seed's, is no suggestion
GROUP_M = 10_000.0  # hikes starting closer than this to a better one are its versions, one row
# The grouping radius a Ranking takes by default follows its search limit (ADR-0032): this share of
# the distance limit, or of the drive-time limit at GROUP_KMH straight-line km per hour, the smaller
# of the two, at least GROUP_MIN_M; with no limit, GROUP_M.
GROUP_SHARE = 0.05
GROUP_MIN_M = 500.0
GROUP_KMH = 50.0  # the 4 h longest drive (web.MAX_DRIVE_HOURS) then groups as no limit does
VERSIONS = 3  # versions per row, the row's own included
VERSION_DIFFERS = 0.5  # a version walks more than this share of its length off every other one
GOOD_MATCH = 0.05  # a hike scoring at most this below a row's leader is a good match, counted when not listed
DRAW_T = 0.005  # a draw takes a row with probability rising as exp(score / DRAW_T) (ADR-0047)
LOOP_DRAW_RANKS = 60  # a drawn loop search takes its LOOP_TRAILHEADS Trailheads from this many best DayHikes
LOOP_NAME_M = 2_000.0  # a loop is named after a named point this near its start
TRAIL_NAME_SHARE = 0.3  # a trail names a walk only when it is at least this share of the walk's length (ADR-0037)
UNNAMING_KINDS = ("restaurant_cafe", "parking")  # amenity points that name no hike: a cafe, a car park
TRAILHEAD_NEAREST = 3  # parking and restaurant/cafe points kept per Trailhead
VALHALLA_CACHE_BYTES = 256 * 1024 * 1024  # mjolnir.max_cache_size; the build default is 1 GB
SEARCH_SCAN_CAP = 5_000  # occurrences of the scanned word a search looks at, so short queries stay fast
FUZZY_MIN_LETTERS = 4  # a shorter query word is found only as typed
FUZZY_SPELLINGS = 3  # the nearest words a word with a typo is tried as
FUZZY_QUERIES = 6  # the respelled queries a search tries, fewest typos first
GEONAMES_URL = "https://download.geonames.org/export/dump/allCountries.zip"
GEONAMES_ATTRIBUTION = "GeoNames (geonames.org), CC BY 4.0"
PLACE_MARGIN_DEG = 2.0  # places this far outside the Trailheads' box are kept: a start a drive away
TOWN_POPULATION = 1_000  # a town (this many people, or a seat as below) is kept anywhere in the margin, as cities1000
SEAT_CODES = ("PPLC", "PPLA", "PPLA2", "PPLA3")  # GeoNames capitals and seats of administrative divisions
VILLAGE_M = 5_000  # a smaller populated place is kept this close to a Trailhead: a village a hike starts from
NOT_VILLAGE_CODES = ("PPLX", "PPLH", "PPLQ", "PPLW", "PPLCH")  # a section of a place; historical, abandoned, gone
LANDMARK_KINDS = ("peak", "hut", "shelter", "viewpoint")  # the named points on hikes a Landmark is made of (ADR-0053)
# GeoNames codes a Landmark is made of too (ADR-0055): lakes and passes the points lack, and the mountains
# the points miss (a summit no hike passes within 2 km of, a ridge no OSM Peak names)
GEONAMES_LANDMARKS = {
    "LK": "lake", "LKS": "lake", "RSV": "lake", "PASS": "pass",
    "MT": "peak", "PK": "peak", "PKS": "peak", "RDGE": "ridge",
}  # fmt: skip
LANDMARK_NEAR_M = 2_000.0  # a GeoNames Landmark is on the hikes passing this close to its GeoNames point ...
LANDMARK_FAR_M = 10_000.0  # ... else, with none, on those passing this close: a big lake's shore, a summit no trail reaches
LANDMARK_MERGE_M = 1_500.0  # a GeoNames peak this near an OSM Peak of one of its names is that Peak, under all its names
MULTI_DAY_M = 40_000.0  # a hike longer than this one way is a multi-day route (ADR-0056)
LANDMARK_HIKES = 5  # the hikes a Landmark lists, nearest first
LANDMARK_TOWN_M = 30_000.0  # a Landmark is told apart by the nearest town this close
_HIKE_CHUNK = 2_000
_RASTER_FACTS = ("tree_share", "temperature_c", "southness", "waterside_share")  # read from rasters
_SIMILARITY_FILES = (
    "ids.npy", "facts.npy", "profile.npy", "twins.npy", "quantiles.npy", "values.npy", "rarity.npy",
    "facts_back.npy", "profile_back.npy", "quantiles_back.npy", "values_back.npy", "rarity_back.npy",
    "way_back.npy", "meta.json",
)  # fmt: skip

# Groups are sections of the weights panel only; scoring knows no groups (ADR-0013). The page
# shows the "basic" group and folds the others away behind Advanced search (ADR-0031).
MEASURE_INFO: dict[str, tuple[str, str, str, str]] = {
    "length": ("basic", "Length", "Walk length; scored by the shorter over the longer (floor 1 km), against random hike pairs.", "OpenStreetMap: the hike's line, from the ways of its hiking relation."),
    "elevation_gain": ("basic", "Climbing", "Metres climbed, the larger of climb and descent; scored by the smaller over the larger (floor 100 m), against random hike pairs. Any climb of 100 m or less counts as the same: so many hikes share that, it says little, and two hikes that match closely on a less common climb, like 166 m and 167 m, are the closer match.", "FABDEM elevation along the line."),
    "difficulty": ("effort", "Difficulty", "Strictest SAC grade, 1-6; scored by the gap against random hike pairs, lowest from 3 grades apart.", "OpenStreetMap: the sac_scale grade of the hike's ways."),
    "highest": ("basic", "Highest point", "Highest elevation; scored by the gap against random hike pairs, lowest from 400 m apart.", "FABDEM elevation along the line."),
    "lowest": ("altitude", "Lowest point", "Lowest elevation; scored by the gap against random hike pairs, lowest from 400 m apart.", "FABDEM elevation along the line."),
    "profile": ("effort", "Profile shape", "Elevation profile scaled to 0-1 in length and height, walked either way.", "FABDEM elevation along the line."),
    "landcover": ("basic", "Landscape", "Shared share of tree, shrub, grass, bare, snow and other cover.", "ESA WorldCover 2021 land cover along the line."),
    "peak_summited": ("scenery", "Summit", "A Peak within 50 m of the line, or not; two hikes that both lack it are not compared on it.", "OpenStreetMap peaks."),
    "viewpoint_along": ("scenery", "Viewpoint along", "A Viewpoint within 100 m of the line, or not; two hikes that both lack it are not compared on it.", "OpenStreetMap viewpoints."),
    "peaks_around": ("scenery", "Peaks around", "Peaks within 2 km of the line; scored by the fewer over the more (floor 1), against random hike pairs. No peak and 1 peak count as the same: so many hikes share that, it says little, and two hikes that match closely on a less common count, like 12 and 13 peaks, are the closer match.", "OpenStreetMap peaks."),
    "temperature": ("basic", "Typical temperature", "Mean temperature (June-August, or the chosen month); scored by the gap against random hike pairs, lowest from 10 °C apart.", "CHELSA V2.1 climate normals, 1981-2010, averaged along the line."),
    "precipitation": ("weather", "Precipitation", "Mean monthly precipitation (or the chosen month's); scored by the smaller over the larger (floor 10 mm), against random hike pairs.", "CHELSA V2.1 climate normals, 1981-2010, averaged along the line."),
    "solar_radiation": ("weather", "Solar radiation", "Solar radiation (June-August, or the chosen month), kJ/m²/day; scored by the gap against random hike pairs, lowest from 5,000 apart.", "CHELSA V2.1 climate normals, 1981-2010, averaged along the line."),
    "southness": ("weather", "Sun-facing slopes", "Which way the slopes face: 1 south, 0 north, 0.5 east or west; a flat walk faces no way and counts 0.5 (from FABDEM); scored by the gap against random hike pairs.", "FABDEM slopes along the line."),
    "water_count": ("water", "Water sources", "WaterSources within 100 m of the line, counted up to 3; scored by the gap against random hike pairs.", "OpenStreetMap drinking water, springs, taps and water points."),
    "dry_stretch": ("water", "Dry stretch", "Longest walk between WaterSources; scored by the shorter over the longer (floor 1 km), against random hike pairs.", "OpenStreetMap drinking water, springs, taps and water points."),
    "waterside": ("basic", "Lake or sea", "Share of the walk within 200 m of sea, lake or wide river (WorldCover); scored by the gap against random hike pairs, a gap under 0.15 counting as none, lowest from 0.5 apart. Any share under 15 % counts as dry, the same as none: so many hikes are dry, it says little, and two hikes that match closely on a less common share, like 50 % and 55 %, are the closer match.", "ESA WorldCover 2021 permanent water (sea, lakes, wide rivers)."),
    "hut_along": ("facilities", "Hut along", "A Hut within 100 m of the line, or not; two hikes that both lack it are not compared on it.", "OpenStreetMap huts."),
    "restaurant_cafe_along": ("facilities", "Restaurant or cafe along", "A restaurant or cafe within 100 m of the line, or not; two hikes that both lack it are not compared on it.", "OpenStreetMap restaurants and cafes."),
    "shelter_along": ("facilities", "Shelter along", "A Shelter within 100 m of the line, or not; two hikes that both lack it are not compared on it.", "OpenStreetMap shelters."),
    "restaurant_cafe_trailhead": ("facilities", "Restaurant at trailhead", "A restaurant or cafe within 500 m of the Trailhead, or not; two hikes that both lack it are not compared on it.", "OpenStreetMap restaurants and cafes."),
    "parking_trailhead": ("facilities", "Parking at trailhead", "Parking within 500 m of the Trailhead, or not; two hikes that both lack it are not compared on it.", "OpenStreetMap parking."),
    "surface": ("trail", "Surface", "Shared share of paved, gravel, natural and rock surface.", "OpenStreetMap surface tags, else the kind of path or road."),
    "tarmac_approach": ("trail", "Tarmac approach", "Paved metres before the trail leaves the road; scored by the gap against random hike pairs, lowest from 2 km apart.", "OpenStreetMap surface tags, else the kind of path or road."),
    "round_trip": ("trail", "Round trip", "Ends within 50 m of the Trailhead, or not (always, with the way back).", "OpenStreetMap: the hike's line, from the ways of its hiking relation."),
    "climb_grade": ("effort", "Climb grade", "Gain over the length of the stretches that climb; scored by the smaller over the larger (floor 3 %), against random hike pairs. Any grade of 3 % or less counts as the same: so many hikes share that, it says little, and two hikes that match closely on a less common grade, like 12 % and 12.1 %, are the closer match.", "FABDEM elevation along the line."),
    "steepest_km": ("effort", "Steepest km", "Largest height change over 1 km; scored by the smaller over the larger (floor 50 m), against random hike pairs. Any change of 50 m or less counts as the same: so many hikes share that, it says little, and two hikes that match closely on a less common change, like 180 m and 182 m, are the closer match.", "FABDEM elevation along the line."),
}

# The Wish form's input for each Measure that takes a number (``WISHABLE`` but yes/no): unit,
# lowest, highest, step and default, in the units of the Measure's fact.
WISH_INPUTS: dict[str, tuple[str | None, float, float, float, float]] = {
    "length": ("m", 1_000, 2_000_000, 500, 10_000),
    "elevation_gain": ("m", 0, 100_000, 50, 600),  # multi-day tours climb tens of km (ADR-0035)
    "difficulty": ("SAC grade", 1, 6, 1, 2),
    "highest": ("m", 0, 5_000, 50, 1_500),
    "lowest": ("m", 0, 4_000, 50, 500),
    "peaks_around": ("peaks", 0, 50, 1, 5),
    "temperature": ("°C", -20, 30, 1, 18),
    "precipitation": ("mm", 0, 300, 5, 80),
    "solar_radiation": ("kJ/m²/day", 0, 25_000, 500, 16_000),
    "southness": (None, 0, 1, 0.05, 0.5),
    "water_count": ("sources", 0, 3, 1, 1),
    "dry_stretch": ("m", 0, 2_000_000, 500, 5_000),
    "waterside": ("share", 0, 1, 0.05, 0),  # dry, as most hikes are: on by default, it favours no lake (ADR-0057)
    "tarmac_approach": ("m", 0, 5_000, 100, 0),
    "climb_grade": ("share", 0, 0.5, 0.01, 0.1),
    "steepest_km": ("m", 0, 800, 10, 150),
}
# The unit the page shows a Wish value in, where it is not the fact's, and how many fact units make
# one: lengths up to multi-day tours are typed in km (ADR-0035), shares in % as the facts show them.
WISH_DISPLAY: dict[str, tuple[str, float]] = {
    "length": ("km", 1_000), "dry_stretch": ("km", 1_000), "waterside": ("%", 0.01), "climb_grade": ("%", 0.01),
}


# Why a ratio Measure counts two values as the same: both at or under its floor, in words.
FLOOR_TEXT = {
    "length": "both 1 km or shorter",
    "elevation_gain": "both 100 m or less",
    "peaks_around": "both 1 peak or none",
    "precipitation": "both 10 mm or less",
    "dry_stretch": "both 1 km or shorter",
    "climb_grade": "both 3 % or less",
    "steepest_km": "both 50 m or less",
}

# A DayHike with no data for a Measure a Wish asks a value of scores 0 there (ADR-0052).
UNMEASURED_NOTE = "No data for this hike: counts as 0 against the value you asked for"


def score_note(
    score: float | None, tied: float | None, same: str | None = None, pairs: str = "random hike pairs"
) -> str | None:
    """A calibrated Measure score in words, with the number the compare table
    shows for it (two decimals, half rounding up): the share of random hike
    pairs less alike. ``tied`` is the share of random hike pairs scoring the
    same raw score. ``same`` is "identical" when the two values are, else why
    the Measure counts them as the same (ADR-0022), or "not compared" when
    neither has an OSM flag's feature mapped. ``pairs`` names the random
    pairs compared: for an OSM flag, those where either has one, but every
    pair for a Wish's "no".
    """
    if same == "not compared":
        return "Neither has one mapped: not compared"
    if score is None or tied is None:
        return None
    percent = _percent(score)
    if same:
        alike = 100 - percent
        told = f"fewer than 1% of {pairs} are" if alike < 1 else f"{alike}% of {pairs} are"
        note = f"{'Identical' if same == 'identical' else f'Counted as the same ({same})'}; {told} this alike"
        return f"{note}, so it counts little" if alike >= 50 else note
    if percent >= 1:
        return f"More alike than {percent}% of {pairs}"
    if _percent(tied) >= 1:
        return f"Nothing in common on this measure, like {_percent(tied)}% of {pairs}"
    return f"Less alike than almost all {pairs}"


def _percent(share: float) -> int:
    """A share as the compare table shows it, two decimals with half rounding up, times 100."""
    return int(Decimal(share).quantize(Decimal("0.01"), ROUND_HALF_UP) * 100)


# What a score means, said to the Hiker: a short line beside the overall score and
# beside the Measure scores, and the fuller sections behind the page's "?" (ADR-0025).
# Every explanation of the score lives here and in MEASURE_INFO, so a change of the
# scoring rule changes the words in one place.
# What a typical random pair of hikes scores under the default weights: the page says a list whose best row
# scores below it holds nothing much alike (ADR-0054).
TYPICAL_PAIR = 0.43
SCORE_HELP = {
    "overall": "Average of the measure scores below, under your weights. Each one is the share of random hike "
    f"pairs less alike than these two. A typical pair scores about {TYPICAL_PAIR}; with the default weights, 0.75 beats "
    "99 random pairs in 100.",
    "measure": "Share of random hike pairs less alike than these two on this measure: 0.9 means only 1 pair "
    "in 10 is as alike; 0 means nothing in common.",
    "sections": (
        {
            "title": "How alike, and how telling",
            "text": f"For every measure, these two hikes are compared with {CALIBRATION_PAIRS:,} random pairs "
            "of hikes, drawn once from the whole catalog. A measure's score is the share of those random pairs "
            "that are less alike than these two. So it says how close the two hikes are, and also how rare it "
            "is to be that close: a match counts for more when fewer random pairs manage it. Two hikes whose "
            "lengths are within 2 % score 0.99 on length. Two hikes that are both never near open water score "
            "only 0.21 on Lake or sea: they are identical there, but 79 % of random pairs are too, so it tells "
            "little. A score of 0.5 means half of random pairs are at least as alike: an ordinary match. 0.9 "
            "means only 1 pair in 10 is; 0.99 means 1 in 100. 0 means the hikes have nothing in common on that "
            "measure: one passes a hut and the other does not, or their highest points are 400 m or more apart. "
            "In information terms, every halving of the share of random pairs that match this well adds one bit "
            "of evidence that the hikes are alike: 0.5 is 1 bit, 0.9 is about 3 and 0.99 about 7. The note "
            "under a score says it in words.",
        },
        {
            "title": "Rare shared values tell more",
            "text": "When two hikes have the same value, the score says how rare that value is: two hikes that "
            "both pass a mountain hut score 0.95, two that are both never near water 0.21. Values a measure "
            "counts as the same are one value: Lake or sea shares under 0.15, Water sources 3 or more, values at "
            "or under a measure's floor (walks under 1 km on Length). Many hikes share such a value, so it says "
            "little: two climbs of 166 m and 167 m are a closer match (1.00) than two climbs both under 100 m (0.88).",
        },
        {
            "title": "Having nothing mapped in common is not evidence",
            "text": "When neither hike has a hut, cafe, shelter, viewpoint, summit or parking mapped in "
            "OpenStreetMap, the two are not compared on that measure. A missing map entry may only mean that "
            "nobody mapped it, and most hikes lack these things anyway. The measure then shows a dash and is "
            "left out of their score; the other measures count instead. When both hikes have one, the score "
            "shows how unusual that is: two hikes that both pass a mountain hut score 0.95. A wish for none is "
            "your answer, not a missing entry, so a hike without one matches it, and that counts by how rare "
            "it is: most random pairs both lack a hut, so a wish for no hut scores a hike without one about "
            "0.16.",
        },
        {
            "title": "What does 0.1 mean when the hikes look similar?",
            "text": "A low number on one measure does not mean the hikes are unalike overall. It means that what "
            "they share on that measure is common, or that they are further apart on it than most pairs of "
            "hikes. For example, Kattegatstien and Kattegatkysten are both Danish coast walks, 56 % and 30 % "
            "of the way beside water. On Lake or sea they score 0.13. Most hikes never come near water, and two "
            "such hikes count as alike, so 87 % of random pairs are at least as alike there. Their overall "
            "score is still 0.75, as high as only 1 random pair in 100, because every other measure matches.",
        },
        {
            "title": "The overall score",
            "text": "The overall score is the average of the measure scores, weighted as you set them: only "
            "their ratios matter, and a measure with no data on either hike, or not compared, drops out; "
            "when you describe the hike you want, a hike with no data for a measure you asked for scores 0 "
            "on it instead. A "
            "typical random pair of hikes scores about 0.43. With the default weights, 0.75 beats 99 random "
            "pairs in 100, and the best match for a hike usually scores about 0.84.",
        },
        {
            "title": "Where the facts come from",
            "text": "OpenStreetMap: each hike's line and length, its SAC grade and trail surface, and the "
            "peaks, viewpoints, huts, shelters, water sources, restaurants and parking along it or at its "
            "start. FABDEM, a 30 m elevation map with forests and buildings removed: heights, climb, "
            "profile shape, steepness and which way the slopes face. ESA WorldCover 2021: land cover and "
            "the share of the walk within 200 m of open water. CHELSA V2.1 climate normals for 1981-2010: "
            "temperature, precipitation and solar radiation, for June-August or the chosen month. "
            "With Return to start, every hike is measured with its way back to the start. "
            "A measure's ? names the one it uses.",
        },
        {
            "title": "How the scores are made",
            "text": "Each fact is measured on both hikes. Each measure compares the two values: the shorter "
            "over the longer, the gap, the shared part of a mix, the two profiles point by point, or whether "
            f"two yes/no facts agree. That comparison is ranked among the {CALIBRATION_PAIRS:,} random pairs, "
            "measured the same way (and in the chosen month, for climate): the score is the share of them "
            "that compare worse, and among pairs that compare the same, those sharing a commoner value count "
            "as less alike.",
        },
    ),
}


def name_dem(text: str, attribution: Mapping[str, str]) -> str:
    """``text`` naming the elevation map an export was sampled from (ADR-0030).

    MEASURE_INFO and SCORE_HELP name FABDEM, the default. An export sampled
    from Copernicus GLO-30 carries its attribution, and the texts name it.
    """
    if "copernicus" not in attribution:
        return text
    return text.replace(
        "FABDEM, a 30 m elevation map with forests and buildings removed",
        "Copernicus GLO-30, a 30 m elevation map of the surface, tree tops and roofs included",
    ).replace("FABDEM", "Copernicus GLO-30")


MEASURE_GROUPS: dict[str, str] = {
    "basic": "Main features",
    "effort": "Effort and steepness",
    "altitude": "Altitude",
    "scenery": "Scenery",
    "facilities": "Facilities",
    "trail": "Trail",
    "water": "Water",
    "weather": "Weather",
}
POINT_COLUMNS = ("kind", "where", "lat", "lon", "along_m", "off_m", "name", "ele")


def drawn_profile(
    lat: np.ndarray, lon: np.ndarray, ele: np.ndarray, *, smooth: bool
) -> np.ndarray | None:
    """The ElevationProfile the page draws: metres at ``ELEVATION_POINTS``
    equal steps, None with fewer than two heights.

    ``smooth``, for a Copernicus GLO-30 export, first resamples the walk every
    ``DRAWN_STEP_M`` and fits a Savitzky-Golay quadratic over
    ``DRAWN_SMOOTH_M``, so what is left of the tree tops does not jag the line
    while climbs, tops and dips keep their shape. It is drawn only: climb,
    descent and the facts read the heights as sampled.
    """
    window = round(DRAWN_SMOOTH_M / DRAWN_STEP_M) // 2 * 2 + 1  # odd, in steps
    points = round(float(_step_m(np.asarray(lat, float), np.asarray(lon, float)).sum()) / DRAWN_STEP_M) + 1
    if not smooth or points < window:
        return _resample(lat, lon, ele, points=ELEVATION_POINTS)
    from scipy.signal import savgol_filter

    dense = _resample(lat, lon, ele, points=points)
    if dense is None:
        return None
    dense = savgol_filter(dense, window, 2, mode="interp")
    return np.interp(np.linspace(0, points - 1, ELEVATION_POINTS), np.arange(points), dense)


class ServingExportError(ValueError):
    """The inputs of a serving export do not fit together."""


def export_serving(
    catalog_path: Path | str,
    out_dir: Path | str,
    *,
    valhalla_dir: Path | str | None = None,
    snaps_path: Path | str | None = None,
    valhalla_url: str | None = None,
    geonames_path: Path | str | None = None,
    progress: Progress | None = None,
) -> Path:
    """Write the serving export of a Catalog into ``out_dir`` (see the module doc).

    Reads the Catalog, its GeometryStore and its similarity directory, all
    read-only. ``valhalla_dir`` holds the highways-only ``valhalla_tiles.tar``
    and the ``valhalla.json`` that built it; the tar is hard-linked when it
    can be (same disk), else copied. ``snaps_path`` seeds ``snaps.npz`` from an
    existing snap cache; with ``valhalla_url`` every Trailhead not in it is
    snapped now, so a server never has to. ``geonames_path`` is a GeoNames
    dump (``allCountries``, zip or txt) for ``places.sqlite``.
    """
    started = time.monotonic()
    catalog_path, out = Path(catalog_path), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "meta.json").unlink(missing_ok=True)
    catalog = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True)
    try:
        # A Catalog built before built DayHikes (ADR-0038) holds relations only.
        columns = {row[1] for row in catalog.execute("PRAGMA table_info(dayhikes)")}
        built = "d.kind, d.parts, d.route" if "kind" in columns else f"'{KIND_RELATION}', NULL, NULL"
        hikes = catalog.execute(
            "SELECT d.id, d.name, d.name_origin, d.difficulty, t.latitude, t.longitude, "
            f"{built} FROM dayhikes d JOIN trailheads t ON t.dayhike_id = d.id ORDER BY d.id"
        ).fetchall()
        attribution = dict(catalog.execute("SELECT key, value FROM catalog_meta"))["attribution"]
    finally:
        catalog.close()
    ids = np.array([row[0] for row in hikes], dtype=np.int64)
    try:
        store = SimilarityStore.open_for(catalog_path, ids)
    except StaleSimilarity as exc:
        raise ServingExportError(str(exc)) from exc
    _check_trailgraph_sampled(trailgraph_path_for(catalog_path), store)
    similarity = out / "similarity"
    similarity.mkdir(exist_ok=True)
    for name in _SIMILARITY_FILES:
        shutil.copyfile(similarity_dir_for(catalog_path) / name, similarity / name)
    trailheads = np.array([(row[4], row[5]) for row in hikes], dtype=np.float64).reshape(-1, 2)
    np.save(out / "trailheads.npy", trailheads)
    sources = _write_hikes(catalog_path, out / "hikes.sqlite", hikes, progress)
    compose = _write_trailgraph(trailgraph_path_for(catalog_path), out / "trailgraph.sqlite", progress)
    # A rerun without tiles or snaps drops the old ones: they may be for other tiles.
    if valhalla_dir is not None:
        _write_valhalla(Path(valhalla_dir), out / "valhalla")
    else:
        shutil.rmtree(out / "valhalla", ignore_errors=True)
    if snaps_path is not None or valhalla_url is not None:
        _write_snaps(out / "snaps.npz", trailheads, snaps_path, valhalla_url, progress)
    else:
        (out / "snaps.npz").unlink(missing_ok=True)
    if geonames_path is not None:
        export_places(geonames_path, out)
    else:
        (out / "places.sqlite").unlink(missing_ok=True)
    export_landmarks(out, geonames_path)
    # The elevation map in use: FABDEM, unless the GeometryStore was sampled from Copernicus GLO-30 (ADR-0030).
    elevation = {} if "copernicus" in sources else {"fabdem": FABDEM_ATTRIBUTION}
    meta = {
        "format": SERVING_FORMAT,
        "dayhikes": len(ids),
        "facts_version": json.loads((similarity / "meta.json").read_text())["facts_version"],
        "line_tolerance_m": LINE_TOLERANCE_M,
        "elevation_points": ELEVATION_POINTS,
        "attribution": {"osm": attribution, **elevation, **sources},
        "drive_time": (out / "valhalla" / "valhalla.json").is_file() and (out / "snaps.npz").is_file(),
        "compose": compose,
        "export_seconds": round(time.monotonic() - started, 1),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    return out


def _write_hikes(
    catalog_path: Path,
    path: Path,
    hikes: list[tuple],
    progress: Progress | None,
) -> dict[str, str]:
    """Write ``hikes.sqlite``; return the GeometryStore's source attributions."""
    path.unlink(missing_ok=True)
    geo = sqlite3.connect(f"file:{geometry_store_path_for(catalog_path)}?mode=ro", uri=True)
    db = sqlite3.connect(path)
    try:
        sources = {
            key.removesuffix("_attribution"): value
            for key, value in geo.execute("SELECT key, value FROM geometry_meta")
            if key.endswith("_attribution")
        }
        line_kinds = (
            (_amenity_points(geo, tuple(k for k in ALONG_KINDS if k != "peak")), CORRIDOR_M),
            (_amenity_points(geo, ("peak",)), PEAKS_AROUND_M),
        )
        trailhead_kinds = {kind: _amenity_points(geo, (kind,)) for kind in ("parking", "restaurant_cafe")}
        db.executescript(
            "PRAGMA journal_mode = OFF;"
            "CREATE TABLE hikes ("
            " id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_origin TEXT NOT NULL,"
            " sac_scale TEXT, walk_m REAL, line TEXT, elevation BLOB, climb_m REAL, descent_m REAL,"
            " points BLOB NOT NULL, kind TEXT NOT NULL, parts TEXT, route TEXT);"
            "CREATE VIRTUAL TABLE hike_boxes USING rtree(id, south, north, west, east)"
        )
        for start in range(0, len(hikes), _HIKE_CHUNK):
            chunk = hikes[start : start + _HIKE_CHUNK]
            walks = _vertices(geo, chunk[0][0], chunk[-1][0])
            rows, boxes = [], []
            for dayhike_id, name, name_origin, sac_scale, th_lat, th_lon, *built in chunk:
                points = _trailhead_points(trailhead_kinds, th_lat, th_lon)
                line = elevation = walk_m = climb_m = descent_m = None
                walk = walks.get(dayhike_id)
                if walk is not None and len(walk[0]) >= 2:
                    lat, lon, ele = walk
                    walk_m = float(_step_m(lat, lon).sum())
                    kept_lat, kept_lon = simplify_line(lat, lon, LINE_TOLERANCE_M)
                    line = encode_polyline(kept_lat, kept_lon)
                    boxes.append((dayhike_id, kept_lat.min(), kept_lat.max(), kept_lon.min(), kept_lon.max()))
                    profile = drawn_profile(lat, lon, ele, smooth="copernicus" in sources)
                    if profile is not None:
                        elevation = np.round(profile).astype("<i2").tobytes()
                        climb_m, descent_m = (round(v) for v in climb_and_descent(lat, lon, ele))
                    along = [p for kinds, radius in line_kinds for p in _line_points(kinds, lat, lon, radius)]
                    points = sorted(along, key=lambda point: point[4]) + points
                rows.append(
                    (dayhike_id, name, name_origin, sac_scale, walk_m, line, elevation, climb_m, descent_m, *built, points)
                )
            _name_points(geo, rows)
            db.executemany(
                "INSERT INTO hikes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (*row[:9], zlib.compress(json.dumps(row[-1], ensure_ascii=False).encode()), *row[9:12])
                    for row in rows
                ],
            )
            db.executemany("INSERT INTO hike_boxes VALUES (?, ?, ?, ?, ?)", boxes)
            if progress is not None:
                progress(f"serving export: {min(start + _HIKE_CHUNK, len(hikes))}/{len(hikes)} DayHikes")
        db.commit()
    finally:
        geo.close()
        db.close()
    return sources


def _check_trailgraph_sampled(source: Path, store: SimilarityStore) -> None:
    """Refuse a TrailGraph ``sample-trailgraph`` never ran on when the similarity
    facts read the rasters: its ComposedHikes would lack those facts."""
    if not source.is_file() or all(np.isnan(store.column(name)).all() for name in _RASTER_FACTS):
        return
    graph = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        unsampled = graph.execute("SELECT 1 FROM segments WHERE landcover IS NULL LIMIT 1").fetchone()
    finally:
        graph.close()
    if unsampled is not None:
        raise ServingExportError(
            f"{source}: the TrailGraph has no raster samples but the similarity facts do; "
            "run `dayhike sample-trailgraph`"
        )


def _write_trailgraph(source: Path, path: Path, progress: Progress | None) -> bool:
    """Copy the TrailGraph, adding each Segment's display line and
    its climb each way, and save the network its way back is searched on
    (``TrailGraph.write_network``); False (and no file) when the Catalog has no TrailGraph."""
    path.unlink(missing_ok=True)
    shutil.rmtree(path.with_name(f"{path.stem}.network"), ignore_errors=True)
    if not source.is_file():
        return False
    if progress is not None:
        progress("serving export: TrailGraph")
    graph = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        graph.execute("VACUUM INTO ?", (str(path),))
    finally:
        graph.close()
    db = sqlite3.connect(path)
    try:
        db.executescript(
            "ALTER TABLE segments ADD COLUMN line TEXT;"
            "ALTER TABLE segments ADD COLUMN climb_ab REAL;"
            "ALTER TABLE segments ADD COLUMN climb_ba REAL;"
        )
        updates = []
        for segment_id, coords, ele in db.execute("SELECT id, coords, ele FROM segments"):
            lat, lon = decode_coords(coords).T
            ele = np.frombuffer(ele, dtype="<f4").astype(float)
            climb_ab, climb_ba = climb_and_descent(lat, lon, ele)
            updates.append(
                (
                    encode_polyline(*simplify_line(lat, lon, LINE_TOLERANCE_M)),
                    None if math.isnan(climb_ab) else round(climb_ab),
                    None if math.isnan(climb_ba) else round(climb_ba),
                    segment_id,
                )
            )
        db.executemany("UPDATE segments SET line = ?, climb_ab = ?, climb_ba = ? WHERE id = ?", updates)
        db.commit()
        db.execute("VACUUM")
    finally:
        db.close()
    TrailGraph(path).write_network()
    return True


@dataclass
class _Kinds:
    """Amenity points of some kinds with their GeometryStore rowids."""

    points: AmenityPoints
    rowids: np.ndarray


def _amenity_points(geo: sqlite3.Connection, kinds: tuple[str, ...]) -> _Kinds:
    rows = []
    for kind in kinds:
        rows += [
            (rowid, lat, lon, kind)
            for rowid, lat, lon in geo.execute(
                "SELECT rowid, latitude, longitude FROM amenities WHERE kind = ?", (kind,)
            )
        ]
    return _Kinds(
        points=AmenityPoints([(lat, lon) for _, lat, lon, _ in rows], [kind for *_, kind in rows]),
        rowids=np.array([row[0] for row in rows], dtype=np.int64),
    )


def _vertices(
    geo: sqlite3.Connection, first_id: int, last_id: int
) -> dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """DayHikeGeometry (lat, lon, ele; NaN missing) of the DayHikes in an id range."""
    table = np.array(
        geo.execute(
            "SELECT dayhike_id, latitude, longitude, ifnull(ele, 'nan') FROM vertices "
            "WHERE dayhike_id BETWEEN ? AND ? ORDER BY dayhike_id, seq",
            (first_id, last_id),
        ).fetchall(),
        dtype=float,
    ).reshape(-1, 4)
    hike_ids, starts = np.unique(table[:, 0], return_index=True)
    ends = np.append(starts[1:], len(table))
    return {
        int(dayhike_id): (table[s:e, 1], table[s:e, 2], table[s:e, 3])
        for dayhike_id, s, e in zip(hike_ids, starts, ends)
    }


def _line_points(kinds: _Kinds, lat: np.ndarray, lon: np.ndarray, radius_m: float) -> list[list]:
    """The points of ``kinds`` within ``radius_m`` of the line.

    The export asks for what the Measures count: WaterSources, Huts,
    restaurants and cafes, Shelters and Viewpoints within ``CORRIDOR_M``, and
    Peaks within ``PEAKS_AROUND_M`` (the Measures summit those within 50 m).
    The name column holds a rowid until ``_name_points`` replaces it.
    """
    hits = kinds.points.rows_near(np.radians(_densify(lat, lon, SEARCH_STEP_M)), radius_m)
    off, along = nearest_on_line(lat, lon, kinds.points.points[hits, 0], kinds.points.points[hits, 1])
    found = []
    for hit, off_m, along_m in zip(hits, off, along):
        if off_m <= radius_m:
            point_lat, point_lon = kinds.points.points[hit]
            found.append(
                [kinds.points.kinds[hit], "line", round(point_lat, 5), round(point_lon, 5),
                 round(float(along_m)), round(float(off_m)), int(kinds.rowids[hit]), None]
            )  # fmt: skip
    return found


def _trailhead_points(kinds: Mapping[str, _Kinds], lat: float, lon: float) -> list[list]:
    """The nearest few parking and restaurant/cafe points within ``TRAILHEAD_M``."""
    found = []
    for kind, of_kind in kinds.items():
        (hits,), (distance,) = of_kind.points.tree.query_radius(
            np.radians([[lat, lon]]),
            r=TRAILHEAD_M / EARTH_RADIUS_M,
            return_distance=True,
            sort_results=True,
        )
        for hit, angle in list(zip(hits, distance))[:TRAILHEAD_NEAREST]:
            if hit >= len(of_kind.rowids):
                continue
            point_lat, point_lon = of_kind.points.points[hit]
            found.append(
                [kind, "trailhead", round(point_lat, 5), round(point_lon, 5), None,
                 round(float(angle) * EARTH_RADIUS_M), int(of_kind.rowids[hit]), None]
            )  # fmt: skip
    return found


def _name_points(geo: sqlite3.Connection, rows: list[tuple]) -> None:
    """Replace each point's rowid with its OSM name and ``ele`` tag (or None)."""
    wanted = sorted({point[6] for row in rows for point in row[-1]})
    tags: dict[int, dict] = {}
    for start in range(0, len(wanted), 900):
        batch = wanted[start : start + 900]
        for rowid, text in geo.execute(
            f"SELECT rowid, tags FROM amenities WHERE rowid IN ({','.join('?' * len(batch))})",
            batch,
        ):
            tags[rowid] = json.loads(text)
    for row in rows:
        for point in row[-1]:
            found = tags.get(point[6], {})
            point[6] = found.get("name")
            point[7] = _number(found.get("ele"))


def _number(value: object) -> float | None:
    try:
        number = float(str(value).replace(",", ".").removesuffix("m").strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def simplify_line(
    lat: np.ndarray, lon: np.ndarray, tolerance_m: float
) -> tuple[np.ndarray, np.ndarray]:
    """Douglas-Peucker on a flat plane around the line, keeping both ends."""
    lat0 = float(np.mean(lat))
    scale = math.cos(math.radians(lat0))
    xy = np.column_stack([np.radians(lon) * scale, np.radians(lat)]) * EARTH_RADIUS_M
    kept = shapely.get_coordinates(shapely.simplify(shapely.linestrings(xy), tolerance_m))
    return np.degrees(kept[:, 1] / EARTH_RADIUS_M), np.degrees(kept[:, 0] / EARTH_RADIUS_M / scale)


def walk_line(lat: np.ndarray, lon: np.ndarray, ele: np.ndarray) -> dict:
    """A walk for a GPX file: ``line`` simplified as a hike's line is, and
    ``ele`` the metres at each vertex kept (None where unknown)."""
    lat0 = float(np.mean(lat))
    scale = math.cos(math.radians(lat0))
    xyz = np.column_stack([np.radians(lon) * scale * EARTH_RADIUS_M, np.radians(lat) * EARTH_RADIUS_M, ele])
    kept = shapely.get_coordinates(shapely.simplify(shapely.linestrings(xyz), LINE_TOLERANCE_M), include_z=True)
    return {
        "line": encode_polyline(np.degrees(kept[:, 1] / EARTH_RADIUS_M), np.degrees(kept[:, 0] / EARTH_RADIUS_M / scale)),
        "ele": [None if math.isnan(z) else round(z) for z in kept[:, 2].tolist()],
    }


def encode_polyline(lat: np.ndarray, lon: np.ndarray) -> str:
    """Google encoded polyline, precision 5 (what Leaflet plugins and OSRM use)."""
    values = np.round(np.column_stack([lat, lon]) * 1e5).astype(np.int64)
    deltas = np.diff(values, axis=0, prepend=np.zeros((1, 2), dtype=np.int64)).ravel()
    out = []
    for delta in deltas.tolist():
        value = ~(delta << 1) if delta < 0 else delta << 1
        while value >= 0x20:
            out.append(chr((0x20 | (value & 0x1F)) + 63))
            value >>= 5
        out.append(chr(value + 63))
    return "".join(out)


def decode_polyline(text: str) -> list[tuple[float, float]]:
    """Inverse of ``encode_polyline``: (lat, lon) pairs."""
    numbers, value, shift = [], 0, 0
    for char in text:
        chunk = ord(char) - 63
        value |= (chunk & 0x1F) << shift
        shift += 5
        if chunk < 0x20:
            numbers.append(~(value >> 1) if value & 1 else value >> 1)
            value, shift = 0, 0
    coords = np.cumsum(np.array(numbers, dtype=np.int64).reshape(-1, 2), axis=0) / 1e5
    return [(float(a), float(b)) for a, b in coords]


def _write_valhalla(source: Path, target: Path) -> None:
    """Link the tile extract and write the config that serves it in the container."""
    target.mkdir(exist_ok=True)
    tar = target / "valhalla_tiles.tar"
    tar.unlink(missing_ok=True)
    try:
        os.link(source / "valhalla_tiles.tar", tar)
    except OSError:
        shutil.copyfile(source / "valhalla_tiles.tar", tar)
    config = json.loads((source / "valhalla.json").read_text())
    overrides = source / "mjolnir.drive-time.json"
    if overrides.is_file():
        config = _merged(config, json.loads(overrides.read_text()))
    config = _merged(
        config,
        {
            "mjolnir": {
                "tile_extract": f"{SERVING_ROOT}/valhalla/valhalla_tiles.tar",
                "tile_dir": f"{SERVING_ROOT}/valhalla/valhalla_tiles",
                "max_cache_size": VALHALLA_CACHE_BYTES,
            },
            "httpd": {"service": {"listen": "tcp://127.0.0.1:8002"}},
        },
    )
    (target / "valhalla.json").write_text(json.dumps(config, indent=1))


def _merged(base: dict, extra: Mapping) -> dict:
    """``jq -s '.[0] * .[1]'``: objects merge recursively, anything else replaces."""
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _merged(merged[key], value)
        else:
            merged[key] = value
    return merged


def _write_snaps(
    path: Path,
    trailheads: np.ndarray,
    seed: Path | str | None,
    valhalla_url: str | None,
    progress: Progress | None,
) -> None:
    from dayhike.highways import HighwaySnaps
    from dayhike.valhalla import DEFAULT_VALHALLA_URL

    if seed is not None and Path(seed).resolve() != path.resolve():
        shutil.copyfile(seed, path)
    snaps = HighwaySnaps(valhalla_url or DEFAULT_VALHALLA_URL, path)
    places = [(float(lat), float(lon)) for lat, lon in trailheads]
    if valhalla_url is None:
        missing = len({p for p in places if p not in snaps._known})
        if missing:
            raise ServingExportError(f"{missing} Trailheads have no snap; pass --valhalla-url")
        return
    if progress is not None:
        progress("serving export: snapping Trailheads onto the highway tiles")
    snaps.of(places)


def fetch_geonames(path: Path | str, urlretrieve: UrlRetrieve = default_urlretrieve) -> Path:
    """GeoNames' ``allCountries.zip`` at ``path``, downloaded first when it is not there."""
    path = Path(path)
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.name}.partial")  # a cut download is not taken for the file
        urlretrieve(GEONAMES_URL, str(partial))
        os.replace(partial, path)
    return path


def export_places(geonames_path: Path | str, out_dir: Path | str) -> Path:
    """Write ``places.sqlite`` into the export ``out_dir`` from a GeoNames dump (zip or txt).

    Keeps the populated places within ``PLACE_MARGIN_DEG`` of the box of
    ``trailheads.npy``: a town (``TOWN_POPULATION`` people or a seat) anywhere
    there, a smaller place only within ``VILLAGE_M`` of a Trailhead, and none
    of ``NOT_VILLAGE_CODES``. Each has its name, country, population and
    position, and an index of its names folded like ``_fold``, marked whether
    it is only an alternate name (not its name or ASCII name); a village keeps
    only its own two, as alternate names would take most of the file. Written
    to a temporary file first, so a server never opens half of one.
    """
    from scipy.spatial import cKDTree

    geonames_path, out = Path(geonames_path), Path(out_dir)
    trailheads = np.load(out / "trailheads.npy")
    (south, west), (north, east) = trailheads.min(axis=0) - PLACE_MARGIN_DEG, trailheads.max(axis=0) + PLACE_MARGIN_DEG
    near = cKDTree(_unit(trailheads[:, 0], trailheads[:, 1]))
    chord = 2 * math.sin(VILLAGE_M / (2 * EARTH_RADIUS_M))
    path, partial = out / "places.sqlite", out / "places.sqlite.partial"
    partial.unlink(missing_ok=True)
    db = sqlite3.connect(partial)
    try:
        db.execute(
            "CREATE TABLE places (id INTEGER PRIMARY KEY, name TEXT, country TEXT, population INTEGER, lat REAL, lon REAL)"
        )
        db.execute(
            "CREATE TABLE place_names (name TEXT, place INTEGER, alternate INTEGER, PRIMARY KEY (name, place)) "
            "WITHOUT ROWID"
        )
        for line in _geonames_lines(geonames_path):
            # geonameid, name, asciiname, alternatenames, latitude, longitude, feature class (6) and code (7),
            # country code (8), ... population (14)
            fields = line.rstrip("\n").split("\t")
            if fields[6] != "P":
                continue
            lat, lon = float(fields[4]), float(fields[5])
            if not (south <= lat <= north and west <= lon <= east):
                continue
            population = int(fields[14] or 0)
            town = population >= TOWN_POPULATION or fields[7] in SEAT_CODES
            if not town and (
                fields[7] in NOT_VILLAGE_CODES
                or not near.query_ball_point(_unit(lat, lon)[0], chord, return_length=True)
            ):
                continue
            place = int(fields[0])
            db.execute(
                "INSERT INTO places VALUES (?, ?, ?, ?, ?, ?)", (place, fields[1], fields[8], population, lat, lon)
            )
            own = {_fold(name).strip() for name in fields[1:3]}
            names = (own | ({_fold(name).strip() for name in fields[3].split(",")} if town else set())) - {""}
            db.executemany(
                "INSERT INTO place_names VALUES (?, ?, ?)", [(name, place, name not in own) for name in names]
            )
        db.commit()
    finally:
        db.close()
    os.replace(partial, path)
    return path


def _geonames_lines(path: Path) -> Iterator[str]:
    if path.suffix != ".zip":
        with path.open(encoding="utf-8") as lines:
            yield from lines
        return
    with zipfile.ZipFile(path) as archive:
        name = next(name for name in archive.namelist() if name.endswith(".txt"))
        with archive.open(name) as raw:
            for line in raw:
                yield line.decode("utf-8")


def export_landmarks(out_dir: Path | str, geonames_path: Path | str | None = None) -> Path:
    """Write ``landmarks.sqlite`` into the export ``out_dir``: the Landmarks a
    hike search finds hikes by (ADR-0053).

    A Landmark is a named point of ``LANDMARK_KINDS`` on the hikes of
    ``hikes.sqlite`` (a Peak within ``PEAKS_AROUND_M`` of the line, the rest
    within ``CORRIDOR_M``), with the hikes it is on; a place of
    ``places.sqlite``, when there is one, with the hikes starting within
    ``VILLAGE_M`` of it; and, from a GeoNames dump, a lake, pass or mountain
    (``GEONAMES_LANDMARKS``) with the hikes passing within ``LANDMARK_NEAR_M``,
    else within ``LANDMARK_FAR_M``; a GeoNames peak that is an OSM Peak
    (``LANDMARK_MERGE_M``) adds its names to that Peak's.
    Each keeps its ``LANDMARK_HIKES`` nearest hikes and how many it has, a Peak its height, the
    nearest town (``LANDMARK_TOWN_M``) to tell equal names apart, how many
    names it has (a famous place has one in many languages: ADR-0055), and an index
    of every folded name it has from each word on (Sorapiss finds Punta
    Sorapiss), marked whether it is the whole name: a point's OSM name, a
    place's names in ``places.sqlite``, a GeoNames Landmark's GeoNames names.
    Written to a temporary file first, like ``places.sqlite``.
    """
    from scipy.spatial import cKDTree

    out = Path(out_dir)
    found: dict[tuple, dict[int, float]] = {}  # (kind, name, lat, lon) -> hike id -> metres off
    names: dict[tuple, set[str]] = {}
    heights: dict[tuple, float] = {}  # a Peak's ele tag
    with sqlite3.connect(f"file:{out / 'hikes.sqlite'}?mode=ro&immutable=1", uri=True) as db:
        for dayhike_id, blob in db.execute("SELECT id, points FROM hikes"):
            for kind, where, lat, lon, _, off_m, name, ele in json.loads(zlib.decompress(blob)):
                if kind in LANDMARK_KINDS and where == "line" and name:
                    hikes = found.setdefault((kind, name, lat, lon), {})
                    hikes[dayhike_id] = min(off_m, hikes.get(dayhike_id, off_m))
                    if ele is not None and kind == "peak":
                        heights[(kind, name, lat, lon)] = ele
        for key in found:
            names[key] = {key[1]}
        if geonames_path is not None:
            _geonames_landmarks(Path(geonames_path), db, found, names, heights)
    towns, town_names = np.empty((0, 2)), []
    places = out / "places.sqlite"
    if places.is_file():
        trailheads = np.load(out / "trailheads.npy")
        ids = np.load(out / "similarity" / "ids.npy")
        near = cKDTree(_unit(trailheads[:, 0], trailheads[:, 1]))
        chord = 2 * math.sin(VILLAGE_M / (2 * EARTH_RADIUS_M))
        with sqlite3.connect(f"file:{places}?mode=ro&immutable=1", uri=True) as db:
            kept = db.execute("SELECT id, name, population, lat, lon FROM places").fetchall()
            spelled: dict[int, set[str]] = {}
            for name, place in db.execute("SELECT name, place FROM place_names"):
                spelled.setdefault(place, set()).add(name)
        for place, name, population, lat, lon in kept:
            rows = near.query_ball_point(_unit(lat, lon)[0], chord)
            if not rows:
                continue
            key = ("town" if population >= TOWN_POPULATION else "village", name, lat, lon)
            found[key] = {
                int(ids[row]): float(_haversine_m(lat, lon, *trailheads[row])) for row in rows
            }
            names[key] = spelled.get(place, set()) | {name}
        big = [(name, lat, lon) for _, name, population, lat, lon in kept if population >= TOWN_POPULATION]
        town_names = [name for name, _, _ in big]
        towns = np.array([(lat, lon) for _, lat, lon in big]).reshape(-1, 2)
    town_tree = cKDTree(_unit(towns[:, 0], towns[:, 1])) if len(towns) else None
    town_chord = 2 * math.sin(LANDMARK_TOWN_M / (2 * EARTH_RADIUS_M))
    path, partial = out / "landmarks.sqlite", out / "landmarks.sqlite.partial"
    partial.unlink(missing_ok=True)
    db = sqlite3.connect(partial)
    try:
        db.executescript(
            "CREATE TABLE landmarks (id INTEGER PRIMARY KEY, name TEXT, kind TEXT, town TEXT,"
            " lat REAL, lon REAL, ele REAL, hikes INTEGER, names INTEGER);"
            "CREATE TABLE landmark_hikes (landmark INTEGER, hike INTEGER, off_m INTEGER, PRIMARY KEY (landmark, off_m, hike))"
            " WITHOUT ROWID;"
            "CREATE TABLE landmark_names (name TEXT, landmark INTEGER, whole INTEGER, PRIMARY KEY (name, landmark))"
            " WITHOUT ROWID;"
        )
        for landmark, (key, hikes) in enumerate(sorted(found.items(), key=lambda item: item[0]), start=1):
            kind, name, lat, lon = key
            town = None
            if town_tree is not None and kind not in ("town", "village"):
                distance, nearest = town_tree.query(_unit(lat, lon)[0], distance_upper_bound=town_chord)
                town = town_names[nearest] if math.isfinite(distance) else None
            folded_names = {_fold(spelled_name).strip() for spelled_name in names[key]} - {""}
            db.execute(
                "INSERT INTO landmarks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (landmark, name, kind, town, lat, lon, heights.get(key), len(hikes), len(folded_names)),
            )
            nearest_hikes = sorted(hikes.items(), key=lambda item: (item[1], item[0]))[:LANDMARK_HIKES]
            db.executemany(
                "INSERT INTO landmark_hikes VALUES (?, ?, ?)",
                [(landmark, dayhike_id, round(off_m)) for dayhike_id, off_m in nearest_hikes],
            )
            keys: dict[str, bool] = {}
            for folded in folded_names:
                for match in re.finditer(r"\w+", folded):
                    tail = folded[match.start() :]
                    keys[tail] = keys.get(tail, False) or match.start() == 0
            db.executemany(
                "INSERT INTO landmark_names VALUES (?, ?, ?)", [(tail, landmark, whole) for tail, whole in keys.items()]
            )
        db.commit()
    finally:
        db.close()
    os.replace(partial, path)
    return path


def _geonames_landmarks(
    path: Path,
    hikes: sqlite3.Connection,
    found: dict[tuple, dict[int, float]],
    names: dict[tuple, set[str]],
    heights: dict[tuple, float],
) -> None:
    """Add the GeoNames Landmarks (``GEONAMES_LANDMARKS``) to ``found``: each with the hikes passing
    within ``LANDMARK_NEAR_M`` of it, else within ``LANDMARK_FAR_M``, none left out with neither. A
    peak within ``LANDMARK_MERGE_M`` of an OSM Peak of ``found`` named as one of its names is that Peak:
    its names join the Peak's (Monte Bianco, Gardasee), and its height is the Peak's when the Peak has none."""
    from scipy.spatial import cKDTree

    peaks = [key for key in found if key[0] == "peak"]
    peak_tree = cKDTree(_unit([key[2] for key in peaks], [key[3] for key in peaks])) if peaks else None
    merge_chord = 2 * math.sin(LANDMARK_MERGE_M / (2 * EARTH_RADIUS_M))
    lines: dict[int, np.ndarray] = {}  # decoded hike lines, kept while the GeoNames lines near them come
    for line in _geonames_lines(path):
        fields = line.rstrip("\n").split("\t")
        if fields[6] not in ("H", "T") or fields[7] not in GEONAMES_LANDMARKS:
            continue
        kind, lat, lon = GEONAMES_LANDMARKS[fields[7]], float(fields[4]), float(fields[5])
        spelled = {fields[1], fields[2], *(name for name in fields[3].split(",") if name)}
        # Its height, else the elevation model's at its point (-9999 off the model)
        ele = _number(fields[15]) if fields[15] else _number(fields[16]) if fields[16] not in ("", "-9999") else None
        if kind == "peak" and peak_tree is not None:
            folded = {_fold(name).strip() for name in spelled}
            same = [
                peaks[i]
                for i in peak_tree.query_ball_point(_unit(lat, lon)[0], merge_chord)
                if _fold(peaks[i][1]).strip() in folded
            ]
            for key in same:
                names[key] |= spelled
                if ele is not None:
                    heights.setdefault(key, ele)
            if same:
                continue
        near = _hikes_near(hikes, lat, lon, LANDMARK_NEAR_M, lines) or _hikes_near(hikes, lat, lon, LANDMARK_FAR_M, lines)
        if near:
            key = (kind, fields[1], lat, lon)
            found[key] = near
            names[key] = spelled
            if ele is not None and kind in ("peak", "ridge"):
                heights[key] = ele
        if len(lines) > 100_000:  # a few hundred MB: start again rather than keep every hike's line
            lines.clear()


def _hikes_near(
    hikes: sqlite3.Connection, lat: float, lon: float, metres: float, lines: dict[int, np.ndarray]
) -> dict[int, float]:
    """The hikes whose line passes within ``metres`` of a point, and how far, read from ``hikes.sqlite``:
    those whose line's box is that near, then those whose line is; their lines decoded once into ``lines``."""
    reach = math.degrees(metres / EARTH_RADIUS_M)
    across = reach / max(math.cos(math.radians(lat)), 0.01)
    ids = [
        dayhike_id
        for (dayhike_id,) in hikes.execute(
            "SELECT id FROM hike_boxes WHERE north >= ? AND south <= ? AND east >= ? AND west <= ?",
            (lat - reach, lat + reach, lon - across, lon + across),
        )
    ]
    missing = [dayhike_id for dayhike_id in ids if dayhike_id not in lines]
    for dayhike_id, encoded in _in_chunks(hikes, "SELECT id, line FROM hikes WHERE id IN ({})", missing):
        lines[dayhike_id] = np.array(decode_polyline(encoded))
    near = {}
    for dayhike_id in ids:
        chain = lines[dayhike_id]
        (off_m,), _ = nearest_on_line(chain[:, 0], chain[:, 1], [lat], [lon])
        if off_m <= metres:
            near[dayhike_id] = float(off_m)
    return near


class Ranking(list):
    """``ServingData.rank``'s rows, best first, and what they leave out: ``other_areas``,
    the hikes ranked that start in no row's area (later pages list them), and
    ``loop_search``, what the SuggestedLoop search did (None when none ran):
    ``starts`` searched from, loops ``found`` and measured, ``listed`` in all its rows and
    their versions (the whole pool; the page counts those in the rows it shows), the ``best``
    score of a loop found within the limits (None: none), and whether it ``stopped`` at ``LOOP_BUDGET_S``;
    and ``day_first``, whether its multi-day routes come after its day hikes (ADR-0056)."""

    def __init__(self, rows: list[dict], other_areas: int, loop_search: dict | None, day_first: bool = False) -> None:
        super().__init__(rows)
        self.other_areas = other_areas
        self.loop_search = loop_search
        self.day_first = day_first


class ServingData:
    """A serving export, opened read-only and memory-mapped."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        meta_path = self.directory / "meta.json"
        if not meta_path.is_file():
            raise ServingExportError(f"{self.directory}: no complete serving export; run `dayhike export-serving`")
        self.meta = json.loads(meta_path.read_text())
        if self.meta["format"] != SERVING_FORMAT:
            raise ServingExportError(
                f"{self.directory}: export format {self.meta['format']}, expected {SERVING_FORMAT}; "
                "rerun `dayhike export-serving`"
            )
        self.store = SimilarityStore.open_dir(self.directory / "similarity")
        graph = self.directory / "trailgraph.sqlite"
        self.trailgraph = TrailGraph(graph) if self.meta.get("compose") and graph.is_file() else None
        self.trailheads = np.load(self.directory / "trailheads.npy", mmap_mode="r")
        self._db_uri = f"file:{self.directory / 'hikes.sqlite'}?mode=ro&immutable=1"
        with self._db() as db:
            self._folded = [_fold(name) for (name,) in db.execute("SELECT name FROM hikes ORDER BY id")]
        # One string of every folded name between newlines: str.find walks it in C.
        self._haystack = "\n" + "\n".join(self._folded) + "\n"
        self._starts = np.cumsum([1] + [len(name) + 1 for name in self._folded[:-1]])
        self._spellings = _Spellings(self._folded)
        places = self.directory / "places.sqlite"
        self.places = f"file:{places}?mode=ro&immutable=1" if places.is_file() else None
        # An export from before Landmarks (ADR-0053) opens without them: the page searches hike names only.
        landmarks = self.directory / "landmarks.sqlite"
        self.landmarks = f"file:{landmarks}?mode=ro&immutable=1" if landmarks.is_file() else None
        self._landmark_names = "NULL"  # how many names a Landmark has: none in a file from before ADR-0055
        if self.landmarks is not None:  # about 2 s for Europe: read beside the first requests, not in one of them
            with sqlite3.connect(self.landmarks, uri=True) as db:
                if "names" in {row[1] for row in db.execute("PRAGMA table_info(landmarks)")}:
                    self._landmark_names = "l.names"
            threading.Thread(target=lambda: self._landmark_spellings, daemon=True).start()
        self._one_way_m = self.store.mode(False).column("length_m")  # what makes a hike multi-day (ADR-0056)

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_uri, uri=True)

    def row_of(self, dayhike_id: int) -> int:
        row = int(np.searchsorted(self.store.ids, dayhike_id))
        if row >= len(self.store.ids) or self.store.ids[row] != dayhike_id:
            raise KeyError(dayhike_id)
        return row

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """DayHikes whose name holds every word of the query, in any order;
        while they are fewer than ``limit``, then names that differ by a typo.

        Matching ignores case and accents and folds German ue/oe/ae to u/o/a.
        The best come first: the exact name, then names starting with the
        query, then names where every word starts a word, then the rest;
        shorter names first within each. A number also finds that DayHike id.
        Only the first SEARCH_SCAN_CAP occurrences, in id order, of the
        longest word (two letters or more) are considered.

        After them come the names that hold every word with up to
        ``_Spellings.typos`` of its letters wrong (a word of the names
        spelled so), fewest typos first: Triglov finds Triglav. These, and
        names that hold a word only inside a longer one (Rappenloch in
        Krappenlochklamm), are marked ``"match": "similar"``; the others
        ``"match": "words"``.
        """
        needle = _fold(query.strip())
        words = needle.split()
        rows: list[int] = []
        if needle.isascii() and needle.isdigit():
            try:
                rows.append(self.row_of(int(needle)))
            except KeyError:
                pass
        by_name = self._rows_with_words(words, needle, set(rows))
        rows += by_name
        similar = {row for row in by_name if _relevance(self._folded[row], needle, words) == 3}
        if len(rows) < limit:
            for spelled in self._spellings.respelled(words):
                found = self._rows_with_words(spelled, " ".join(spelled), set(rows))
                rows += found
                similar.update(found)
                if len(rows) >= limit:
                    break
        return [
            {**summary, "match": "similar" if row in similar else "words"}
            for row, summary in zip(rows[:limit], self._summaries(rows[:limit]), strict=True)
        ]

    def _rows_with_words(self, words: list[str], needle: str, skip: set[int]) -> list[int]:
        """Rows not in ``skip`` whose name holds every word, best first (see ``search``)."""
        scan = max(words, key=len, default="")
        if len(scan) < 2:
            return []
        found = [
            row
            for row in self._rows_containing(scan)
            if row not in skip and all(word in self._folded[row] for word in words)
        ]
        found.sort(key=lambda row: (_relevance(self._folded[row], needle, words), len(self._folded[row]), row))
        return found

    def find_place(self, query: str, limit: int = 5) -> list[dict]:
        """Places with a name starting with the query, folded like ``search``.

        A place named exactly the query comes first, then one with it as an
        alternate name (Wien is Vienna), then the most populous: so Krk is the
        town of Krk before Kraków, whose alternate names include Krk.
        """
        needle = _fold(query).strip()
        if self.places is None or not needle:
            return []
        with sqlite3.connect(self.places, uri=True) as db:
            rows = db.execute(
                "SELECT p.name, p.country, p.lat, p.lon, p.population FROM place_names n "
                "JOIN places p ON p.id = n.place WHERE n.name >= ? AND n.name < ? "
                "GROUP BY p.id ORDER BY MIN(CASE WHEN n.name = ? THEN n.alternate ELSE 2 END), "
                "p.population DESC, p.id LIMIT ?",
                (needle, needle + "\U0010ffff", needle, limit),
            ).fetchall()
        return [dict(zip(("name", "country", "lat", "lon", "population"), row)) for row in rows]

    def find_landmarks(self, query: str, limit: int = 5) -> list[dict]:
        """Landmarks (peaks, huts, lakes, towns ...) with a name holding the
        query from a word's start, each with its nearest hikes (ADR-0053).

        Folded like ``search``. The Landmark named exactly the query comes
        first, then one with it as its last words (Punta Sorapiss for
        Sorapiss), then names starting with it, then the rest. Within each
        the one with the most names comes first (a famous place has one in many
        languages: Lake Constance before the tarns named Bodensee, ADR-0055), then the
        highest, then the most hikes: Kleiner Watzmann in the Alps before Märkischer
        Watzmann, a hill near Berlin. When none is named
        so, the query is respelled as ``search`` respells it, from the
        Landmarks' words, and those found are marked ``"match": "similar"``.
        Each says where it is: the nearest town other than itself (``town``) and that town's
        ``country``, from ``places.sqlite`` (without it, the town the export named and no country).
        """
        needle = _fold(query).strip()
        if self.landmarks is None or len(needle) < 2:
            return []
        with sqlite3.connect(self.landmarks, uri=True) as db:
            found, match = self._landmarks_named(db, needle, limit, self._landmark_names), "words"
            if not found:
                for spelled in self._landmark_spellings.respelled(needle.split()):
                    found, match = self._landmarks_named(db, " ".join(spelled), limit, self._landmark_names), "similar"
                    if found:
                        break
            hikes: dict[int, list[tuple[int, int]]] = {}
            for landmark, dayhike_id, off_m in _in_chunks(
                db, "SELECT landmark, hike, off_m FROM landmark_hikes WHERE landmark IN ({}) ORDER BY landmark, off_m, hike",
                [row[0] for row in found],
            ):
                hikes.setdefault(landmark, []).append((dayhike_id, off_m))
        results = []
        for landmark, name, kind, town, lat, lon, count in found:
            nearest = hikes.get(landmark, [])
            summaries = self._summaries([self.row_of(dayhike_id) for dayhike_id, _ in nearest])
            country = None
            if self._towns[1] is not None:
                near = self._near_town(lat, lon)
                country = near and near[1]
                itself = near and near[0] == name and near[2] <= TRAILHEAD_M  # a town is near no other
                town = None if not near or itself else near[0]
            results.append(
                {
                    "name": name, "kind": kind, "town": town, "country": country, "lat": lat, "lon": lon,
                    "match": match, "hike_count": count,
                    "hikes": [{**summary, "off_m": off_m} for summary, (_, off_m) in zip(summaries, nearest, strict=True)],
                }
            )  # fmt: skip
        return results

    @staticmethod
    def _landmarks_named(db: sqlite3.Connection, needle: str, limit: int, names: str = "NULL") -> list[tuple]:
        """``names``: the column of how many names each has, or NULL (a file from before ADR-0055)."""
        return db.execute(
            "SELECT l.id, l.name, l.kind, l.town, l.lat, l.lon, l.hikes FROM landmark_names n "
            "JOIN landmarks l ON l.id = n.landmark WHERE n.name >= ? AND n.name < ? "
            "GROUP BY l.id ORDER BY MIN(CASE WHEN n.name = ? THEN 1 - n.whole ELSE 3 - n.whole END), "
            f"{names} DESC, l.ele IS NULL, l.ele DESC, l.hikes DESC, l.id LIMIT ?",
            (needle, needle + "\U0010ffff", needle, limit),
        ).fetchall()

    @functools.cached_property
    def _towns(self) -> tuple[list[tuple[str, str]], object | None]:
        """The towns of ``places.sqlite`` (``TOWN_POPULATION`` people or more), name and country,
        and a tree of where they are; none and None without the file."""
        from scipy.spatial import cKDTree

        if self.places is None:
            return [], None
        with sqlite3.connect(self.places, uri=True) as db:
            rows = db.execute(
                "SELECT name, country, lat, lon FROM places WHERE population >= ? ORDER BY id", (TOWN_POPULATION,)
            ).fetchall()
        if not rows:
            return [], None
        where = np.array([(lat, lon) for *_, lat, lon in rows])
        return [(name, country) for name, country, *_ in rows], cKDTree(_unit(where[:, 0], where[:, 1]))

    def _near_town(self, lat: float, lon: float) -> tuple[str, str, float] | None:
        """The town nearest a point within ``LANDMARK_TOWN_M``: name, country, metres; None with none."""
        towns, tree = self._towns
        if tree is None:
            return None
        chord, i = tree.query(_unit(lat, lon)[0], distance_upper_bound=2 * math.sin(LANDMARK_TOWN_M / (2 * EARTH_RADIUS_M)))
        return (*towns[i], 2 * EARTH_RADIUS_M * math.asin(chord / 2)) if math.isfinite(chord) else None

    def _region(self, row: int) -> str | None:
        """Where a hike starts, as the town nearest its Trailhead and its country ("Bovec, SI"); None with no town near."""
        return self._place_near(*map(float, self.trailheads[row]))

    def _place_near(self, lat: float, lon: float) -> str | None:
        """The town nearest a point and its country ("Bovec, SI"); None with no town near."""
        near = self._near_town(lat, lon)
        return f"{near[0]}, {near[1]}" if near else None

    def multi_day(self, row: int) -> bool:
        """Whether a hike is a multi-day route: longer than ``MULTI_DAY_M`` one way (ADR-0056)."""
        return bool(self._one_way_m[row] > MULTI_DAY_M)

    @functools.cached_property
    def _landmark_spellings(self) -> _Spellings:
        """The words of the Landmarks' names, read at the first query that needs them."""
        with sqlite3.connect(self.landmarks, uri=True) as db:
            return _Spellings([name for (name,) in db.execute("SELECT name FROM landmark_names WHERE whole")])

    def _rows_containing(self, word: str) -> list[int]:
        """Rows whose name holds ``word``, in id order, from its first SEARCH_SCAN_CAP occurrences."""
        hits = itertools.islice(re.finditer(re.escape(word), self._haystack), SEARCH_SCAN_CAP)
        offsets = np.fromiter((hit.start() for hit in hits), dtype=np.int64)
        return np.unique(np.searchsorted(self._starts, offsets, side="right") - 1).tolist()

    def _summaries(self, rows: list[int], return_to_start: bool = False) -> list[dict]:
        """Name and main figures per row, one way or with the way back."""
        store = self.store.mode(return_to_start)
        names = self._names([int(self.store.ids[row]) for row in rows])
        return [
            {
                "id": int(self.store.ids[row]),
                "name": names[int(self.store.ids[row])][0],
                "length_m": _value(store.column("length_m")[row]),
                "elevation_gain_m": _value(store.column("elevation_gain_m")[row]),
                **self._climb(row, names[int(self.store.ids[row])][1:], return_to_start),
                "highest_m": _value(store.column("highest_m")[row]),
                "difficulty_rank": _value(store.column("difficulty_rank")[row]),
                "round_trip": _value(store.column("round_trip")[row]),
                "trailhead": [float(v) for v in self.trailheads[row]],
                # One way, what makes it multi-day (ADR-0056), and where it starts: twins of a name told apart
                "walk_m": _value(self._one_way_m[row]),
                "multi_day": self.multi_day(row),
                "region": self._region(row),
            }
            for row in rows
        ]

    def _climb(self, row: int, one_way: tuple, return_to_start: bool) -> dict:
        """Climb and descent (metres, walking direction), one way or with the way back."""
        if not return_to_start:
            return {"climb_m": one_way[0], "descent_m": one_way[1]}
        _, climb_m, descent_m, _ = self.store.with_way_back.way_back[:, row]
        return {"climb_m": _round(climb_m), "descent_m": _round(descent_m)}

    def in_box(self, south: float, west: float, north: float, east: float, limit: int) -> tuple[list[dict], bool]:
        """The DayHikes whose line's bounding box meets the box, each with its id,
        name, walk length and line, and whether there are more than ``limit``;
        then none are listed.
        """
        with self._db() as db:
            found = db.execute(
                "SELECT h.id, h.name, h.name_origin, h.walk_m, h.line FROM hike_boxes b JOIN hikes h ON h.id = b.id "
                "WHERE b.north >= ? AND b.south <= ? AND b.east >= ? AND b.west <= ? LIMIT ?",
                (south, north, west, east, limit + 1),
            ).fetchall()
        if len(found) > limit:
            return [], True
        return [
            {"id": i, "name": _shown_name(i, name, origin), "walk_m": walk_m, "multi_day": self.multi_day(self.row_of(i)), "line": line}
            for i, name, origin, walk_m, line in found
        ], False

    def _names(self, ids: list[int]) -> dict[int, tuple[str, float | None, float | None]]:
        """Name, climb and descent (metres, walking direction) per DayHike id."""
        with self._db() as db:
            return {
                dayhike_id: (_shown_name(dayhike_id, name, origin), climb_m, descent_m)
                for dayhike_id, name, origin, climb_m, descent_m in db.execute(
                    f"SELECT id, name, name_origin, climb_m, descent_m FROM hikes WHERE id IN ({','.join('?' * len(ids))})",
                    ids,
                )
            }

    def detail(self, dayhike_id: int, return_to_start: bool = True) -> dict:
        """Everything the page plots for one DayHike, and the facts behind every
        Measure. With ``return_to_start`` the facts, climb and descent are of
        the walk with its way back, and ``way_back`` says how it gets back;
        the line and ElevationProfile stay the DayHike's own."""
        row = self.row_of(dayhike_id)
        store = self.store.mode(return_to_start)
        with self._db() as db:
            name, name_origin, sac_scale, walk_m, line, elevation, climb_m, descent_m, points, kind, parts, route = (
                db.execute(
                    "SELECT name, name_origin, sac_scale, walk_m, line, elevation, climb_m, descent_m, points, "
                    "kind, parts, route FROM hikes WHERE id = ?",
                    (dayhike_id,),
                ).fetchone()
            )
        profile = np.frombuffer(elevation, dtype="<i2").tolist() if elevation else None
        return {
            "id": dayhike_id,
            "name": _shown_name(dayhike_id, name, name_origin),
            "name_origin": name_origin,
            "sac_scale": sac_scale,
            "trailhead": [float(v) for v in self.trailheads[row]],
            "walk_m": walk_m,
            "multi_day": self.multi_day(row),
            "region": self._region(row),
            **self._climb(row, (climb_m, descent_m), return_to_start),
            "line": line,
            "elevation": None
            if profile is None
            else {"step_m": walk_m / (len(profile) - 1), "metres": profile},
            "points": [dict(zip(POINT_COLUMNS, point)) for point in json.loads(zlib.decompress(points))],
            "facts": {column: _value(store.facts[i, row]) for i, column in enumerate(FACT_COLUMNS)},
            "profile_shape": [_value(v) for v in store.profile[row]],
            "way_back": _way_back(*store.way_back[[0, 3], row]) if return_to_start else None,
            # A built DayHike (ADR-0038): what kind, the OSM relations it is built from, its Route.
            "built": None
            if kind == KIND_RELATION
            else {"kind": kind, "parts": json.loads(parts), "route": route},
        }

    def way_back_walk(self, dayhike_id: int) -> dict:
        """A DayHike's way back to its start as walked (ADR-0019), for its GPX:
        ``kind`` and ``m`` as ``detail`` gives them, and for a trail way back
        its ``line`` and ``ele`` (see ``walk_line``), found again on the
        TrailGraph between the line's ends; None for both when it is not
        (a step or a retrace the page draws from the hike's own line)."""
        row = self.row_of(dayhike_id)
        metres, kind = self.store.with_way_back.way_back[[0, 3], row]
        back = {**_way_back(metres, kind), "line": None, "ele": None}
        if back["kind"] != "trail" or self.trailgraph is None:
            return back
        with self._db() as db:
            (line,) = db.execute("SELECT line FROM hikes WHERE id = ?", (dayhike_id,)).fetchone()
        points = decode_polyline(line)
        ends = [self.trailgraph.vertex_near(lat, lon, LINE_END_M) for lat, lon in (points[0], points[-1])]
        if None in ends:
            return back
        (lat, lon), (end_lat, end_lon) = ends
        trail = self.trailgraph.trail_back(np.array([lat, end_lat]), np.array([lon, end_lon]), float(metres) + 1.0)
        return back if trail is None else {**back, **walk_line(trail.lat, trail.lon, trail.ele)}

    def segments_in_box(
        self, south: float, west: float, north: float, east: float, limit: int
    ) -> tuple[dict, bool]:
        """The TrailGraph Segments whose box meets the box, Links included, and
        their Junctions; whether there are more than ``limit``, and then none.

        A Segment is ``[id, kind, a, b, length_m, climb_ab, climb_ba, line,
        sac_scale, routes, step_ab, step_ba]``, routes the labels of its OSM
        relations and the steps how a ``Route`` names it walked from a and
        from b; a Junction is ``[id, lat, lon, degree, key]``.
        """
        empty = {"junctions": [], "segments": []}
        with sqlite3.connect(f"file:{self.trailgraph.path}?mode=ro", uri=True) as db:
            # The R*Tree alone first: a box over more than ``limit`` reads no Segment row and sorts nothing.
            ids = [
                row[0]
                for row in db.execute(
                    "SELECT id FROM segment_boxes WHERE north >= ? AND south <= ? AND east >= ? AND west <= ? LIMIT ?",
                    (south, north, west, east, limit + 1),
                )
            ]
            if len(ids) > limit:
                return empty, True
            found = sorted(
                _in_chunks(
                    db,
                    "SELECT id, kind, a, b, length_m, climb_ab, climb_ba, line, sac_scale, relation_ids, step_ab, "
                    "step_ba FROM segments WHERE id IN ({})",
                    ids,
                )
            )
            relation_ids = sorted({rel for row in found for rel in json.loads(row[9])})
            labels = dict(
                _in_chunks(db, "SELECT id, coalesce(name, ref) FROM relations WHERE id IN ({})", relation_ids)
            )
            ends = sorted({end for row in found for end in row[2:4]})
            junctions = _in_chunks(
                db, "SELECT id, latitude, longitude, degree, key FROM junctions WHERE id IN ({})", ends
            )
        segments = [
            [*row[:9], [labels[rel] for rel in json.loads(row[9]) if labels.get(rel)][:3], *row[10:]]
            for row in found
        ]
        return {"junctions": [list(row) for row in junctions], "segments": segments}, False

    def compose(self, route: Route, name: str | None = None, return_to_start: bool = True) -> dict:
        """The detail of a ComposedHike, in the shape of ``detail``: ``id`` is None
        and ``route`` holds the Route as text and its start Junction, Segment
        ids and their steps in this export (None where the walk cuts a Segment,
        as a link from an older build may). ``name`` is the Hiker's; without one it is proposed
        from the trails walked most and a named place at the start. ``return_to_start``
        measures it with its way back, as ``detail``."""
        walk = self.trailgraph.walk(route)
        trip, back = self._trip(walk, return_to_start)
        facts, shape = self.trailgraph.facts(trip)
        lat, lon, ele = walk.lat, walk.lon, walk.ele
        walk_m = float(_step_m(lat, lon).sum())
        line = elevation = climb_m = descent_m = None
        if len(lat) >= 2:
            line = encode_polyline(*simplify_line(lat, lon, LINE_TOLERANCE_M))
            profile = drawn_profile(lat, lon, ele, smooth="copernicus" in self.meta["attribution"])
            if profile is not None:
                elevation = {"step_m": walk_m / (ELEVATION_POINTS - 1), "metres": np.round(profile).astype(int).tolist()}
                climb_m, descent_m = (_round(v) for v in climb_and_descent(trip.lat, trip.lon, trip.ele))
        labels = self._labels(list(walk.relation_m))
        routes = list(dict.fromkeys(labels[rel] for rel in walk.relation_m if labels.get(rel)))
        return {
            "id": None,
            "route": {"text": str(route), "start": walk.start, "segments": walk.segments, "steps": walk.steps},
            "name": name or self._proposed_name(walk, labels),
            "name_origin": "hiker" if name else "proposed",
            "sac_scale": walk.sac_scale,
            "trailhead": [float(lat[0]), float(lon[0])],
            "walk_m": walk_m,
            "multi_day": walk_m > MULTI_DAY_M,
            "region": self._place_near(float(lat[0]), float(lon[0])),
            "climb_m": climb_m,
            "descent_m": descent_m,
            "line": line,
            "elevation": elevation,
            "points": self._composed_points(lat, lon),
            "facts": {column: _value(value) for column, value in facts.items()},
            "profile_shape": None if shape is None else [_value(v) for v in shape],
            "routes": routes,
            "way_back": None
            if back is None
            else {
                "m": back.metres,
                "kind": back.kind,
                **(walk_line(back.walk.lat, back.walk.lon, back.walk.ele) if back.walk is not None else {"line": None, "ele": None}),
            },
            "built": None,
        }

    def _labels(self, relation_ids: list[int]) -> dict[int, str]:
        """The name, else ref, of each OSM relation of the TrailGraph that has one."""
        with sqlite3.connect(f"file:{self.trailgraph.path}?mode=ro", uri=True) as db:
            return dict(_in_chunks(db, "SELECT id, coalesce(name, ref) FROM relations WHERE id IN ({})", relation_ids))

    def _trip(self, walk: ComposedWalk, return_to_start: bool) -> tuple[ComposedWalk, WayBack | None]:
        """The walk with its way back and the way back (ADR-0019), or the walk and None."""
        if not return_to_start:
            return walk, None
        back = way_back(walk.lat, walk.lon, self.trailgraph)
        return round_trip(walk, back), back

    def _proposed_name(self, walk: ComposedWalk, labels: dict[int, str]) -> str:
        """The two trails walked longest (in name order, so a longer walk on
        them keeps the name), and the named hiking place nearest the start
        within ``TRAILHEAD_M``: "Lake Loop and Summit Line from Summit Hut"."""
        trails = self._trails(walk, labels) or "Your hike"
        near = self._places_near_start(walk, TRAILHEAD_M)
        return f"{trails} from {near[0][1]}" if near else trails

    def _trails(self, walk: ComposedWalk, labels: dict[int, str]) -> str:
        """The two trails a walk walks longest, by name, joined by "and", of
        those that are at least ``TRAIL_NAME_SHARE`` of its length; "" with none."""
        main: dict[str, float] = {}
        for relation, metres in walk.relation_m.items():
            if labels.get(relation):
                main[labels[relation]] = main.get(labels[relation], 0.0) + metres
        least = TRAIL_NAME_SHARE * float(_step_m(walk.lat, walk.lon).sum())
        named = [label for label in sorted(main, key=lambda label: -main[label])[:2] if main[label] >= least]
        return " and ".join(sorted(named))

    def _places_near_start(self, walk: ComposedWalk, radius_m: float) -> list[tuple[float, str]]:
        """(metres, name) of the named amenity points within ``radius_m`` of a
        walk's start that a hiker would steer by, nearest first: no
        restaurant, cafe or parking (ADR-0022)."""
        lat, lon = float(walk.lat[0]), float(walk.lon[0])
        return sorted(
            (metres, row[4])
            for row in self.trailgraph.amenities_near(walk.lat[:1], walk.lon[:1], radius_m)
            if row[4] and row[1] not in UNNAMING_KINDS and (metres := _haversine_m(lat, lon, row[2], row[3])) <= radius_m
        )

    def _composed_points(self, lat: np.ndarray, lon: np.ndarray) -> list[dict]:
        """The amenity points a ComposedHike's detail shows, as ``_write_hikes`` picks them."""
        rows = self.trailgraph.amenities_near(lat, lon, PEAKS_AROUND_M)
        named = {row[0]: (row[4], _number(row[5])) for row in rows}

        def kinds(wanted: tuple[str, ...]) -> _Kinds:
            chosen = [row for row in rows if row[1] in wanted]
            return _Kinds(
                AmenityPoints([(row[2], row[3]) for row in chosen], [row[1] for row in chosen]),
                np.array([row[0] for row in chosen], dtype=np.int64),
            )

        points = []
        if len(lat) >= 2:
            along = tuple(kind for kind in ALONG_KINDS if kind != "peak")
            points = [p for wanted, radius in ((along, CORRIDOR_M), (("peak",), PEAKS_AROUND_M))
                      for p in _line_points(kinds(wanted), lat, lon, radius)]  # fmt: skip
            points.sort(key=lambda point: point[4])
        points += _trailhead_points({kind: kinds((kind,)) for kind in TRAILHEAD_KINDS}, float(lat[0]), float(lon[0]))
        for point in points:
            point[6], point[7] = named[point[6]]
        return [dict(zip(POINT_COLUMNS, point)) for point in points]

    def _seed(self, seed: Seed, return_to_start: bool) -> tuple[int | OutsideSeed, ComposedWalk | None]:
        """A DayHike's store row, or a ComposedHike's facts beside the store
        (with its way back when the DayHikes are; the DayHikes it retraces as its Twins)
        and its walk, or a Wish's values as facts (it walks nowhere)."""
        if isinstance(seed, Mapping):
            return OutsideSeed.wished(seed), None
        if not isinstance(seed, Route):
            return self.row_of(seed), None
        walk = self.trailgraph.walk(seed)
        facts, shape = self.trailgraph.facts(self._trip(walk, return_to_start)[0])
        return OutsideSeed.of(facts, shape, self._retraced_rows(walk.lat, walk.lon)), walk

    def _retraced_rows(self, lat: np.ndarray, lon: np.ndarray) -> list[int]:
        """Rows of the DayHikes a walk retraces: its Twins, on their served lines."""
        if len(lat) < 2:
            return []
        walk = _even_points(lat, lon, TWIN_STEP_M)
        length = _path_length_m(walk[:, 0], walk[:, 1])
        with self._db() as db:
            found = db.execute(
                "SELECT h.id, h.line FROM hike_boxes b JOIN hikes h ON h.id = b.id "
                "WHERE b.north >= ? AND b.south <= ? AND b.east >= ? AND b.west <= ?",
                (lat.min(), lat.max(), lon.min(), lon.max()),
            ).fetchall()
        rows = []
        for dayhike_id, line in found:
            points = np.array(decode_polyline(line))
            other = _even_points(points[:, 0], points[:, 1], TWIN_STEP_M)
            other_length = _path_length_m(other[:, 0], other[:, 1])
            if min(length, other_length) >= TWIN_SHARE * max(length, other_length) and are_twins(walk, other):
                rows.append(self.row_of(dayhike_id))
        return rows

    def rank(
        self,
        seed: Seed,
        weights: Mapping[str, float],
        *,
        month: int | None = None,
        allowed_rows: np.ndarray | None = None,
        limit: int = 10,
        return_to_start: bool = True,
        reach: Reach | None = None,
        loops: bool = False,
        near: tuple[Origin, float] | None = None,
        group_m: float | None = None,
        after: Sequence[tuple[float, float]] = (),
        origin: Origin | None = None,
        draw: int | None = None,
    ) -> Ranking:
        """The ``limit`` hikes most similar to one Seed, with every Measure's score.

        The Seed is a DayHike id, a ComposedHike's Route (the DayHikes it
        retraces are left out, as a Seed's Twins) or a Wish, Measure name to
        value (nothing is left out). Each Measure's ``score`` is
        its calibrated score (what the weights average), ``raw`` the
        uncalibrated one; both are None where either hike lacks the fact.
        ``return_to_start`` measures the Seed and every hike alike with
        its way back (and the figures listed are of that walk), else one way.
        Each hike has its served ``line``.

        With a TrailGraph and ``loops`` (asked for: ADR-0028) the hikes are
        DayHikes and SuggestedLoops (ADR-0020), ranked together: a loop has
        ``id`` None, its ``route``, a name from where it starts, and
        ``drive_hours`` to its start from ``reach`` (None without it), which
        leaves out a loop starting out of reach. ``near``, an Origin and metres,
        leaves out every hike, DayHike or loop, starting farther from it in a
        straight line.

        Unless the Seed is a multi-day route (longer than ``MULTI_DAY_M`` one way) or a Wish asks a
        length over it one way (twice it with ``return_to_start``), every multi-day route comes after every day-length hike, best first in each
        (ADR-0056). One row per area: best first, a hike starting within ``group_m``
        (``GROUP_M`` when None) of a better row's start is a version of that row, listed in its
        ``versions`` (at most ``VERSIONS`` - 1, each walking more than
        ``VERSION_DIFFERS`` of its length off every version kept), else left
        out. Hikes are taken down the whole Ranking until one starts in a
        ``limit`` + 1st area, so ``limit`` rows are listed where the hikes
        allow; loops are searched near the Hiker's ``origin``, when given, first,
        and near the best ``LOOP_TRAILHEADS`` of the ``LOOP_SEEDS`` DayHikes (ADR-0036, ADR-0045);
        with a ``draw`` number, near ``LOOP_TRAILHEADS`` of the best ``LOOP_DRAW_RANKS``
        DayHikes ``drawn`` by score with it, in rank order (ADR-0047).
        ``after`` are the starts (lat, lon) of the rows already listed: every hike
        starting within ``group_m`` (above 0) of one is left out (a DayHike
        after its Twins are chosen, so a Twin of one listed is not listed in its
        place; ``near`` and ``allowed_rows`` come first), so the rows
        listed continue the areas of those exactly, none twice and none skipped,
        and the DayHike rows match one longer Ranking. With ``loops`` each page
        searches loops anew near its own best DayHikes, so its loops can differ
        from those one longer Ranking would list (ADR-0032).

        Each row has ``more_here``: the DayHikes ranked that start in its area (within
        ``group_m`` of its start and of no better row's) and score at most ``GOOD_MATCH``
        below it, but are not listed; no later page lists them. Each row has ``in_area``,
        every DayHike ranked that starts in its area, listed or not. The Ranking counts the
        DayHikes ranked that start in no row's area (ADR-0046).
        """
        group_m = GROUP_M if group_m is None else group_m
        weights = _measure_weights(weights, return_to_start)  # Round trip is left out with the way back
        store = self.store.mode(return_to_start)
        seed_row, seed_walk = self._seed(seed, return_to_start)
        kept = self._far_and_near(self.trailheads, near, group_m, ())
        if kept is not None:
            kept_rows = np.flatnonzero(kept)
            allowed_rows = kept_rows if allowed_rows is None else np.intersect1d(allowed_rows, kept_rows)
        allowed = None if allowed_rows is None else self.store.ids[allowed_rows]
        rows, scores = rank_rows(store, [seed_row], weights, month=month, allowed_ids=allowed)
        # After the Twins are chosen: a Twin of a hike listed stays out, not stands in for it.
        kept = self._far_and_near(self.trailheads[rows], None, group_m, after)
        if kept is not None:
            rows, scores = rows[kept], scores[kept]
        # Unless the Seed is one, the multi-day routes come after every day-length hike (ADR-0056).
        day_first = not self._multi_day_seed(seed, seed_row, seed_walk, return_to_start)
        if day_first:
            order = np.argsort(self._one_way_m[rows] > MULTI_DAY_M, kind="stable")
            rows, scores = rows[order], scores[order]

        def place(candidate: list) -> tuple[bool, float]:
            """Where a hike comes in the Ranking: by score, a multi-day route after the day hikes."""
            return day_first and candidate[1] is not None and self.multi_day(candidate[1]), -candidate[0]

        found_loops, loop_search = [], None
        if loops and self.trailgraph is not None and LOOP_SEEDS:
            seed_line = (
                None
                if isinstance(seed, Mapping)  # a Wish walks nowhere
                else np.array(decode_polyline(self._served_lines([seed])[seed]))
                if seed_walk is None
                else np.column_stack([seed_walk.lat, seed_walk.lon])
            )
            best = rows[:LOOP_SEEDS]
            if draw is not None:
                ranks = rows[:LOOP_DRAW_RANKS]
                keys = [str(dayhike_id) for dayhike_id in self.store.ids[ranks].tolist()]
                best = ranks[drawn(scores[: len(ranks)].tolist(), keys, draw, LOOP_TRAILHEADS, DRAW_T)]
            found_loops, loop_search = self._loops(
                store, seed_row, seed_line, weights, month, best, return_to_start, reach, origin
            )
            starts = np.array([loop["trailhead"] for loop, _ in found_loops]).reshape(-1, 2)
            kept = self._far_and_near(starts, near, group_m, after)
            if kept is not None:
                found_loops = list(itertools.compress(found_loops, kept))
        # [score, store row or None, the loop or None, points once needed]
        catalog = ([float(score), row, None, None] for row, score in zip(rows.tolist(), scores.tolist()))
        found = sorted(([loop["score"], None, loop, points] for loop, points in found_loops), key=lambda c: -c[0])
        groups: list[list[list]] = []  # the best first in each
        for hike in heapq.merge(catalog, found, key=place):
            start = self._start(hike)
            group = next((g for g in groups if _haversine_m(*start, *self._start(g[0])) < group_m), None)
            if group is None:
                if len(groups) == limit:
                    break
                groups.append([hike])
            elif len(group) < VERSIONS and all(
                1.0 - _share_on(self._points(hike), self._points(kept)) > VERSION_DIFFERS for kept in group
            ):
                group.append(hike)
        listed = [hike for group in groups for hike in group if hike[1] is not None]
        listed_rows = [hike[1] for hike in listed]
        summaries = self._summaries(listed_rows, return_to_start)
        lines = self._served_lines([summary["id"] for summary in summaries])
        measures = scored_measures(store, seed_row, np.array(listed_rows, dtype=np.int64), month)
        dayhikes = {
            hike[1]: {**summary, "line": lines[summary["id"]], "score": hike[0], "measures": scored}
            for hike, summary, scored in zip(listed, summaries, measures)
        }
        results = [[hike[2] if hike[1] is None else dayhikes[hike[1]] for hike in group] for group in groups]
        more_here, in_area, other_areas = self._unlisted(rows, scores, groups, listed_rows, group_m)
        if loop_search is not None:
            loop_search["listed"] = sum(hike[1] is None for group in groups for hike in group)
            loop_search["best"] = max((loop["score"] for loop, _ in found_loops), default=None)
        return Ranking(
            [
                {**group[0], "versions": group[1:], "more_here": more, "in_area": area}
                for group, more, area in zip(results, more_here, in_area)
            ],
            other_areas,
            loop_search,
            day_first,
        )

    def _multi_day_seed(
        self, seed: Seed, seed_row: int | OutsideSeed, seed_walk: ComposedWalk | None, return_to_start: bool
    ) -> bool:
        """Whether a Ranking looks for multi-day routes: its Seed is one, or its Wish asks a length over
        ``MULTI_DAY_M`` one way (twice it with Return to start, the way back included)."""
        if isinstance(seed, Mapping):
            return seed.get("length", 0) > MULTI_DAY_M * (2 if return_to_start else 1)
        if seed_walk is not None:
            return float(_step_m(seed_walk.lat, seed_walk.lon).sum()) > MULTI_DAY_M
        return self.multi_day(seed_row)

    def _unlisted(
        self, rows: np.ndarray, scores: np.ndarray, groups: list[list[list]], listed_rows: list[int], group_m: float
    ) -> tuple[list[int], list[int], int]:
        """Of the DayHikes ranked (store ``rows``, ``scores`` best first) and not listed, per
        row of ``rank``'s ``groups`` those starting in its area, within ``group_m`` of its start and
        of no better row's, that score at most ``GOOD_MATCH`` below it; per row every DayHike ranked
        starting in its area, listed or not; and how many start in no row's area."""
        starts = self.trailheads[rows]
        area = np.full(len(rows), -1)
        band = math.degrees(group_m / EARTH_RADIUS_M)  # no nearer than this in latitude alone
        for i, group in enumerate(groups):
            lat, lon = self._start(group[0])
            free = np.flatnonzero((area < 0) & (np.abs(starts[:, 0] - lat) <= band))
            area[free[straight_line_distances_m(Origin(lat, lon), starts[free]) < group_m]] = i
        hidden = np.flatnonzero((area >= 0) & ~np.isin(rows, listed_rows))
        floors = np.array([group[0][0] - GOOD_MATCH for group in groups])
        good = hidden[scores[hidden] >= floors[area[hidden]]]
        return (
            np.bincount(area[good], minlength=len(groups)).tolist(),
            np.bincount(area[area >= 0], minlength=len(groups)).tolist(),
            int(np.count_nonzero(area < 0)),
        )

    @staticmethod
    def _far_and_near(
        places: np.ndarray, near: tuple[Origin, float] | None, group_m: float, after: Sequence[tuple[float, float]]
    ) -> np.ndarray | None:
        """Which places (lat, lon rows) are within ``near`` and ``group_m`` or farther from
        every start in ``after``, as ``rank`` keeps hikes starting there; None keeps all."""
        if near is None and not after:
            return None
        kept = np.ones(len(places), dtype=bool)
        if not len(places):
            return kept
        if near is not None:
            kept &= straight_line_distances_m(near[0], places) <= near[1]
        for lat, lon in after:
            kept &= straight_line_distances_m(Origin(lat, lon), places) >= group_m
        return kept

    def _start(self, hike: list) -> tuple[float, float]:
        """Where a hike of ``rank``'s grouping starts: a DayHike at its Trailhead."""
        if hike[1] is None:
            return tuple(hike[2]["trailhead"])
        lat, lon = self.trailheads[hike[1]]
        return float(lat), float(lon)

    def _points(self, hike: list) -> np.ndarray:
        """A hike of ``rank``'s grouping every ``TWIN_STEP_M``; a DayHike's on its served line, read once."""
        if hike[3] is None:
            dayhike_id = int(self.store.ids[hike[1]])
            line = np.array(decode_polyline(self._served_lines([dayhike_id])[dayhike_id]))
            hike[3] = _even_points(line[:, 0], line[:, 1], TWIN_STEP_M)
        return hike[3]

    def _loops(
        self,
        store: SimilarityStore,
        seed: int | OutsideSeed,
        seed_line: np.ndarray | None,
        weights: Mapping[str, float],
        month: int | None,
        rows: np.ndarray,
        return_to_start: bool,
        reach: Reach | None,
        origin: Origin | None = None,
    ) -> tuple[list[tuple[dict, np.ndarray]], dict]:
        """SuggestedLoops from Junctions near ``origin`` and the Trailheads of ``rows``, as
        ``rank`` lists them, each with its walk every ``TWIN_STEP_M``, and what the search did
        (``starts``, loops ``found`` and measured, whether it ``stopped`` at the deadline): the
        ``LOOP_MEASURED`` best guesses of ``find_loops`` measured (with their
        way back when the Seed is) and scored like a DayHike. A loop is left out
        that shares more than ``LOOP_SEED_SHARE`` of its walk with the Seed's
        line (``seed_line``, (lat, lon) rows; None for a Wish) or of the Seed's with it, or that
        is a Twin of a DayHike. All of it stops at ``LOOP_BUDGET_S``, the search at ``LOOP_SEARCH_S``,
        so a search that runs out of time still leaves time to measure the loops it found."""
        began = time.monotonic()
        deadline = began + LOOP_BUDGET_S
        if not isinstance(seed, OutsideSeed):
            seed = OutsideSeed.of(dict(zip(FACT_COLUMNS, store.facts[:, seed].tolist())), store.profile[seed])
        fact = dict(zip(FACT_COLUMNS, seed.facts.tolist()))
        target = LoopTarget(
            fact["length_m"], fact["highest_m"], fact["lowest_m"], fact["elevation_gain_m"], seed.profile
        )
        trailheads = [(float(lat), float(lon)) for lat, lon in self.trailheads[rows]]
        search = find_loops(
            self.trailgraph,
            target,
            trailheads,
            limit=LOOP_MEASURED,
            deadline=began + min(LOOP_SEARCH_S, LOOP_BUDGET_S),
            reach=reach,
            origin=None if origin is None else (origin.latitude, origin.longitude),
        )
        measured, stopped = [], search.stopped
        for loop in search.loops:
            if time.monotonic() >= deadline:
                stopped = True
                break
            walk = self.trailgraph.walk_segments(loop.start, loop.segments)
            trip = self._trip(walk, return_to_start)[0]
            facts, shape = self.trailgraph.facts(trip)
            measured.append((loop, walk, trip, facts, shape))
        done = {"starts": search.starts, "found": len(measured), "stopped": stopped}
        if not measured:
            return [], done
        loops = SimilarityStore(
            ids=np.zeros(len(measured), dtype=np.int64),
            facts=np.array([[facts[c] for c in FACT_COLUMNS] for *_, facts, _ in measured], dtype=np.float32).T,
            profile=np.array(
                [np.full(PROFILE_POINTS, np.nan) if shape is None else shape for *_, shape in measured],
                dtype=np.float32,
            ),
            twins=np.arange(len(measured)),
            quantiles=store.quantiles,
            values=store.values,
            rarity=store.rarity,
        )
        similarity, _ = score_rows(loops, [seed], np.arange(len(measured)), _measure_weights(weights), month=month)
        seed_points = None if seed_line is None else _even_points(seed_line[:, 0], seed_line[:, 1], TWIN_STEP_M)
        kept: dict[int, np.ndarray] = {}
        for i in np.flatnonzero(~np.isnan(similarity)).tolist():
            walk = measured[i][1]
            points = _even_points(walk.lat, walk.lon, TWIN_STEP_M)
            if seed_points is not None and (
                max(_share_on(points, seed_points), _share_on(seed_points, points)) > LOOP_SEED_SHARE
            ):
                continue  # it mostly walks the Seed again
            if self._retraced_rows(walk.lat, walk.lon):
                continue  # a Catalog DayHike stands for it
            kept[i] = points
        chosen = list(kept)
        listed = []
        for i, measures in zip(chosen, scored_measures(loops, seed, np.array(chosen, dtype=np.int64), month)):
            loop, walk, trip, facts, _ = measured[i]
            climb_m, descent_m = climb_and_descent(trip.lat, trip.lon, trip.ele)
            listed.append((
                {
                    "id": None,
                    "route": {"text": str(walk.route)},
                    "name": self._loop_name(walk),
                    "length_m": _value(facts["length_m"]),
                    "elevation_gain_m": _value(facts["elevation_gain_m"]),
                    "climb_m": _round(climb_m),
                    "descent_m": _round(descent_m),
                    "highest_m": _value(facts["highest_m"]),
                    "difficulty_rank": _value(facts["difficulty_rank"]),
                    "trailhead": [float(walk.lat[0]), float(walk.lon[0])],
                    "line": encode_polyline(*simplify_line(walk.lat, walk.lon, LINE_TOLERANCE_M)),
                    "drive_hours": loop.drive_hours,
                    "score": float(similarity[i]),
                    "measures": measures,
                },
                kept[i],
            ))
        return listed, done

    def _served_lines(self, ids: list[int]) -> dict[int, str]:
        """DayHikes' served lines, encoded, what their Twins are found on, by id."""
        with self._db() as db:
            return dict(_in_chunks(db, "SELECT id, line FROM hikes WHERE id IN ({})", ids))

    def _loop_name(self, walk: ComposedWalk) -> str:
        """A SuggestedLoop's name, from where it starts: the named hiking place
        nearest its start, "from" it within ``TRAILHEAD_M``, "near" it within
        ``LOOP_NAME_M``; else "on" the trails it walks longest; else the
        start's coordinates (ADR-0022)."""
        near = self._places_near_start(walk, LOOP_NAME_M)
        if near:
            metres, name = near[0]
            return f"Loop {'from' if metres <= TRAILHEAD_M else 'near'} {name}"
        trails = self._trails(walk, self._labels(list(walk.relation_m)))
        if trails:
            return f"Loop on {trails}"
        return f"Loop from {float(walk.lat[0]):.4f}, {float(walk.lon[0]):.4f}"

    def pair(
        self,
        a: int | Route,
        b_id: int,
        weights: Mapping[str, float],
        *,
        month: int | None = None,
        return_to_start: bool = True,
    ) -> dict:
        """How alike two hikes are: the score a Ranking would show for DayHike b
        with a (a DayHike id or a ComposedHike's Route) as the Seed (None when no
        weighted Measure is scored), and every Measure's calibrated and raw score;
        both hikes with their way back, or both one way.
        """
        store = self.store.mode(return_to_start)
        a_row, _ = self._seed(a, return_to_start)
        b = self.row_of(b_id)
        chosen = _measure_weights(weights, return_to_start)
        _check_climate(store, month)
        rows = np.array([b])
        similarity, _ = score_rows(store, [a_row], rows, chosen, month=month)
        return {
            "a": None if isinstance(a, Route) else a,
            "b": b_id,
            "score": _value(similarity[0]),
            "measures": scored_measures(store, a_row, rows, month)[0],
        }

    def reachable(
        self, origin: Origin, max_hours: float, client: MatrixClient
    ) -> dict[int, float]:
        """Rows whose Trailhead is within ``max_hours`` DriveTime, with the hours."""
        hours = self.drive_hours(origin, max_hours, client, self.trailheads)
        return {row: value for row, value in enumerate(hours) if value is not None}

    def drive_hours(
        self, origin: Origin, max_hours: float, client: MatrixClient, places: Sequence[tuple[float, float]]
    ) -> list[float | None]:
        """DriveTime hours to each place, None beyond ``max_hours``.

        Only places within the 140 km/h straight-line reach are asked.
        """
        reach_m = max_hours * 3600 * MAX_CAR_SPEED_MS
        places = np.asarray(places, dtype=float).reshape(-1, 2)
        rows = np.flatnonzero(straight_line_distances_m(origin, places) <= reach_m)
        hours: list[float | None] = [None] * len(places)
        if rows.size == 0:
            return hours
        times = client.sources_to_targets(
            origin, [Trailhead(float(lat), float(lon), None) for lat, lon in places[rows]]
        )
        for row, seconds in zip(rows.tolist(), times):
            if seconds is not None and math.isfinite(seconds) and 0 <= seconds <= max_hours * 3600:
                hours[row] = seconds / 3600
        return hours


def draw_noise(draw: int, key: str) -> float:
    """Gumbel noise for the row named ``key`` (a DayHike id, a loop's route text) in draw number
    ``draw``: a hash of "draw:key" (FNV-1a, then MurmurHash3's finalizer), the same as the page's
    (``drawNoise`` in app.js), so a draw needs no random state."""
    h = 0x811C9DC5
    for byte in f"{draw}:{key}".encode():
        h = ((h ^ byte) * 0x01000193) & 0xFFFFFFFF
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    h ^= h >> 16
    return -math.log(-math.log((h + 0.5) / 2**32))


def drawn(scores: Sequence[float], keys: Sequence[str], draw: int, count: int, t: float = DRAW_T) -> list[int]:
    """The indices of ``count`` rows drawn without replacement, each next one with probability
    rising as exp(score / ``t``) (Gumbel-top-k: the largest score / ``t`` plus ``draw_noise``),
    in their given order; all of them when there are no more (ADR-0047)."""
    keyed = sorted(range(len(scores)), key=lambda i: (-(scores[i] / t + draw_noise(draw, keys[i])), i))
    return sorted(keyed[:count])


def group_radius_m(max_hours: float | None, max_km: float | None) -> float:
    """The grouping radius a Ranking takes by default from its limits (ADR-0032): ``GROUP_SHARE`` of the
    distance limit, or of the drive-time limit at ``GROUP_KMH``, the smaller, at least ``GROUP_MIN_M``;
    ``GROUP_M`` with neither."""
    limits = [km * 1000 for km in (max_km,) if km is not None]
    limits += [hours * GROUP_KMH * 1000 for hours in (max_hours,) if hours is not None]
    return max(GROUP_MIN_M, GROUP_SHARE * min(limits)) if limits else GROUP_M


def scored_measures(
    store: SimilarityStore, seed: int | OutsideSeed, rows: np.ndarray, month: int | None
) -> list[dict]:
    """Per row, every Measure's calibrated ``score``, ``raw`` score and
    ``score_note`` against the Seed."""
    measures = {}
    for measure in MEASURES:
        raw = ranked_scores(store, measure, seed, rows, month=month)
        key = _calibration_key(measure, month, seed)
        same = _sameness(store, measure, seed, rows, month, raw)
        calibrated = calibrate(store, key, raw, measure_rarity(store, measure, seed, rows, month=month))
        pairs = "random hike pairs where either has one" if key == measure.name and measure.presence else "random hike pairs"
        unmeasured = (
            wish_unmeasured(store, measure, seed, rows, month)
            if isinstance(seed, OutsideSeed) and seed.stated
            else np.zeros(len(rows), dtype=bool)
        )
        measures[measure.name] = (raw, calibrated, random_pair_shares(store, key, raw)[1], same, pairs, unmeasured)
    return [
        {
            name: {
                "score": _value(calibrated[i]),
                "raw": _value(raw[i]),
                "note": UNMEASURED_NOTE if unmeasured[i] else score_note(_value(calibrated[i]), _value(tied[i]), same[i], pairs),
            }
            for name, (raw, calibrated, tied, same, pairs, unmeasured) in measures.items()
        }
        for i in range(len(rows))
    ]


def _sameness(
    store: SimilarityStore, measure, seed: int | OutsideSeed, rows: np.ndarray, month: int | None, raw: np.ndarray
) -> list[str | None]:
    """Per row, "identical" when its value of the Measure is the Seed's, else
    why a raw score of 1 counts two different values as the same, else "not
    compared" when neither has an OSM flag's feature, else None."""
    if measure.kind == "profile":
        left = seed.profile if isinstance(seed, OutsideSeed) else store.profile[seed]
        identical = (store.profile[rows] == left).all(axis=-1)
        reason = "the same shape walked the other way"
    else:
        columns = [f"{c}_{month:02d}" if measure.monthly and month is not None else c for c in measure.columns]
        index = [FACT_COLUMNS.index(c) for c in columns]
        left = seed.facts[index] if isinstance(seed, OutsideSeed) else store.facts[index, seed]
        identical = (store.facts[index][:, rows] == np.asarray(left)[:, None]).all(axis=0)
        if measure.dead_band is not None:
            reason = f"less than {measure.dead_band * 100:g} % of the walk apart"
        elif measure.cap is not None:
            reason = f"both {measure.cap:g} or more"
        elif measure.kind == "ratio":
            reason = FLOOR_TEXT[measure.name]
        else:
            reason = "close enough to score the same"
    absent = np.isnan(raw) & identical & (store.facts[index[0]][rows] == 0) if measure.presence else np.zeros(len(rows), bool)
    return [
        "not compared" if none else "identical" if same else reason if one else None
        for same, one, none in zip(identical.tolist(), (raw == 1.0).tolist(), absent.tolist())
    ]


# Letters with no Unicode decomposition to a base letter, spelt as a plain keyboard would.
_PLAIN_LETTERS = str.maketrans(
    {"ł": "l", "ø": "o", "đ": "d", "ð": "d", "æ": "ae", "œ": "oe", "ı": "i", "þ": "th"}
)


def _fold(text: str) -> str:
    """Casefolded, accents removed, ue/oe/ae as u/o/a (Mueller is Müller): what search compares."""
    plain = text.replace("\n", " ").casefold().translate(_PLAIN_LETTERS)
    decomposed = unicodedata.normalize("NFKD", plain)
    bare = "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
    return bare.replace("ue", "u").replace("oe", "o").replace("ae", "a")


class _Spellings:
    """The words of every hike name, to find a query word typed with a typo.

    A word of ``FUZZY_MIN_LETTERS`` letters or more may be one or two letters
    off (``typos``): a letter missing, added, changed, or two swapped. The
    words are kept by length as arrays of their letters, with a mask of the
    letters each holds, so a query compares only words of a near length
    whose letters differ little, all at once.
    """

    def __init__(self, names: list[str]) -> None:
        counts: dict[str, int] = {}
        for name in names:
            for word in set(re.findall(r"\w+", name)):
                if len(word) >= FUZZY_MIN_LETTERS - 1:
                    counts[word] = counts.get(word, 0) + 1
        by_length: dict[int, list[str]] = {}
        for word in sorted(counts, key=lambda word: (-counts[word], word)):  # the commonest first
            by_length.setdefault(len(word), []).append(word)
        self._words = {
            length: (
                spelled,
                np.array([[ord(c) for c in word] for word in spelled], dtype=np.uint32),
                np.array([_letter_mask(word) for word in spelled], dtype=np.uint32),
            )
            for length, spelled in by_length.items()
        }

    @staticmethod
    def typos(word: str) -> int:
        """How many typos a word may have: none under FUZZY_MIN_LETTERS letters
        or with a digit (1003 is another stage than 1002), two from 8."""
        if len(word) < FUZZY_MIN_LETTERS or any(c.isdigit() for c in word):
            return 0
        return 1 if len(word) < 8 else 2

    def near(self, word: str) -> list[tuple[int, str]]:
        """(typos, word) of the names' words at most ``typos(word)`` off it,
        but not ``word`` itself: fewest typos first, then the commonest."""
        allowed = self.typos(word)
        if not allowed:
            return []
        letters, mask = np.array([ord(c) for c in word], dtype=np.uint32), np.uint32(_letter_mask(word))
        found: list[tuple[int, int, str]] = []
        for length in range(len(word) - allowed, len(word) + allowed + 1):
            if length not in self._words:
                continue
            spelled, table, masks = self._words[length]
            # A typo changes at most two letters of the mask: a cheap test first.
            keep = np.flatnonzero(np.bitwise_count(masks ^ mask) <= 2 * allowed)
            off = _typos_off(letters, table[keep])
            found += [(int(off[i]), int(keep[i]), spelled[keep[i]]) for i in np.flatnonzero((off <= allowed) & (off > 0))]
        return [(typos, spelled) for typos, _, spelled in sorted(found)]

    def respelled(self, words: list[str]) -> list[list[str]]:
        """The query's words with typos put right, fewest typos first: each
        word as typed or as one of its ``FUZZY_SPELLINGS`` nearest words,
        at most ``FUZZY_QUERIES`` of them, never the query as typed."""
        options = [[(0, word)] + self.near(word)[:FUZZY_SPELLINGS] for word in words]
        start = (0,) * len(options)
        heap, seen, queries = [(0, start)], {start}, []
        while heap and len(queries) < FUZZY_QUERIES:
            typos, chosen = heapq.heappop(heap)
            if typos:
                queries.append([options[i][pick][1] for i, pick in enumerate(chosen)])
            for i, pick in enumerate(chosen):
                if pick + 1 < len(options[i]):
                    after = chosen[:i] + (pick + 1,) + chosen[i + 1 :]
                    if after not in seen:
                        seen.add(after)
                        cost = typos - options[i][pick][0] + options[i][pick + 1][0]
                        heapq.heappush(heap, (cost, after))
        return queries


def _letter_mask(word: str) -> int:
    """A bit per letter a word holds: a to z, any other letter in one of six bits more."""
    mask = 0
    for c in word:
        mask |= 1 << (ord(c) - 97 if "a" <= c <= "z" else 26 + ord(c) % 6)
    return mask


def _typos_off(word: np.ndarray, table: np.ndarray) -> np.ndarray:
    """Per row of ``table`` (the letters of words of one length), how many
    typos it is off ``word``: letters missing, added, changed or two swapped
    (the optimal string alignment distance), all rows at once."""
    count, length = table.shape
    before = None
    above = np.tile(np.arange(length + 1, dtype=np.int16), (count, 1))
    for i in range(1, len(word) + 1):
        row = np.empty_like(above)
        row[:, 0] = i
        changed = (table != word[i - 1]).astype(np.int16)
        for j in range(1, length + 1):
            best = np.minimum(np.minimum(above[:, j], row[:, j - 1]) + 1, above[:, j - 1] + changed[:, j - 1])
            if before is not None and j > 1:
                swapped = (table[:, j - 2] == word[i - 1]) & (table[:, j - 1] == word[i - 2])
                best = np.where(swapped, np.minimum(best, before[:, j - 2] + 1), best)
            row[:, j] = best
        before, above = above, row
    return above[:, length]


def _relevance(name: str, needle: str, words: list[str]) -> int:
    """0 the exact name, 1 starts with the query, 2 every word starts a word, else 3."""
    if name == needle:
        return 0
    if name.startswith(needle):
        return 1
    name_words = re.split(r"\W+", name)
    if all(any(part.startswith(word) for part in name_words) for word in words):
        return 2
    return 3


def _in_chunks(db: sqlite3.Connection, query: str, values: list) -> list[tuple]:
    """``query`` with its ``IN ({})`` filled by ``values``, a few hundred at a time."""
    rows = []
    for start in range(0, len(values), 900):
        batch = values[start : start + 900]
        rows += db.execute(query.format(",".join("?" * len(batch))), batch).fetchall()
    return rows


def _shown_name(dayhike_id: int, name: str, origin: str) -> str:
    """A DayHike's name as the page shows it: a synthetic ``relation:{id}``
    label (no name, ref or place in OSM) reads as an unnamed hike."""
    return f"Unnamed hike (OSM relation {dayhike_id})" if origin == NAME_ORIGIN_SYNTHETIC else name


def _value(value: float) -> float | None:
    value = float(value)
    return None if math.isnan(value) else value


def _round(value: float) -> int | None:
    return None if math.isnan(value) else round(float(value))


def _share_on(a: np.ndarray, b: np.ndarray) -> float:
    """The share of walk a within ``TWIN_M`` of walk b's line, both (lat, lon) rows every ``TWIN_STEP_M``."""
    distance, _ = nearest_on_line(b[:, 0], b[:, 1], a[:, 0], a[:, 1])
    return float((distance <= TWIN_M).mean())


def _way_back(metres: float, kind: float) -> dict:
    """How a DayHike gets back to its start, as ``way_back`` stores it."""
    return {"m": float(metres), "kind": WAY_BACK_KINDS[int(kind)]}
