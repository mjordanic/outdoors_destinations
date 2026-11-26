"""Places to stay and restaurants, cafes and pubs near each place, from OSM extracts (ADR-0084)."""

import sqlite3
from pathlib import Path

import numpy as np

from dayhike.cli import main
from dayhike.town_counts import TownCounts, count_near, count_town_amenities

FIXTURES = Path(__file__).parent / "fixtures"
TOWN, EAST, NOWHERE = 1, 2, 3


def _places(tmp_path: Path) -> Path:
    path = tmp_path / "places.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE places (id INTEGER PRIMARY KEY, name TEXT, country TEXT, population INTEGER, lat REAL, lon REAL)")
    db.executemany(
        "INSERT INTO places VALUES (?, ?, 'SI', 0, ?, ?)",
        [(TOWN, "Town", 45.0, 15.0), (EAST, "East", 45.0, 15.1), (NOWHERE, "Nowhere", 46.0, 15.0)],
    )
    db.commit()
    db.close()
    return path


def _counts(counts: TownCounts) -> dict[int, tuple[int, int]]:
    return {int(p): (int(s), int(e)) for p, s, e in zip(counts.place_ids, counts.stays, counts.eats)}


def test_places_to_stay_and_eat_within_2_km_are_counted(tmp_path: Path) -> None:
    counts = count_town_amenities([FIXTURES / "town_amenities.osm"], _places(tmp_path), workers=1)

    # Stays: hotel 0.5 km, guest house 1.9 km, the hotel with a restaurant, the chalet way,
    # the camp site multipolygon; not the hotel 2.1 km out nor the museum.
    # Eats: cafe, pub and the hotel's restaurant; not the bank.
    assert _counts(counts) == {TOWN: (5, 3), EAST: (0, 0), NOWHERE: (0, 0)}
    assert counts.radius_m == 2_000


def test_each_extract_counts_and_an_object_in_two_extracts_counts_once(tmp_path: Path) -> None:
    places = _places(tmp_path)
    extracts = [FIXTURES / "town_amenities.osm", FIXTURES / "town_amenities_east.osm"]

    one = count_town_amenities(extracts, places, workers=1)
    two = count_town_amenities([*extracts, extracts[0]], places, workers=2)

    assert _counts(one) == {TOWN: (5, 3), EAST: (1, 0), NOWHERE: (0, 0)}
    assert _counts(two) == _counts(one)


def test_counts_saved_and_counted_again_at_another_radius(tmp_path: Path) -> None:
    places = _places(tmp_path)
    counts = count_town_amenities([FIXTURES / "town_amenities.osm"], places, workers=1)

    again = TownCounts.load(counts.save(tmp_path / "town-counts.npz"))
    wider = count_near(again.points, again.kinds, places, 3_000, workers=1)

    assert _counts(again) == _counts(counts) and again.sources == ("town_amenities.osm",)
    assert _counts(wider)[TOWN] == (6, 3)  # the hotel 2.1 km out


def test_count_town_amenities_command_saves_the_counts(tmp_path: Path) -> None:
    places = _places(tmp_path)
    out = tmp_path / "town-counts.npz"

    code = main(
        ["count-town-amenities", "--extract", str(FIXTURES / "town_amenities.osm"),
         "--extract", str(FIXTURES / "town_amenities_east.osm"), "--places", str(places),
         "--out", str(out), "--workers", "2"]
    )  # fmt: skip

    assert code == 0
    assert _counts(TownCounts.load(out)) == {TOWN: (5, 3), EAST: (1, 0), NOWHERE: (0, 0)}


def test_without_osmium_tool_the_extracts_are_read_whole_to_the_same_counts(tmp_path: Path, monkeypatch) -> None:
    places = _places(tmp_path)
    extracts = [FIXTURES / "town_amenities.osm", FIXTURES / "town_amenities_east.osm"]
    monkeypatch.setattr("dayhike.town_counts.shutil.which", lambda name: None)

    counts = count_town_amenities(extracts, places, workers=1)

    assert _counts(counts) == {TOWN: (5, 3), EAST: (1, 0), NOWHERE: (0, 0)}
