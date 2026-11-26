import dataclasses
import json
import re
import shutil
import sqlite3
import time
import zlib
from pathlib import Path

import numpy as np
import pytest

from dayhike import Catalog, build_catalog, build_similarity, rank_dayhikes
from dayhike import serving
from dayhike.catalog import NAME_ORIGIN_OSM_NAME, NAME_ORIGIN_PLACE, Origin
from dayhike.db import scoped
from dayhike.regions import load_region
from dayhike.serving import (
    ELEVATION_POINTS,
    FLOOR_TEXT,
    ServingData,
    ServingExportError,
    _fold,
    _Spellings,
    _relevance,
    decode_polyline,
    draw_noise,
    drawn,
    drawn_profile,
    encode_polyline,
    export_name_index,
    export_serving,
    score_note,
    scored_measures,
    simplify_line,
)
from dayhike.similarity import FACT_COLUMNS, MEASURE_NAMES, _resample

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
RIDGE_WALK_ID = 1001


@pytest.fixture(scope="module")
def catalog_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("serving")
    path = directory / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    build_similarity(path, directory / "dem")
    return path


@pytest.fixture(scope="module")
def export(catalog_path: Path) -> Path:
    return export_serving(catalog_path, catalog_path.parent / "web-serving")


@pytest.fixture(scope="module")
def data(export: Path) -> ServingData:
    return ServingData(export)


def test_export_writes_a_complete_directory(export: Path) -> None:
    meta = json.loads((export / "meta.json").read_text())

    assert meta["format"] == 5
    assert meta["compose"] is True
    assert meta["dayhikes"] == 4
    assert meta["attribution"]["osm"] == "© OpenStreetMap contributors"
    assert "FABDEM" in meta["attribution"]["fabdem"]
    assert "https://doi.org/10.5523/bris.s5hqmjcdj8yo2ibzi9b4ew3sn" in meta["attribution"]["fabdem"]
    assert meta["drive_time"] is False
    for name in ("ids.npy", "facts.npy", "profile.npy", "twins.npy", "quantiles.npy", "meta.json"):
        assert (export / "similarity" / name).is_file()
    assert np.load(export / "trailheads.npy").shape == (4, 2)


def test_export_keeps_the_route_the_true_metre_profile_and_the_measured_points(
    data: ServingData,
) -> None:
    ridge = data.detail(RIDGE_WALK_ID, return_to_start=False)

    line = decode_polyline(ridge["line"])
    assert line[0] == pytest.approx(tuple(ridge["trailhead"]), abs=1e-5)
    assert len(ridge["elevation"]["metres"]) == ELEVATION_POINTS
    assert min(ridge["elevation"]["metres"]) == 200
    assert max(ridge["elevation"]["metres"]) == pytest.approx(500, abs=5)
    assert ridge["elevation"]["step_m"] * (ELEVATION_POINTS - 1) == pytest.approx(ridge["walk_m"])
    kinds = {(point["kind"], point["where"]) for point in ridge["points"]}
    assert {("hut", "line"), ("drinking_water", "line"), ("peak", "line"), ("parking", "trailhead")} <= kinds
    peak = next(point for point in ridge["points"] if point["kind"] == "peak")
    assert (peak["name"], peak["ele"]) == ("Ridge Peak", 900.0)
    along = [point["along_m"] for point in ridge["points"] if point["where"] == "line"]
    assert along == sorted(along)
    assert set(ridge["facts"]) == set(FACT_COLUMNS)
    # Climb and descent follow the drawn direction; the gain fact is the larger.
    assert (ridge["climb_m"], ridge["descent_m"]) == (450, 250)
    assert ridge["facts"]["elevation_gain_m"] == pytest.approx(450)
    assert ridge["facts"]["hut_along"] == 1.0
    assert len(ridge["profile_shape"]) == 64


def test_a_copernicus_profile_is_drawn_smoothed_and_a_fabdem_one_as_sampled() -> None:
    # 3 km due north, a vertex every 10 m: a 300 m hill, with 4 m of DEM jitter every 20 m.
    steps = np.arange(301)
    lat, lon = 45 + steps * 10 / 111_195, np.full(301, 15.0)
    hill = 500 + 300 * np.sin(np.pi * steps / 300)
    noisy = hill + 4.0 * np.where(steps // 2 % 2 == 0, 1.0, -1.0)
    sampled = _resample(lat, lon, noisy, points=ELEVATION_POINTS)
    truth = _resample(lat, lon, hill, points=ELEVATION_POINTS)

    assert drawn_profile(lat, lon, noisy, smooth=False).tolist() == sampled.tolist()
    smoothed = drawn_profile(lat, lon, noisy, smooth=True)
    assert len(smoothed) == ELEVATION_POINTS
    assert np.abs(sampled - truth).max() > 3.5
    assert np.abs(smoothed - truth)[1:-1].max() < 1.5  # the two ends are fitted from one side
    assert smoothed.max() == pytest.approx(800, abs=1.5)  # the top keeps its height
    # A walk shorter than the smoothing is drawn as sampled.
    assert drawn_profile(lat[:5], lon[:5], noisy[:5], smooth=True).tolist() == _resample(
        lat[:5], lon[:5], noisy[:5], points=ELEVATION_POINTS
    ).tolist()
    assert drawn_profile(lat, lon, np.full(301, np.nan), smooth=True) is None


def test_a_dayhike_without_elevation_has_no_profile(data: ServingData) -> None:
    valley = data.detail(1002)

    assert valley["elevation"] is None
    assert valley["facts"]["elevation_gain_m"] is None
    assert (valley["climb_m"], valley["descent_m"]) == (None, None)


def test_in_box_finds_a_line_crossing_the_box_away_from_its_trailhead(data: ServingData) -> None:
    # Ridge Walk runs north from its Trailhead at 45.0, 15.0 to 45.09.
    hikes, too_many = data.in_box(45.05, 14.99, 45.06, 15.01, 10)

    assert [hike["id"] for hike in hikes] == [RIDGE_WALK_ID]
    assert too_many is False
    assert hikes[0]["name"] == "Ridge Walk"
    assert decode_polyline(hikes[0]["line"])[0] == pytest.approx((45.0, 15.0), abs=1e-5)
    assert hikes[0]["walk_m"] == pytest.approx(data.detail(RIDGE_WALK_ID)["walk_m"])
    assert data.in_box(46.0, 16.0, 46.1, 16.1, 10) == ([], False)


def test_in_box_lists_none_when_the_box_holds_more_than_the_limit(data: ServingData) -> None:
    assert data.in_box(44.0, 14.0, 46.0, 16.0, 3) == ([], True)
    every, too_many = data.in_box(44.0, 14.0, 46.0, 16.0, 4)
    assert len(every) == 4 and too_many is False


def test_export_refuses_a_similarity_store_for_other_dayhikes(
    catalog_path: Path, tmp_path: Path
) -> None:
    copy = tmp_path / "catalog.sqlite"
    copy.write_bytes(catalog_path.read_bytes())
    (tmp_path / "catalog.geometry.sqlite").write_bytes(
        (catalog_path.parent / "catalog.geometry.sqlite").read_bytes()
    )
    build_similarity(copy, tmp_path / "dem")
    connection = sqlite3.connect(copy)
    connection.execute("DELETE FROM trailheads WHERE dayhike_id = 1007")
    connection.execute("DELETE FROM dayhikes WHERE id = 1007")
    connection.commit()
    connection.close()

    with pytest.raises(ServingExportError, match="other DayHikes"):
        export_serving(copy, tmp_path / "out")
    assert not (tmp_path / "out" / "meta.json").exists()


def test_export_refuses_a_similarity_store_for_earlier_catalog_values(
    catalog_path: Path, tmp_path: Path
) -> None:
    copy = tmp_path / "catalog.sqlite"
    copy.write_bytes(catalog_path.read_bytes())
    (tmp_path / "catalog.geometry.sqlite").write_bytes(
        (catalog_path.parent / "catalog.geometry.sqlite").read_bytes()
    )
    build_similarity(copy, tmp_path / "dem")
    connection = sqlite3.connect(copy)
    connection.execute(
        "UPDATE feature_vectors SET length_m = length_m + 1 WHERE dayhike_id = ?", (RIDGE_WALK_ID,)
    )
    connection.commit()
    connection.close()

    with pytest.raises(ServingExportError, match="build-similarity"):
        export_serving(copy, tmp_path / "out")
    assert not (tmp_path / "out" / "meta.json").exists()


def test_an_incomplete_export_does_not_open(tmp_path: Path) -> None:
    with pytest.raises(ServingExportError, match="no complete serving export"):
        ServingData(tmp_path)


def test_search_finds_prefix_then_contains_ignoring_case_and_accents(data: ServingData) -> None:
    assert [hit["name"] for hit in data.search("ridge")] == ["Ridge Walk"]
    assert [hit["name"] for hit in data.search("WALK")] == ["Ridge Walk"]
    assert [hit["name"] for hit in data.search("Válley")] == ["Valley Path"]
    assert [(hit["id"], hit["match"]) for hit in data.search("1002")] == [(1002, "words")]
    assert data.search("x") == []
    assert data.search("no such hike") == []
    hit = data.search("ridge")[0]
    assert hit["trailhead"] == [45.0, 15.0]
    assert hit["length_m"] == pytest.approx(10_000, abs=1)
    assert (hit["climb_m"], hit["descent_m"]) == (450, 250)


def test_search_folds_letters_without_a_decomposition_to_plain_letters() -> None:
    assert _fold("Høllesliheia") == _fold("Hollesliheia")
    assert _fold("Łódź Przełęcz") == _fold("Lodz Przelecz")
    assert _fold("Đakovo Gjøvik") == _fold("Dakovo Gjovik")
    assert _fold("Ærø Œuvre Iğdır") == _fold("aero oeuvre igdir")


def test_search_matches_every_word_in_any_order(data: ServingData) -> None:
    assert [hit["name"] for hit in data.search("walk ridge")] == ["Ridge Walk"]
    assert [hit["name"] for hit in data.search("  RIDGE   wa ")] == ["Ridge Walk"]
    assert data.search("ridge path") == []


def test_search_folds_german_ue_oe_ae_spellings() -> None:
    assert _fold("Müller-Hütte") == _fold("Mueller-Huette")
    assert _fold("Schönau Bärental") == _fold("Schoenau Baerental")


def test_search_relevance_puts_exact_then_prefix_then_the_query_in_order_then_word_starts_first() -> None:
    assert _relevance("triglav", "triglav", ["triglav"]) == 0
    assert _relevance("triglav north face", "triglav", ["triglav"]) == 1
    assert _relevance("tour du mont blanc", "mont blanc", ["mont", "blanc"]) == 2
    assert _relevance("dolic - triglav", "triglav dolic", ["triglav", "dolic"]) == 3
    assert _relevance("lac blanc from montana", "mont blanc", ["mont", "blanc"]) == 3
    assert _relevance("pod triglavom", "riglav", ["riglav"]) == 4


def test_search_prefix_matches_come_first(data: ServingData) -> None:
    # "path" is inside "Valley Path"; "ps" starts "PS-1" and is inside no other name.
    names = [hit["name"] for hit in data.search("p")] + [hit["name"] for hit in data.search("pa")]
    assert names == ["Valley Path"]
    assert [hit["name"] for hit in data.search("ps")] == ["PS-1"]


def test_search_scans_a_bounded_number_of_occurrences_of_a_word_of_two_letters_or_more(
    data: ServingData, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert [hit["name"] for hit in data.search("al")] == ["Ridge Walk", "Valley Path"]
    assert data.search("r w") == []
    monkeypatch.setattr(serving, "SEARCH_SCAN_CAP", 1)
    assert [hit["name"] for hit in data.search("al")] == ["Ridge Walk"]


def test_search_finds_an_exact_name_first_though_its_first_word_is_in_more_names_than_are_scanned(
    export: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _renamed(export, tmp_path / "wanderweg", [
        ("Wanderweg 523 near Salla", []),
        ("Wanderweg 24 - Neubeuern", []),
        ("Wanderweg 52 near Salla", []),
        ("Wanderweg 66 - Hochfelln", []),
    ])  # fmt: skip
    monkeypatch.setattr(serving, "SEARCH_SCAN_CAP", 2)  # Wanderweg is in more names than are scanned

    assert [hit["name"] for hit in data.search("Wanderweg 52 near Salla")] == [
        "Wanderweg 52 near Salla",
        "Wanderweg 523 near Salla",
    ]
    assert [hit["name"] for hit in data.search("Wanderweg 66 - Hochfelln")] == ["Wanderweg 66 - Hochfelln"]
    monkeypatch.setattr(serving, "SEARCH_SCAN_CAP", 1)  # every word is: the exact name is still found
    assert [hit["name"] for hit in data.search("wanderweg 52 near salla")][:1] == ["Wanderweg 52 near Salla"]


def test_search_puts_the_exact_name_before_the_hike_to_the_summit_named(export: Path, tmp_path: Path) -> None:
    data = _renamed(export, tmp_path / "triglav-exact", [
        ("Triglav via Dom Planika from Pri lesi", [("peak", "Triglav", 1)]),
        ("Triglav", [("peak", "Triglav", 300)]),
        ("Ridge Walk", []),
        ("Valley Path", []),
    ])  # fmt: skip

    assert [hit["name"] for hit in data.search("Triglav")] == ["Triglav", "Triglav via Dom Planika from Pri lesi"]


def test_search_finds_a_name_typed_with_a_typo_and_says_it_is_only_similar(data: ServingData) -> None:
    for typed in ("Valey", "Vallley", "Valkey Path", "Rdige walk"):
        assert [(hit["name"], hit["match"]) for hit in data.search(typed)][:1] in (
            [("Valley Path", "similar")],
            [("Ridge Walk", "similar")],
        ), typed
    assert [(hit["name"], hit["match"]) for hit in data.search("ridge")] == [("Ridge Walk", "words")]
    assert data.search("Rid") == data.search("rid")  # a short word only as typed: nothing near it


def test_search_finds_two_words_typed_as_one(data: ServingData) -> None:
    # "Catbells" is how most people write Cat Bells (holiday visitor, round 3).
    assert [(hit["name"], hit["match"]) for hit in data.search("Ridgewalk")] == [("Ridge Walk", "words")]
    assert [hit["name"] for hit in data.search("valleypath")] == ["Valley Path"]
    assert [hit["name"] for hit in data.search("ridgewalk, croatia")] == ["Ridge Walk"]
    assert data.search("ridgeways") == []


def test_a_landmark_typed_as_one_word_is_found_by_its_two(data: ServingData) -> None:
    assert [(landmark["name"], landmark["match"]) for landmark in data.find_landmarks("Ridgepeak")] == [("Ridge Peak", "words")]


def test_a_word_only_inside_a_longer_word_is_a_similar_name(data: ServingData) -> None:
    # As Rappenloch is only inside Krappenlochklamm: the name has no such word.
    assert [(hit["name"], hit["match"]) for hit in data.search("idge")] == [("Ridge Walk", "similar")]


NAMES_TO_FIND = [
    ("Mt. Whitney Trail", []),
    ("Müllerhütte - Großglockner Rundweg", []),
    ("Łysica przez Święta Katarzyna", []),
    ("Koča pri Savici - Dom na Komni", []),
]
QUERIES = (
    "mount whitney", "Mt Whitney", "mt. whit", "muellerhutte", "MÜLLER", "grossglockner rundweg", "glockner",
    "lysica", "swieta katarzyna", "koca pri savici", "dom komni", "Savicі", "Grosglocknre", "Rundwge", "Whitny trail",
    "Kocca pri", "1002", "x", "no such hike",
)  # fmt: skip


def test_export_writes_the_name_index_and_a_server_answers_the_same_with_or_without_it(
    export: Path, tmp_path: Path
) -> None:
    indexed = _renamed(export, tmp_path / "indexed", NAMES_TO_FIND)
    assert (indexed.directory / serving.NAME_INDEX).is_file()
    built = tmp_path / "built"
    shutil.copytree(indexed.directory, built)
    (built / serving.NAME_INDEX).unlink()  # an export from before the index: built at start
    without = ServingData(built)

    assert indexed._folded == without._folded
    assert indexed._spellings.tables().keys() == without._spellings.tables().keys()
    for length, (words, letters, masks) in indexed._spellings.tables().items():
        assert words == without._spellings.tables()[length][0] and all(type(word) is str for word in words)
        assert np.array_equal(letters, without._spellings.tables()[length][1])
        assert np.array_equal(masks, without._spellings.tables()[length][2])
    for query in QUERIES:
        assert indexed.search(query) == without.search(query), query
    assert [hit["name"] for hit in indexed.search("Mt Whitney")] == ["Mt. Whitney Trail"]
    assert [(hit["name"], hit["match"]) for hit in indexed.search("Grosglocknre")][:1] == [
        ("Müllerhütte - Großglockner Rundweg", "similar")
    ]


def test_a_server_builds_the_name_index_itself_when_the_exports_is_of_other_hikes_or_another_format(
    export: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _renamed(export, tmp_path / "other", NAMES_TO_FIND)
    ids = np.asarray(other.store.ids)

    assert serving._read_name_index(other.directory, ids) is not None
    assert serving._read_name_index(other.directory, ids + 1) is None  # other hikes: a cut, rerun without the index
    assert serving._read_name_index(other.directory, ids[:-1]) is None
    monkeypatch.setattr(serving, "NAME_INDEX_FORMAT", serving.NAME_INDEX_FORMAT + 1)  # an index of an older format
    assert serving._read_name_index(other.directory, ids) is None
    assert ServingData(other.directory)._folded[0] == "mount whitney trail"  # built again from the names


def test_export_name_index_adds_the_index_beside_an_existing_export(export: Path, tmp_path: Path) -> None:
    out = tmp_path / "old"
    shutil.copytree(export, out)
    (out / serving.NAME_INDEX).unlink()

    from dayhike.cli import main

    assert main(["export-name-index", "--out", str(out)]) == 0
    assert ServingData(out)._folded == ServingData(export)._folded
    assert not list(out.glob("*.partial"))


def _renamed(export: Path, out: Path, hikes: list[tuple[str, list[tuple[str, str, int]]]]) -> ServingData:
    """A copy of ``export`` whose DayHikes, in id order, have these names and named
    points: (kind, name, metres off the line)."""
    shutil.copytree(export, out)
    with scoped(sqlite3.connect(out / "hikes.sqlite")) as db:
        ids = [dayhike_id for (dayhike_id,) in db.execute("SELECT id FROM hikes ORDER BY id")]
        for dayhike_id, (name, points) in zip(ids, hikes, strict=True):
            rows = [[kind, "line", 45.0, 15.0, 0, off_m, point, None] for kind, point, off_m in points]
            db.execute(
                "UPDATE hikes SET name = ?, points = ? WHERE id = ?",
                (name, zlib.compress(json.dumps(rows).encode()), dayhike_id),
            )
        db.commit()
    export_name_index(out)  # the names changed: index them again
    return ServingData(out)


def test_search_puts_the_hike_to_the_summit_named_first_and_viewpoint_paths_named_after_it_last(
    export: Path, tmp_path: Path
) -> None:
    data = _renamed(export, tmp_path / "triglav", [
        ("Triglav View from Pri Cerkvi", [("viewpoint", "Triglav View", 5)]),
        ("Triglavska jezera - Mala Tičarica", [("hut", "Koča pri Triglavskih jezerih", 33)]),
        ("Triglav via Dom Planika pod Triglavom from Pri lesi", [("peak", "Mali Triglav", 448), ("peak", "Triglav", 1)]),
        ("Kredarica via Triglavski dom na Kredarici from Trenta", [("peak", "Triglav", 988)]),
    ])  # fmt: skip

    assert [hit["name"] for hit in data.search("Triglav")] == [
        "Triglav via Dom Planika pod Triglavom from Pri lesi",  # reaches the top of Triglav
        "Triglavska jezera - Mala Tičarica",
        "Triglav View from Pri Cerkvi",  # a view of it
        "Kredarica via Triglavski dom na Kredarici from Trenta",  # passes 988 m from the top
    ]


def test_search_typed_to_part_of_a_summit_name_puts_the_hike_to_that_summit_first(export: Path, tmp_path: Path) -> None:
    data = _renamed(export, tmp_path / "zugspitze", [
        ("Zugspitzblick from P 10", [("viewpoint", "Zugspitzblick", 9)]),
        ("Zugspitz-Blick from Mösern", [("viewpoint", "Zugspitz-Blick", 0)]),
        ("Zugspitze from Ehrwalder Almbahn", [("peak", "Zugspitzeck", 591), ("peak", "Zugspitze", 0)]),
        ("Zugspitzi am Gaisbach", []),
    ])  # fmt: skip

    assert [hit["name"] for hit in data.search("Zugspitz")] == [
        "Zugspitze from Ehrwalder Almbahn",
        "Zugspitzi am Gaisbach",
        "Zugspitzblick from P 10",
        "Zugspitz-Blick from Mösern",
    ]


def test_search_puts_names_holding_the_query_in_its_order_before_names_with_its_words_apart(
    export: Path, tmp_path: Path
) -> None:
    # No hike reaches the top of Mont Blanc: the Tour du Mont Blanc is named after it.
    data = _renamed(export, tmp_path / "mont-blanc", [
        ("Lac Blanc from Montana", []),
        ("Panorama Mont Blanc from Introd", [("viewpoint", "Panorama Mont Blanc", 0)]),
        ("Petit Mont Blanc from Planay", [("viewpoint", "Petit Mont Blanc", 0)]),
        ("Tour du Mont Blanc - Itinéraire principal", [("peak", "Mont Truc", 1277)]),
    ])  # fmt: skip

    assert [hit["name"] for hit in data.search("Mont Blanc")] == [
        "Tour du Mont Blanc - Itinéraire principal",
        "Petit Mont Blanc from Planay",
        "Panorama Mont Blanc from Introd",
        "Lac Blanc from Montana",
    ]


def test_names_spelled_like_the_query_come_after_every_name_with_its_words(
    data: ServingData, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(data._spellings, "respelled", lambda words: [["path"]])

    assert [(hit["name"], hit["match"]) for hit in data.search("ridge")] == [
        ("Ridge Walk", "words"),
        ("Valley Path", "similar"),
    ]
    assert [hit["name"] for hit in data.search("ridge", limit=1)] == ["Ridge Walk"]


def test_a_word_may_be_one_typo_off_and_a_long_word_two() -> None:
    spellings = _Spellings(["triglav", "dolic triglav", "triglavska jezera", "kredarica", "trig"])

    assert spellings.near("triglov") == [(1, "triglav")]  # changed
    assert spellings.near("tirglav") == [(1, "triglav")]  # two swapped
    assert spellings.near("trglav") == [(1, "triglav")]  # missing
    assert spellings.near("trigglav") == [(1, "triglav")]  # added
    assert spellings.near("trilgov") == []  # two typos in a short word
    assert spellings.near("triglavskka") == [(1, "triglavska")]
    assert spellings.near("krdaricca") == [(2, "kredarica")]
    assert spellings.near("triglav") == [] and spellings.near("tri") == []  # as typed; too short
    assert spellings.respelled(["triglov", "jezra"]) == [["triglov", "jezera"], ["triglav", "jezra"], ["triglav", "jezera"]]
    assert _Spellings(["stage 1002", "stage 1003"]).near("1004") == []  # a number is another hike


def test_respellings_of_equal_typos_try_the_commonest_word_first() -> None:
    spellings = _Spellings(["volley a", "volley b", "valley"])

    assert spellings.respelled(["vplley"]) == [["volley"], ["valley"]]


def test_a_long_query_of_many_words_with_typos_is_respelled_quickly() -> None:
    words = [f"{a}{b}{c}x" for a in "bcd" for b in "aeiou" for c in "lmnr"]
    spellings = _Spellings([" ".join(words[i : i + 3]) for i in range(len(words))])
    query = [word[:-1] + "y" for word in words[:25]]

    started = time.monotonic()
    queries = spellings.respelled(query)

    assert time.monotonic() - started < 2
    assert len(queries) == serving.FUZZY_QUERIES
    assert all(len(spelled) == len(query) and spelled != query for spelled in queries)


def test_rank_matches_rank_dayhikes_and_reports_every_measure(
    data: ServingData, catalog_path: Path
) -> None:
    weights = {"length": 100, "hut_along": 40}
    expected = rank_dayhikes(Catalog.open(catalog_path), ["Ridge Walk"], weights)

    results = data.rank(RIDGE_WALK_ID, weights)

    assert [r["id"] for r in results] == [item.dayhike.id for item in expected.items]
    assert [r["score"] for r in results] == pytest.approx([item.score for item in expected.items])
    for result in results:
        assert set(result["measures"]) == set(MEASURE_NAMES)
    assert results[0]["measures"]["length"]["raw"] == pytest.approx(0.5)
    assert "random hike pair" in results[0]["measures"]["length"]["note"]
    # No climb data where the Seed has some: raw 0 (ADR-0071); no random pair has a climb to calibrate it on.
    assert results[0]["measures"]["elevation_gain"] == {
        "score": None,
        "raw": 0.0,
        "note": "No data for this hike: counts as 0 against the hike you like",
    }


@pytest.mark.parametrize(
    ("score", "tied", "note"),
    [
        # Calibrated 0.52: the same 52 as the score.
        (0.52, 0.0, "More alike than 52% of random hike pairs"),
        # 128/1024 shows as 0.13 (half rounding up), so the note says 13, not 12.
        (0.125, 0.0, "More alike than 13% of random hike pairs"),
        (0.625, 0.1, "More alike than 63% of random hike pairs"),
        (0.996, 0.0, "More alike than 100% of random hike pairs"),
        (0.005, 0.0, "More alike than 1% of random hike pairs"),
        # Highest points 400 m or more apart, like 59% of random pairs: no pair is less alike.
        (0.0, 0.59, "Nothing in common on this measure, like 59% of random hike pairs"),
        (0.004, 0.2, "Nothing in common on this measure, like 20% of random hike pairs"),
        # Less alike than all but a few random pairs, and few tie: no "more alike than 0%".
        (0.004, 0.0, "Less alike than almost all random hike pairs"),
        (0.0, 0.004, "Less alike than almost all random hike pairs"),
        (None, None, None),
    ],
)
def test_score_note_says_the_score_in_words(score, tied, note) -> None:
    assert score_note(score, tied) == note


@pytest.mark.parametrize(
    ("score", "same", "note"),
    [
        # Two dry hikes: 84% of random pairs are as alike on WaterSide, so it tells little.
        (0.16, "identical", "Identical; 84% of random hike pairs are this alike, so it counts little"),
        (0.92, "identical", "Identical; 8% of random hike pairs are this alike"),
        (0.996, "identical", "Identical; fewer than 1% of random hike pairs are this alike"),
        (0.995, "identical", "Identical; 1% of random hike pairs are this alike"),
        (0.58, "both 3 or more", "Counted as the same (both 3 or more); 42% of random hike pairs are this alike"),
        (
            0.16,
            "less than 15 % of the walk apart",
            "Counted as the same (less than 15 % of the walk apart); 84% of random hike pairs are this alike, "
            "so it counts little",
        ),
        (
            0.88,
            "both 100 m or less",
            "Counted as the same (both 100 m or less); 12% of random hike pairs are this alike",
        ),
    ],
)
def test_score_note_says_how_many_random_pairs_are_as_alike_when_the_values_are_the_same(
    score: float, same: str, note: str
) -> None:
    assert score_note(score, 0.8, same) == note
    assert score_note(None, None, same) is None


def test_score_note_on_an_osm_flag_counts_the_pairs_where_either_has_one() -> None:
    pairs = "random hike pairs where either has one"
    assert score_note(0.95, 0.05, "identical", pairs) == (
        "Identical; 5% of random hike pairs where either has one are this alike"
    )
    assert score_note(0.0, 0.95, None, pairs) == (
        "Nothing in common on this measure, like 95% of random hike pairs where either has one"
    )
    assert score_note(None, None, "not compared", pairs) == "Neither has one mapped: not compared"


def test_compare_notes_tell_identical_values_and_values_counted_the_same(data: ServingData) -> None:
    facts = np.array(data.store.facts)
    column = {name: index for index, name in enumerate(FACT_COLUMNS)}
    facts[:, 1] = facts[:, 0]
    # Both beside a lake for about half the walk: inside WaterSide's dead band, not equal.
    facts[column["waterside_share"], :2] = (0.41, 0.53)
    facts[column["water_count"], :2] = (3, 5)  # both "3 or more"
    facts[column["length_m"], :2] = (4000, 9000)
    facts[column["elevation_gain_m"], :2] = (11, 65)  # both under Climbing's 100 m floor
    facts[column["shelter_along"], :2] = 0  # neither has one mapped
    profile = np.repeat(data.store.profile[:1], len(facts[0]), 0)
    # Random-pair scores for every Measure, as a Catalog with land cover has them.
    quantiles = np.tile(np.linspace(0, 1, data.store.quantiles.shape[1]), (len(data.store.quantiles), 1))
    store = dataclasses.replace(data.store, facts=facts, profile=profile, quantiles=quantiles)

    (notes,) = scored_measures(store, 0, np.array([1]), None)

    assert notes["waterside"]["note"].startswith("Counted as the same (less than 15 % of the walk apart); ")
    assert notes["elevation_gain"]["note"].startswith("Counted as the same (both 100 m or less); ")
    assert notes["water_count"]["note"].startswith("Counted as the same (both 3 or more); ")
    assert notes["highest"]["note"].startswith("Identical; ")
    assert notes["round_trip"]["note"].startswith("Identical; ")
    assert notes["length"]["note"].startswith("More alike than")
    assert notes["shelter_along"] == {"score": None, "raw": None, "note": "Neither has one mapped: not compared"}


def test_compare_notes_say_two_dry_hikes_are_not_compared_on_lake_or_sea(data: ServingData) -> None:
    # ADR-0058: two shares under 0.15 are both dry, not compared; a waterside pair counts the pairs
    # where either is beside water.
    facts = np.array(data.store.facts)
    column = FACT_COLUMNS.index("waterside_share")
    facts[column, :4] = (0.0, 0.12, 0.3, 0.6)
    quantiles = np.tile(np.linspace(0, 1, data.store.quantiles.shape[1]), (len(data.store.quantiles), 1))
    store = dataclasses.replace(data.store, facts=facts, quantiles=quantiles)

    dry, wet = scored_measures(store, 0, np.array([1, 2]), None)
    (apart,) = scored_measures(store, 2, np.array([3]), None)

    assert dry["waterside"] == {
        "score": None, "raw": None, "note": "Both dry (less than 15 % of the walk beside water): not compared"
    }
    assert wet["waterside"]["score"] is not None
    assert apart["waterside"]["note"].endswith("random hike pairs where either is beside water")


def test_rank_respects_the_limit_and_the_allowed_rows(data: ServingData) -> None:
    assert len(data.rank(RIDGE_WALK_ID, {"length": 100}, limit=2)) == 2
    allowed = np.array([data.row_of(1003)])
    assert [r["id"] for r in data.rank(RIDGE_WALK_ID, {"length": 100}, allowed_rows=allowed)] == [1003]


def test_rank_fills_its_rows_from_further_down_when_the_best_hikes_share_an_area(
    data: ServingData, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1002 and 1003 start 14 km apart, 1007 farther; no SuggestedLoops read more of the Catalog.
    monkeypatch.setattr(serving, "GROUP_M", 20_000.0)
    monkeypatch.setattr(serving, "LOOP_SEEDS", 0)

    results = data.rank(RIDGE_WALK_ID, {"length": 100}, limit=2)

    assert [(r["id"], [v["id"] for v in r["versions"]]) for r in results] == [(1002, [1003]), (1007, [])]
    assert [len(data.rank(RIDGE_WALK_ID, {"length": 100}, limit=limit)) for limit in (1, 3)] == [1, 2]


def _rows(results: list[dict]) -> list[tuple[int, list[int]]]:
    return [(r["id"], [v["id"] for v in r["versions"]]) for r in results]


def test_the_next_rows_continue_the_rows_listed_exactly(data: ServingData, monkeypatch: pytest.MonkeyPatch) -> None:
    # 1002 and 1003 start 14 km apart, 1007 farther: two areas within 20 km.
    monkeypatch.setattr(serving, "GROUP_M", 20_000.0)
    monkeypatch.setattr(serving, "LOOP_SEEDS", 0)
    first = data.rank(RIDGE_WALK_ID, {"length": 100}, limit=1)

    after = [tuple(row["trailhead"]) for row in first]
    following = data.rank(RIDGE_WALK_ID, {"length": 100}, limit=1, after=after)

    assert _rows(first) == [(1002, [1003])]
    assert _rows(following) == [(1007, [])]
    assert _rows(first + following) == _rows(data.rank(RIDGE_WALK_ID, {"length": 100}, limit=2))
    shown = after + [tuple(row["trailhead"]) for row in following]
    assert data.rank(RIDGE_WALK_ID, {"length": 100}, limit=1, after=shown) == []


def test_the_rows_after_leave_out_every_hike_of_an_area_already_listed(
    data: ServingData, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1003, a version of 1002's row on the first page, never leads a row on the next.
    monkeypatch.setattr(serving, "LOOP_SEEDS", 0)

    following = data.rank(RIDGE_WALK_ID, {"length": 100}, after=[(45.2, 15.1)], group_m=20_000.0)

    assert _rows(following) == [(1007, [])]


def test_the_grouping_radius_is_asked_per_ranking(data: ServingData, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serving, "LOOP_SEEDS", 0)

    assert _rows(data.rank(RIDGE_WALK_ID, {"length": 100}, group_m=20_000.0)) == [(1002, [1003]), (1007, [])]
    assert _rows(data.rank(RIDGE_WALK_ID, {"length": 100}, group_m=0.0)) == [(1002, []), (1003, []), (1007, [])]
    # Without one, today's 10 km: 1002 and 1003, 14 km apart, are rows of their own.
    assert _rows(data.rank(RIDGE_WALK_ID, {"length": 100})) == [(1002, []), (1003, []), (1007, [])]


def test_a_row_counts_every_dayhike_ranked_that_starts_in_its_area(data: ServingData) -> None:
    # Within 20 km 1002 leads 1003 (a version) and 1007 its own area; within 100 m each its own.
    listed = data.rank(RIDGE_WALK_ID, {"length": 100}, group_m=20_000.0)
    alone = data.rank(RIDGE_WALK_ID, {"length": 100}, group_m=100.0)

    assert [(r["id"], r["in_area"]) for r in listed] == [(1002, 2), (1007, 1)]
    assert [(r["id"], r["in_area"]) for r in alone] == [(1002, 1), (1003, 1), (1007, 1)]


def test_a_draw_is_the_same_for_the_same_draw_number_and_lists_its_rows_in_their_order() -> None:
    scores, keys = [0.9, 0.8, 0.8, 0.7, 0.6], ["11", "12", "13", "14", "15"]

    first = drawn(scores, keys, 7, 3)

    assert first == drawn(scores, keys, 7, 3) == sorted(first) and len(set(first)) == 3
    assert drawn(scores, keys, 7, 9) == [0, 1, 2, 3, 4]  # a pool no larger than the page is the page
    assert drawn(scores, keys, 7, 0) == []
    equal = [0.5] * 6
    assert len({tuple(drawn(equal, [str(k) for k in range(6)], draw, 2)) for draw in range(20)}) > 5


def test_a_draw_takes_a_row_with_probability_rising_as_exp_of_its_score_over_t() -> None:
    # Scores T ln 3 apart: the first is drawn 3 times as often as the second.
    scores = [0.6, 0.6 - serving.DRAW_T * np.log(3)]

    first = sum(drawn(scores, ["a", "b"], draw, 1) == [0] for draw in range(4_000))

    assert first / 4_000 == pytest.approx(0.75, abs=0.025)
    assert all(drawn([0.9, 0.5], ["a", "b"], draw, 1) == [0] for draw in range(1_000))  # 80 T apart: never


def test_draw_noise_is_gumbel_noise_from_the_draw_number_and_the_row_key() -> None:
    noise = [draw_noise(draw, "19787033") for draw in range(20_000)]

    assert draw_noise(5, "19787033") == noise[5] and draw_noise(5, "19787034") != noise[5]
    assert np.mean(noise) == pytest.approx(np.euler_gamma, abs=0.03)  # a Gumbel(0, 1)'s mean
    assert np.var(noise) == pytest.approx(np.pi**2 / 6, abs=0.08)


@pytest.mark.parametrize(
    ("max_hours", "max_km", "metres"),
    [
        (None, None, 10_000.0),  # no limit: today's 10 km
        (None, 50.0, 2_500.0),  # 5 % of the distance
        (2.0, None, 5_000.0),  # 2 h at 50 km/h is 100 km
        (4.0, None, 10_000.0),  # the longest drive groups as no limit does
        (2.0, 50.0, 2_500.0),  # both: the smaller
        (2.0, 300.0, 5_000.0),
        (0.1, None, 500.0),  # at least 500 m
        (None, 5.0, 500.0),
    ],
)
def test_the_default_grouping_radius_follows_the_search_limit(
    max_hours: float | None, max_km: float | None, metres: float
) -> None:
    assert serving.group_radius_m(max_hours, max_km) == pytest.approx(metres)


def test_a_distance_limit_keeps_hikes_starting_within_it_in_a_straight_line(data: ServingData) -> None:
    # From 45.2,15.1 (1002's Trailhead): 1003 at 45.3,15.2 is 13.6 km off, 1007 at 45.6,15.5 about 55 km.
    near = (Origin(45.2, 15.1), 20_000.0)

    assert [r["id"] for r in data.rank(RIDGE_WALK_ID, {"length": 100}, near=near, group_m=0.0)] == [1002, 1003]
    assert data.rank(RIDGE_WALK_ID, {"length": 100}, near=(Origin(45.2, 15.1), 1.0)) == [
        {**r, "in_area": 1}  # 0 m groups no hike in its area, not even its own
        for r in data.rank(RIDGE_WALK_ID, {"length": 100}, near=near, group_m=0.0)
        if r["id"] == 1002
    ]


def test_reachable_asks_only_trailheads_within_straight_line_reach(data: ServingData) -> None:
    class Matrix:
        def sources_to_targets(self, origin, trailheads):
            self.asked = [(t.latitude, t.longitude) for t in trailheads]
            return [1800.0, None, 7200.0, 9000.0][: len(trailheads)]

    matrix = Matrix()
    # 45.6,15.5 is about 60 km from the Origin; 0.4 h at 140 km/h reaches 56 km.
    assert data.reachable(Origin(45.1, 15.05), 0.4, matrix) == {}  # 0.5 h is over 0.4 h
    assert matrix.asked == [(45.0, 15.0), (45.2, 15.1), (45.3, 15.2)]
    # Unreachable (None) and over-the-limit times are left out.
    assert data.reachable(Origin(45.1, 15.05), 2.0, matrix) == {0: 0.5, 2: 2.0}
    assert len(matrix.asked) == 4


def test_polyline_round_trips_and_simplify_keeps_the_ends() -> None:
    lat = np.array([45.0, 45.00001, 45.00002, 45.01])
    lon = np.array([15.0, 15.0, 15.00001, 15.0])

    kept_lat, kept_lon = simplify_line(lat, lon, 10.0)

    assert (kept_lat[0], kept_lon[0]) == pytest.approx((45.0, 15.0))
    assert (kept_lat[-1], kept_lon[-1]) == pytest.approx((45.01, 15.0))
    assert len(kept_lat) == 2
    points = decode_polyline(encode_polyline(lat, lon))
    assert points == pytest.approx(list(zip(np.round(lat, 5), np.round(lon, 5))))
    assert encode_polyline(np.array([38.5, 40.7, 43.252]), np.array([-120.2, -120.95, -126.453])) == (
        "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    )


def test_cli_exports_and_can_run_again(catalog_path: Path, tmp_path: Path, capsys) -> None:
    from dayhike.cli import main

    out = tmp_path / "web-serving"
    assert main(["export-serving", "--catalog", str(catalog_path), "--out", str(out)]) == 0
    first = (out / "hikes.sqlite").read_bytes()
    assert main(["export-serving", "--catalog", str(catalog_path), "--out", str(out)]) == 0

    assert (out / "hikes.sqlite").read_bytes() == first
    assert ServingData(out).search("ridge")[0]["id"] == RIDGE_WALK_ID
    assert capsys.readouterr().out.strip() == f"{out}\n{out}"


def test_export_without_valhalla_refuses_trailheads_with_no_snap(catalog_path: Path, tmp_path: Path) -> None:
    np.savez_compressed(tmp_path / "seed.npz", lat=[45.0], lon=[15.0], snap_lat=[45.01], snap_lon=[15.0], metres=[1000.0])

    with pytest.raises(ServingExportError, match="3 Trailheads have no snap"):
        export_serving(catalog_path, tmp_path / "out", snaps_path=tmp_path / "seed.npz")


def test_a_rerun_without_tiles_or_snaps_drops_the_old_ones(catalog_path: Path, tmp_path: Path) -> None:
    tiles = tmp_path / "tiles"
    tiles.mkdir()
    (tiles / "valhalla_tiles.tar").write_bytes(b"tiles")
    (tiles / "valhalla.json").write_text(json.dumps({"mjolnir": {"max_cache_size": 1}, "httpd": {"service": {}}}))
    out = tmp_path / "out"
    trailheads = [(45.0, 15.0), (45.2, 15.1), (45.3, 15.2), (45.6, 15.5)]
    np.savez_compressed(
        tmp_path / "seed.npz",
        lat=[p[0] for p in trailheads], lon=[p[1] for p in trailheads],
        snap_lat=[p[0] for p in trailheads], snap_lon=[p[1] for p in trailheads], metres=[0.0] * 4,
    )  # fmt: skip

    export_serving(catalog_path, out, valhalla_dir=tiles, snaps_path=tmp_path / "seed.npz")
    config = json.loads((out / "valhalla" / "valhalla.json").read_text())
    assert json.loads((out / "meta.json").read_text())["drive_time"] is True
    assert (out / "valhalla" / "valhalla_tiles.tar").read_bytes() == b"tiles"
    assert config["mjolnir"]["tile_extract"] == "/data/valhalla/valhalla_tiles.tar"
    assert config["httpd"]["service"]["listen"] == "tcp://127.0.0.1:8002"

    export_serving(catalog_path, out)
    assert json.loads((out / "meta.json").read_text())["drive_time"] is False
    assert not (out / "snaps.npz").exists() and not (out / "valhalla").exists()
    assert (tiles / "valhalla_tiles.tar").read_bytes() == b"tiles"


def _region(directory: Path, name: str, label: str, continent: str, west: float, east: float):
    """A region file of a box from ``west`` to ``east`` around the test hikes (latitude 44 to 46)."""
    path = directory / f"{name}.toml"
    path.write_text(
        f'label = "{label}"\ncontinent = "{continent}"\nextracts = []\n'
        f"bounds = {{ south = 44.0, west = {west}, north = 46.0, east = {east} }}\n"
        f"[map]\nview = {{ west = {west}, south = 44.0, east = {east}, north = 46.0 }}\ncentre = {{ lon = {west}, lat = 45.0 }}\n"
    )
    return load_region(path)


def test_the_export_names_the_regions_its_hikes_start_in(
    catalog_path: Path, export: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert json.loads((export / "meta.json").read_text())["regions"] == [{"label": "Europe", "continent": "Europe"}]
    # The test hikes start at 15.0, 15.1, 15.2 and 15.5° E: two of them "in the United States", west of 15.15.
    monkeypatch.setattr(serving, "REGION_SPLIT_LON", 15.15)
    both = export_serving(catalog_path, tmp_path / "both")

    assert json.loads((both / "meta.json").read_text())["regions"] == [
        {"label": "Europe", "continent": "Europe"},
        {"label": "the United States", "continent": "North America"},
    ]


def test_the_export_names_its_hikes_regions_as_the_regions_given_and_refuses_regions_they_contradict(
    catalog_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(serving, "REGION_SPLIT_LON", 15.15)  # two test hikes "in the United States"
    west = _region(tmp_path, "west", "Colorado", "North America", 14.9, 15.12)
    east = _region(tmp_path, "east", "the Alps", "Europe", 15.18, 15.6)

    out = export_serving(catalog_path, tmp_path / "out", regions=[west, east])

    assert json.loads((out / "meta.json").read_text())["regions"] == [  # Europe first, as the region filter lists it
        {"label": "the Alps", "continent": "Europe"},
        {"label": "Colorado", "continent": "North America"},
    ]
    with pytest.raises(ServingExportError, match="hikes start in the United States, which no --region names"):
        export_serving(catalog_path, tmp_path / "east-only", regions=[east])
    assert not (tmp_path / "east-only" / "meta.json").exists()  # refused before anything is written
    assert not (tmp_path / "east-only" / "trailheads.npy").exists()
    monkeypatch.setattr(serving, "REGION_SPLIT_LON", -40.0)  # every test hike in Europe
    with pytest.raises(ServingExportError, match="no hike starts in the region us-colorado"):
        export_serving(catalog_path, tmp_path / "us", regions=[load_region("europe"), load_region("us-colorado")])


def test_a_server_names_the_regions_by_its_hikes_for_an_export_from_before_regions(export: Path, tmp_path: Path) -> None:
    old = tmp_path / "old"
    shutil.copytree(export, old)
    meta = json.loads((old / "meta.json").read_text())
    del meta["regions"]
    (old / "meta.json").write_text(json.dumps(meta))
    assert ServingData(old).covered == [{"label": "Europe", "continent": "Europe"}]  # the pages say Europe, as before

    trailheads = np.load(old / "trailheads.npy")
    trailheads[:, 1] -= 120.0
    np.save(old / "trailheads.npy", trailheads)
    assert ServingData(old).covered == [{"label": "the United States", "continent": "North America"}]


CITIES = Path(__file__).parent / "fixtures" / "geonames.txt"


@pytest.fixture(scope="module")
def placed(catalog_path: Path) -> ServingData:
    return ServingData(export_serving(catalog_path, catalog_path.parent / "placed", geonames_path=CITIES))


def test_an_export_without_geonames_has_no_place_search(data: ServingData) -> None:
    assert data.places is None
    assert not (data.directory / "places.sqlite").exists()


def test_place_search_keeps_only_places_near_the_trailheads(placed: ServingData) -> None:
    assert placed.find_place("lisbon") == []
    assert placed.find_place("lissabon") == []
    assert [place["name"] for place in placed.find_place("vo")] == ["Völkermarkt"]


def test_place_search_matches_a_folded_prefix_of_any_name_most_populous_first(placed: ServingData) -> None:
    assert placed.find_place("Za") == [
        {"name": "Zagreb", "country": "HR", "lat": 45.81444, "lon": 15.97798, "population": 698966},
        {"name": "Zaprešić", "country": "HR", "lat": 45.85639, "lon": 15.80778, "population": 19644},
    ]
    assert [place["name"] for place in placed.find_place("zapresic")] == ["Zaprešić"]
    assert [place["name"] for place in placed.find_place("  AGRAM ")] == ["Zagreb"]
    assert [place["name"] for place in placed.find_place("Загреб")] == ["Zagreb"]
    assert [place["name"] for place in placed.find_place("Voelkermarkt")] == ["Völkermarkt"]
    assert [place["name"] for place in placed.find_place("za", limit=1)] == ["Zagreb"]
    assert placed.find_place("greb") == [] and placed.find_place(" ") == []


def test_place_search_finds_a_village_a_hike_starts_from_under_its_own_names(placed: ServingData) -> None:
    assert placed.find_place("planina") == [
        {"name": "Planina", "country": "HR", "lat": 45.31, "lon": 15.21, "population": 0}
    ]
    # A village keeps its name and ASCII name only: its alternate names would cost the most room.
    assert placed.find_place("alpe") == []
    # Far from every Trailhead (though inside the towns' margin), a hamlet is not kept;
    # nor is a feature that is no place (a mountain) or a section of one.
    assert placed.find_place("daleka") == []
    assert placed.find_place("kapelica") == [] and placed.find_place("gornji") == []


def test_place_search_puts_its_name_then_another_exact_name_before_a_more_populous_prefix(
    placed: ServingData,
) -> None:
    assert [place["name"] for place in placed.find_place("vrh")] == ["Vrh", "Vrhnika"]
    assert [place["name"] for place in placed.find_place("velikovec")] == ["Völkermarkt", "Velikovecki Breg"]


def test_place_choices_are_the_places_named_so_or_one_typo_away(placed: ServingData) -> None:
    names = lambda found: [place["name"] for place in found[0]]  # noqa: E731
    assert placed.place_choices("zag") == (placed.find_place("zag"), False)
    # A country after a comma keeps the places there; any other words after it (a county, a region) are left out.
    assert names(placed.place_choices("Zagreb, Croatia")) == ["Zagreb"]
    assert names(placed.place_choices("Vrh, Slovenia")) == ["Vrhnika"] and placed.place_choices("Vrh, Austria") == ([], False)
    assert names(placed.place_choices("Zagreb, Grad Zagreb")) == ["Zagreb"]
    # With no name starting so, the names one typo away are offered as guesses.
    assert placed.place_choices("Zagerb") == (placed.find_place("zagreb"), True)
    assert placed.place_choices("Ogullin")[1] is True and names(placed.place_choices("Ogullin")) == ["Ogulin"]
    assert placed.place_choices("Qqqqqq") == ([], False)
    # A typo before a country is guessed among the places there.
    assert placed.place_choices("Zagerb, Croatia") == (placed.find_place("zagreb"), True)
    assert placed.place_choices("Zagerb, Austria") == ([], False)


def test_place_file_can_be_added_beside_an_existing_export(catalog_path: Path, tmp_path: Path, capsys) -> None:
    from dayhike.cli import main

    out = export_serving(catalog_path, tmp_path / "out")
    meta = (out / "meta.json").read_bytes()

    assert main(["export-places", "--geonames", str(CITIES), "--out", str(out)]) == 0

    assert (out / "meta.json").read_bytes() == meta
    assert [place["name"] for place in ServingData(out).find_place("ogulin")] == ["Ogulin"]
    assert [landmark["kind"] for landmark in ServingData(out).find_landmarks("ridge lake")] == ["lake"]
    assert capsys.readouterr().out.split() == [str(out / "places.sqlite"), str(out / "landmarks.sqlite")]


def test_place_file_reads_the_geonames_zip(catalog_path: Path, tmp_path: Path) -> None:
    import zipfile

    with zipfile.ZipFile(tmp_path / "allCountries.zip", "w") as archive:
        archive.write(CITIES, "geonames.txt")
    out = export_serving(catalog_path, tmp_path / "out", geonames_path=tmp_path / "allCountries.zip")

    assert [place["name"] for place in ServingData(out).find_place("ogulin")] == ["Ogulin"]


def test_a_rerun_without_geonames_drops_the_old_place_file(catalog_path: Path, tmp_path: Path) -> None:
    out = export_serving(catalog_path, tmp_path / "out", geonames_path=CITIES)
    export_serving(catalog_path, out)

    assert ServingData(out).places is None


def test_geonames_is_downloaded_only_when_missing(tmp_path: Path) -> None:
    from dayhike.serving import GEONAMES_URL, fetch_geonames

    asked = []

    def urlretrieve(url: str, path: str) -> None:
        asked.append(url)
        Path(path).write_bytes(b"zip")

    target = tmp_path / "geonames" / "allCountries.zip"
    assert fetch_geonames(target, urlretrieve) == target
    assert fetch_geonames(target, urlretrieve) == target
    assert asked == [GEONAMES_URL] == ["https://download.geonames.org/export/dump/allCountries.zip"]


def test_landmarks_are_the_named_points_on_hikes_with_the_hikes_nearest_first(data: ServingData) -> None:
    [peak] = data.find_landmarks("Ridge Peak")

    assert {key: peak[key] for key in ("name", "kind", "town", "lat", "lon", "match", "hike_count")} == {
        "name": "Ridge Peak", "kind": "peak", "town": None, "lat": 45.00764, "lon": 15.0, "match": "words", "hike_count": 1,
    }  # fmt: skip
    assert [(hike["id"], hike["off_m"]) for hike in peak["hikes"]] == [(RIDGE_WALK_ID, 0)]
    assert {key for key in peak["hikes"][0]} == {*data._summaries([0])[0], "off_m"}
    # From any word's start: a word inside the name, or the start of one.
    assert [landmark["name"] for landmark in data.find_landmarks("peak")] == ["Ridge Peak"]
    assert [landmark["kind"] for landmark in data.find_landmarks("RIDGE VI")] == ["viewpoint"]
    # A cafe names no Landmark, an unnamed hut neither, and a letter alone finds nothing.
    assert data.find_landmarks("ridge cafe") == [] and data.find_landmarks("r") == []
    assert data.find_landmarks("idg") == []  # not from a word's start, and too short for a typo


def test_a_landmark_typed_with_a_typo_is_found_and_said_to_be_similar(data: ServingData) -> None:
    [peak] = data.find_landmarks("Rigde Peak")

    assert (peak["name"], peak["match"]) == ("Ridge Peak", "similar")


def test_a_landmark_named_exactly_comes_before_one_named_from_it(placed: ServingData) -> None:
    # Ridge Peak, Ridge View, Ridge Lake and Ridge Saddle all start with "ridge"; the lake's last word is "lake".
    assert placed.find_landmarks("ridge lake")[0]["name"] == "Ridge Lake"
    assert len(placed.find_landmarks("ridge")) == 4
    assert [landmark["name"] for landmark in placed.find_landmarks("ridge", limit=2)] == [
        landmark["name"] for landmark in placed.find_landmarks("ridge")[:2]
    ]


def test_among_equal_names_the_highest_peak_comes_first_then_the_most_hikes() -> None:
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE landmarks (id INTEGER PRIMARY KEY, name TEXT, kind TEXT, town TEXT, lat REAL, lon REAL, ele REAL, hikes INTEGER);"
        "CREATE TABLE landmark_names (name TEXT, landmark INTEGER, whole INTEGER);"
        "INSERT INTO landmarks VALUES (1, 'Mont Blanc', 'peak', 'Hesdin', 50, 2, 120, 6), (2, 'Mont Blanc', 'peak', 'Chamonix', 45.8, 6.9, 4806, 2),"
        " (3, 'Mont Blanc', 'village', NULL, 46, 6, NULL, 9), (4, 'Le Mont Blanc', 'peak', NULL, 45, 6, 4810, 40);"
        "INSERT INTO landmark_names VALUES ('mont blanc', 1, 1), ('mont blanc', 2, 1), ('mont blanc', 3, 1),"
        " ('le mont blanc', 4, 1), ('mont blanc', 4, 0);"
    )

    assert [row[0] for row in ServingData._landmarks_named(db, "mont blanc", 5)] == [2, 1, 3, 4]


def test_geonames_lakes_and_passes_near_a_hike_are_landmarks_under_every_name(placed: ServingData) -> None:
    [lake] = placed.find_landmarks("Grebensko jezero")
    [saddle] = placed.find_landmarks("ridge saddle")

    assert (lake["name"], lake["kind"]) == ("Ridge Lake", "lake")
    assert [hike["id"] for hike in lake["hikes"]] == [RIDGE_WALK_ID]
    assert 750 < lake["hikes"][0]["off_m"] < 820  # 0.01° of longitude east of the line at 45°
    assert (saddle["kind"], saddle["hikes"][0]["off_m"]) == ("pass", 0)
    # A lake no hike passes within LANDMARK_FAR_M of is not kept.
    assert placed.find_landmarks("far lake") == []


def test_geonames_mountains_the_points_lack_are_landmarks(placed: ServingData) -> None:
    # Kapelica is a GeoNames mountain no OSM Peak names: its height is the elevation model's there.
    [mountain] = placed.find_landmarks("kapelica")

    assert (mountain["kind"], [hike["id"] for hike in mountain["hikes"]]) == ("peak", [RIDGE_WALK_ID])


def test_a_landmark_no_hike_passes_near_is_on_the_hikes_farther_off(placed: ServingData) -> None:
    # Big Lake's GeoNames point is about 4.7 km west of Ridge Walk, the nearest hike: a big lake's middle.
    [lake] = placed.find_landmarks("grosser see")

    assert (lake["name"], lake["kind"], [hike["id"] for hike in lake["hikes"]]) == ("Big Lake", "lake", [RIDGE_WALK_ID])
    assert serving.LANDMARK_NEAR_M < lake["hikes"][0]["off_m"] < serving.LANDMARK_FAR_M


def test_a_geonames_peak_at_an_osm_peak_of_its_name_adds_its_names_to_it(placed: ServingData) -> None:
    # GeoNames has Ridge Peak 120 m from the OSM Peak, also as Grebenski vrh: one Landmark under both names.
    assert [(landmark["name"], landmark["lat"]) for landmark in placed.find_landmarks("grebenski vrh")] == [
        ("Ridge Peak", 45.00764)
    ]
    assert len(placed.find_landmarks("ridge peak")) == 1


def test_among_equal_names_the_one_with_most_names_comes_first(placed: ServingData) -> None:
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE landmarks (id INTEGER PRIMARY KEY, name TEXT, kind TEXT, town TEXT, lat REAL, lon REAL, ele REAL,"
        " hikes INTEGER, names INTEGER);"
        "CREATE TABLE landmark_names (name TEXT, landmark INTEGER, whole INTEGER);"
        # A tarn named Bodensee high in the Alps with many hikes, and Lake Constance, known in 99 languages.
        "INSERT INTO landmarks VALUES (1, 'Bodensee', 'lake', 'Haus', 47.4, 13.8, NULL, 8, 1),"
        " (2, 'Bodensee', 'lake', 'Lindau', 47.6, 9.5, NULL, 3, 99), (3, 'Mont Blanc', 'peak', 'Aumale', 50, 2, 174, 3, 1),"
        " (4, 'Mont Blanc', 'peak', 'Chamonix', 45.8, 6.9, 4806, 2, 70), (5, 'Radolfzell', 'town', NULL, 47.7, 9, NULL, 92, 3);"
        "INSERT INTO landmark_names VALUES ('bodensee', 1, 1), ('bodensee', 2, 1), ('mont blanc', 3, 1), ('mont blanc', 4, 1),"
        " ('radolfzell am bodensee', 5, 1), ('bodensee', 5, 0);"
    )

    assert [row[0] for row in ServingData._landmarks_named(db, "bodensee", 5, "l.names")] == [2, 1, 5]
    assert [row[0] for row in ServingData._landmarks_named(db, "mont blanc", 5, "l.names")] == [4, 3]
    assert placed._landmark_names == "l.names"  # an export of now has the column


def test_a_place_is_a_landmark_with_the_hikes_starting_near_it(placed: ServingData) -> None:
    village = placed.find_landmarks("vrh")[0]  # then Ridge Peak, also Grebenski vrh

    # Told apart by the town nearest it and that town's country.
    assert (village["name"], village["kind"], village["town"], village["country"]) == ("Vrh", "village", "Ogulin", "HR")
    start = placed.search("Valley Path")[0]
    assert village["hikes"][0]["id"] == start["id"]
    assert 1_000 < village["hikes"][0]["off_m"] < 2_000  # Vrh (45.21, 15.11) from the start at (45.2, 15.1)
    # A town no hike starts within VILLAGE_M of is no Landmark (Vrhnika is in Slovenia, far from every hike).
    assert [landmark["name"] for landmark in placed.find_landmarks("vrhnika")] == []


def test_a_landmark_lists_its_nearest_hikes_and_counts_them_all(
    catalog_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(serving, "LANDMARK_HIKES", 1)
    monkeypatch.setattr(serving, "VILLAGE_M", 50_000.0)  # every hike starts "near" Ogulin
    out = export_serving(catalog_path, tmp_path / "out", geonames_path=CITIES)
    [ogulin] = ServingData(out).find_landmarks("ogulin")

    assert ogulin["kind"] == "town" and len(ogulin["hikes"]) == 1 < ogulin["hike_count"]


def test_landmarks_can_be_added_beside_an_existing_export_and_an_older_one_has_none(
    catalog_path: Path, tmp_path: Path
) -> None:
    out = export_serving(catalog_path, tmp_path / "out")
    (out / "landmarks.sqlite").unlink()
    older = ServingData(out)

    assert older.landmarks is None and older.find_landmarks("ridge peak") == []
    serving.export_landmarks(out)
    assert [landmark["name"] for landmark in ServingData(out).find_landmarks("ridge peak")] == ["Ridge Peak"]


def test_a_landmark_file_from_before_names_were_counted_still_opens(catalog_path: Path, tmp_path: Path) -> None:
    out = export_serving(catalog_path, tmp_path / "out", geonames_path=CITIES)
    with sqlite3.connect(out / "landmarks.sqlite") as db:
        db.execute("ALTER TABLE landmarks DROP COLUMN names")
    older = ServingData(out)

    assert older._landmark_names == "NULL"
    assert [landmark["name"] for landmark in older.find_landmarks("grebenski vrh")] == ["Ridge Peak"]


def test_a_hike_says_the_town_and_country_it_starts_near(placed: ServingData, data: ServingData) -> None:
    # Valley Path starts 12 km from Ogulin; Ridge Walk 35 km from it, past LANDMARK_TOWN_M, near no town.
    [valley] = [hike for hike in placed.search("Valley Path") if hike["id"] == 1002]
    [ridge] = placed.search("Ridge Walk")

    assert (valley["region"], ridge["region"]) == ("Ogulin, HR", None)
    assert placed.detail(1002)["region"] == "Ogulin, HR"
    assert data.search("Valley Path")[0]["region"] is None  # an export without places names none


def _famous(
    placed: ServingData,
    out: Path,
    hikes: list[tuple[str, dict]],
    landmarks: list[tuple] = (),
    towns: dict[str, str] | None = None,
) -> ServingData:
    """A copy of the placed export whose DayHikes, in id order, have these names and columns: ``points``
    as (kind, name, metres off the line), ``parking_m`` and ``commons_contributors``. Given ``landmarks``,
    only these: (name, kind, lat, lon, how many names, [(hike index, metres off)]); given ``towns``,
    these towns of ``places.sqlite`` in this country."""
    shutil.copytree(placed.directory, out)
    with scoped(sqlite3.connect(out / "hikes.sqlite")) as db:
        ids = [dayhike_id for (dayhike_id,) in db.execute("SELECT id FROM hikes ORDER BY id")]
        for dayhike_id, (name, columns) in zip(ids, hikes, strict=True):
            points = [[kind, "line", 45.0, 15.0, 0, off_m, point, None] for kind, point, off_m in columns.get("points", [])]
            db.execute(
                "UPDATE hikes SET name = ?, points = ?, parking_m = ?, commons_contributors = ? WHERE id = ?",
                (name, zlib.compress(json.dumps(points).encode()), columns.get("parking_m"), columns.get("commons"), dayhike_id),
            )
        db.commit()
    export_name_index(out)
    if landmarks:
        with scoped(sqlite3.connect(out / "landmarks.sqlite")) as db:
            db.executescript("DELETE FROM landmarks; DELETE FROM landmark_hikes; DELETE FROM landmark_names;")
            for landmark, (name, kind, lat, lon, names, near) in enumerate(landmarks, start=1):
                db.execute(
                    "INSERT INTO landmarks VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, ?)", (landmark, name, kind, lat, lon, len(near), names)
                )
                db.executemany("INSERT INTO landmark_hikes VALUES (?, ?, ?)", [(landmark, ids[i], off_m) for i, off_m in near])
                folded = _fold(name)
                db.executemany(
                    "INSERT INTO landmark_names VALUES (?, ?, ?)",
                    [(folded[word.start() :], landmark, word.start() == 0) for word in re.finditer(r"\w+", folded)],
                )
            db.commit()
    with scoped(sqlite3.connect(out / "places.sqlite")) as db:
        for town, country in (towns or {}).items():
            db.execute("UPDATE places SET country = ? WHERE name = ?", (country, town))
        db.commit()
    return ServingData(out)


def test_a_famous_peak_named_so_leads_the_hike_names_and_its_namesakes_do_not(placed: ServingData, tmp_path: Path) -> None:
    # "Matterhorn" listed Matterhorn Trail in Oregon first; the Swiss Matterhorn has 48 names in GeoNames.
    data = _famous(placed, tmp_path / "matterhorn", [
        ("Matterhorn Trail", {"points": [("peak", "Matterhorn", 0)]}),
        ("Hörnli Ridge from Schwarzsee", {"points": [("peak", "Matterhorn", 30)]}),
        ("Disappointment Cleaver Route", {}),
        ("Overlook from Mount Rainier", {}),
    ], [
        ("Matterhorn", "peak", 45.0, 15.0, 1, [(0, 0)]),
        ("Matterhorn", "peak", 45.2, 15.1, 48, [(1, 30)]),
        ("Mount Rainier", "peak", 45.0, 15.0, 43, [(2, 0)]),
        ("Mount Rainier", "town", 45.6, 15.5, 19, [(3, 900)]),  # a town known in many languages: no peak
        ("Matterhornblick", "viewpoint", 45.0, 15.0, 30, [(0, 10)]),  # named from it, not so
    ])  # fmt: skip

    found = data.find_landmarks("Matterhorn")
    assert [(landmark["name"], landmark["leads"], [hike["name"] for hike in landmark["hikes"]]) for landmark in found] == [
        ("Matterhorn", True, ["Hörnli Ridge from Schwarzsee"]),
        ("Matterhorn", False, ["Matterhorn Trail"]),
        ("Matterhornblick", False, ["Matterhorn Trail"]),
    ]
    assert [(landmark["kind"], landmark["leads"]) for landmark in data.find_landmarks("Mount Rainier")] == [
        ("peak", True), ("town", False)
    ]
    assert [landmark["leads"] for landmark in data.find_landmarks("Matterhorm")] == [False, False, False]  # a typo: similar


def test_a_place_named_so_leads_when_more_photographed_than_the_hikes_named_best_like_it(
    placed: ServingData, tmp_path: Path
) -> None:
    # "Grand Canyon" listed a 1.6 km walk of that name near Cairo, New York, first: no Commons photo is near it.
    hikes = [
        ("Grand Canyon", {"commons": 0}),
        ("Yavapai Point from Grand Canyon Village", {"commons": 41}),
        ("Eagle Heights from Middleton Junction", {"commons": 5}),
        ("Valley Path", {}),
    ]
    landmarks = [
        ("Grand Canyon", "town", 45.2, 15.1, 5, [(1, 800)]),
        ("Grand Canyon", "shelter", 45.0, 15.0, 1, [(2, 10)]),  # named so too, less photographed
    ]
    data = _famous(placed, tmp_path / "canyon", hikes, landmarks)

    assert [(landmark["kind"], landmark["leads"]) for landmark in data.find_landmarks("Grand Canyon")] == [
        ("town", True), ("shelter", False)
    ]
    hikes[0] = ("Grand Canyon", {"commons": 50})  # the walk named so photographed most: it stays first
    data = _famous(placed, tmp_path / "walk", hikes, landmarks)
    assert [landmark["leads"] for landmark in data.find_landmarks("Grand Canyon")] == [False, False]


def test_a_hike_to_a_summit_from_a_car_park_comes_before_one_starting_far_from_one(placed: ServingData, tmp_path: Path) -> None:
    # "Half Dome" listed Half Dome Trail, its last 3.2 km starting 4.6 km from a car park, before the hike.
    data = _famous(placed, tmp_path / "half-dome", [
        ("Half Dome Trail", {"points": [("peak", "Half Dome", 0)], "parking_m": 4578}),
        ("Half Dome via Subdome from Valley Trailhead Parking", {"points": [("peak", "Half Dome", 3)], "parking_m": 71}),
        ("Half Dome View", {"points": [("peak", "Half Dome", 900)], "parking_m": 10}),
        ("Valley Path", {}),
    ], [("Half Dome", "peak", 45.0, 15.0, 35, [(0, 0), (1, 3), (2, 900)])])  # fmt: skip

    assert [hit["name"] for hit in data.search("Half Dome")] == [
        "Half Dome via Subdome from Valley Trailhead Parking",
        "Half Dome Trail",  # reaches the top too
        "Half Dome View",
    ]
    [peak] = data.find_landmarks("Half Dome")
    assert [hike["name"] for hike in peak["hikes"]] == [
        "Half Dome via Subdome from Valley Trailhead Parking", "Half Dome Trail", "Half Dome View"
    ]


def test_mt_is_mount_in_hike_and_landmark_names(placed: ServingData, tmp_path: Path) -> None:
    # "Mt Whitney" found nothing though Mount Whitney Trail is there.
    data = _famous(placed, tmp_path / "mount", [
        ("Mount Whitney Trail", {}),
        ("Mt. Hood Trail", {}),
        ("Valley Path", {}),
        ("Smt Walk", {}),
    ], [
        ("Mount Whitney", "peak", 45.0, 15.0, 47, [(0, 0)]),
        ("Mt. Tom", "peak", 45.0, 15.0, 1, [(1, 0)]),
        ("Mtskheta", "town", 45.0, 15.0, 30, [(2, 0)]),
    ])  # fmt: skip

    for typed, name in [
        ("Mt Whitney", "Mount Whitney Trail"), ("mt. whitney", "Mount Whitney Trail"), ("Mount Whitney Trail", "Mount Whitney Trail"),
        ("Mount Hood", "Mt. Hood Trail"), ("Mt Hood", "Mt. Hood Trail"),
    ]:  # fmt: skip
        assert [hit["name"] for hit in data.search(typed)][:1] == [name], typed
    assert [hit["name"] for hit in data.search("smt")] == ["Smt Walk"]
    assert [landmark["name"] for landmark in data.find_landmarks("Mt Whitney")] == ["Mount Whitney"]
    assert [landmark["name"] for landmark in data.find_landmarks("Mount Tom")] == ["Mt. Tom"]
    # "Mount" (on the way to "Mount Tom") finds the names spelled "Mt", not every name starting with "mt"
    assert [landmark["name"] for landmark in data.find_landmarks("Mount")] == ["Mount Whitney", "Mt. Tom"]
    assert serving._unshortened("mt. whitney via mt hood, mtb and smt") == "mount whitney via mount hood, mtb and smt"


def test_a_query_ending_in_a_country_finds_the_hikes_and_landmarks_there(placed: ServingData, tmp_path: Path) -> None:
    # "Triglav, Slovenia" found nothing though Triglav finds the hikes.
    hikes = [
        ("Triglav via Dom Planika from Pri lesi", {}),
        ("Triglav View from Pri Cerkvi", {}),
        ("Valley Path", {}),
        ("Triglav Lake", {}),  # starts near Velikovecki Breg
    ]
    data = _famous(placed, tmp_path / "triglav", hikes, towns={"Ogulin": "SI"})

    assert len(data.search("Triglav")) == 3
    for typed in ("Triglav, Slovenia", "triglav,slovenia"):
        assert {hit["name"] for hit in data.search(typed)} == {
            "Triglav via Dom Planika from Pri lesi", "Triglav View from Pri Cerkvi"
        }, typed
    assert [hit["name"] for hit in data.search("Triglav, Croatia")] == ["Triglav Lake"]
    assert [(landmark["name"], landmark["country"]) for landmark in data.find_landmarks("Vrh, Slovenia")] == [
        ("Vrh", "SI")  # not Ridge Peak (Grebenski vrh): no town within LANDMARK_TOWN_M says its country
    ]
    assert data.find_landmarks("Vrh, Croatia") == [] and data.search("Triglav, Austria") == []


def test_a_country_narrows_a_search_past_the_names_it_scans(
    placed: ServingData, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # "Bear Lake, Utah": the first SEARCH_SCAN_CAP names with its words were all elsewhere, so it found none.
    data = _famous(placed, tmp_path / "scan", [
        ("Triglav Loop", {}), ("Triglav Ridge", {}), ("Valley Path", {}), ("Triglav Lake", {}),
    ], towns={"Ogulin": "SI"})  # fmt: skip
    monkeypatch.setattr(serving, "SEARCH_SCAN_CAP", 1)

    assert [hit["name"] for hit in data.search("Triglav, Croatia")] == ["Triglav Lake"]


def test_a_place_tail_is_a_country_after_a_comma() -> None:
    assert serving._place_tail("triglav, slovenia") == ("triglav", "SI")
    assert serving._place_tail("triglav slovenia") is None  # no comma: the words as typed
    assert serving._place_tail("slovenia") is None and serving._place_tail(", slovenia") is None
    assert serving._place_tail("valley path") is None and serving._place_tail("triglav, nowhere") is None


def _placed_renamed(catalog_path: Path, out: Path, names: dict[int, str], origin: str = NAME_ORIGIN_OSM_NAME) -> ServingData:
    """A placed export whose hikes have the ``names`` ingest's naming would give them, from ``origin``."""
    directory = export_serving(catalog_path, out, geonames_path=CITIES)
    with sqlite3.connect(directory / "hikes.sqlite") as db:
        db.executemany(
            "UPDATE hikes SET name = ?, name_origin = ? WHERE id = ?", [(name, origin, i) for i, name in names.items()]
        )
    export_name_index(directory)
    return ServingData(directory)


def test_a_hike_named_near_a_place_says_that_place_where_it_starts_then_the_town(
    catalog_path: Path, tmp_path: Path
) -> None:
    # Ingest adds the place nearest the Trailhead to an OSM name two hikes share, a hamlet as well as a town
    # ("Wicklow Way near Clonegal"); the town nearest it, for where the hike is, can be another (Bunclody).
    renamed = _placed_renamed(catalog_path, tmp_path / "out", {1002: "Valley Path near Vrh", 1001: "Hike near Ogulin (3)"})
    [valley] = [hike for hike in renamed.search("Valley Path") if hike["id"] == 1002]

    assert valley["region"] == renamed.detail(1002)["region"] == "Vrh, Ogulin, HR"
    assert renamed.search("Hike near Ogulin")[0]["region"] is None  # Ridge Walk starts near no town


@pytest.mark.parametrize(
    "name",
    [
        "Hike near Ogulin (JK11)",  # the town it starts near, named once
        "Hike near Planina, stage 2: Vrh – Ogulin",  # a stage of a tour that starts elsewhere
        "Hike near Planina (P+ (P – P+))",  # a code that holds brackets of its own
    ],
)
def test_a_hike_whose_name_names_no_other_start_says_the_town(catalog_path: Path, tmp_path: Path, name: str) -> None:
    renamed = _placed_renamed(catalog_path, tmp_path / "out", {1002: name}, NAME_ORIGIN_PLACE)

    assert renamed.detail(1002)["region"] == "Ogulin, HR"


@pytest.mark.parametrize(
    ("name", "origin", "note"),
    [
        (
            "Hike near Planina (JK11)",
            NAME_ORIGIN_PLACE,
            "Named by this site after Planina, a place near where it starts: its source gives it no name "
            "that says where it is. “JK11” is the name its source gives it, a code.",
        ),
        (
            "Valley Path near Vrh (2)",
            NAME_ORIGIN_OSM_NAME,
            "“near Vrh” is added by this site: Vrh is a place near where it starts. “(2)” is its "
            "number in its source, or tells it apart from other hikes of the same name.",
        ),
        ("Valley Path", NAME_ORIGIN_OSM_NAME, None),
    ],
)
def test_a_name_this_site_made_says_how(
    catalog_path: Path, tmp_path: Path, name: str, origin: str, note: str | None
) -> None:
    renamed = _placed_renamed(catalog_path, tmp_path / "out", {1002: name}, origin)

    assert renamed.detail(1002)["name_note"] == note


@pytest.mark.parametrize(
    "name",
    [
        "Circular walk near Ambleside",  # a place the export does not know
        "Circular walk near Planina",  # a village 15 km from where it starts, and no town
    ],
)
def test_an_osm_name_with_its_own_near_is_not_said_to_be_made_by_this_site(
    catalog_path: Path, tmp_path: Path, name: str
) -> None:
    renamed = _placed_renamed(catalog_path, tmp_path / "out", {1002: name})
    [walk] = renamed.search("Circular walk")

    assert renamed.detail(1002)["name_note"] is None
    assert walk["region"] == renamed.detail(1002)["region"] == "Ogulin, HR"


def test_an_osm_code_renamed_to_a_placeholder_says_this_site_named_it(catalog_path: Path, tmp_path: Path) -> None:
    renamed = _placed_renamed(catalog_path, tmp_path / "out", {1002: "Hike near Planina (JK11)"})

    assert renamed.detail(1002)["name_note"] == (
        "Named by this site after Planina, a place near where it starts: its source gives it no name "
        "that says where it is. “JK11” is the name its source gives it, a code."
    )
    assert renamed.detail(1002)["region"] == "Planina, Ogulin, HR"


def test_a_hike_says_how_far_its_start_is_from_the_nearest_car_park(data: ServingData, tmp_path: Path, export: Path) -> None:
    # Ridge Walk's car park is 400 m due north of its Trailhead; none is within 20 km of Valley Path's.
    assert data.detail(RIDGE_WALK_ID)["parking_m"] == pytest.approx(400, abs=1)
    assert data.detail(1002)["parking_m"] is None

    older = tmp_path / "older"
    shutil.copytree(export, older)
    with sqlite3.connect(older / "hikes.sqlite") as db:
        db.execute("ALTER TABLE hikes DROP COLUMN parking_m")
    assert ServingData(older).detail(RIDGE_WALK_ID)["parking_m"] is None  # an export from before has none


def test_a_hike_longer_than_a_multi_day_length_one_way_is_said_to_be_multi_day(
    data: ServingData, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(serving, "MULTI_DAY_M", 7_000.0)  # Ridge Walk (10 km one way) is multi-day here

    [ridge] = data.search("Ridge Walk")
    assert (ridge["multi_day"], round(ridge["walk_m"])) == (True, 10_000)
    assert data.detail(RIDGE_WALK_ID)["multi_day"] is True and data.detail(1002)["multi_day"] is False
    assert {hike["id"]: hike["multi_day"] for hike in data.in_box(44, 14, 46, 16, 10)[0]}[RIDGE_WALK_ID] is True


def test_a_day_hikes_ranking_lists_the_multi_day_routes_after_every_day_hike(
    data: ServingData, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A 9 km wish, one way: Ridge Walk (10 km) is the closest, the others are 5 km.
    wish, weights = {"length": 9_000.0}, {"length": 100.0}
    plain = data.rank(wish, weights, limit=10, return_to_start=False)
    monkeypatch.setattr(serving, "MULTI_DAY_M", 9_500.0)  # Ridge Walk is multi-day here, the wish is not
    ranking = data.rank(wish, weights, limit=10, return_to_start=False)

    assert plain[0]["id"] == RIDGE_WALK_ID and plain.day_first is True
    assert ranking.day_first is True and ranking[-1]["id"] == RIDGE_WALK_ID and ranking[-1]["multi_day"] is True
    assert [row["id"] for row in ranking] == [row["id"] for row in plain[1:]] + [RIDGE_WALK_ID]
    assert ranking[-1]["score"] == plain[0]["score"]  # the score is kept; only its place moves
    # From a multi-day Seed, or a Wish of a multi-day length, they rank by score alone.
    assert data.rank({"length": 9_600.0}, weights, limit=10, return_to_start=False)[0]["id"] == RIDGE_WALK_ID
    assert data.rank(RIDGE_WALK_ID, weights, limit=10).day_first is False


def test_a_wish_asks_for_multi_day_routes_by_its_length_one_way(
    data: ServingData, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(serving, "MULTI_DAY_M", 9_500.0)  # Ridge Walk (10 km one way) is multi-day here
    wish, weights = {"length": 15_000.0}, {"length": 100.0}

    # With Return to start 15 km is the whole walk, about 7.5 km out and back: a day hike.
    assert data.rank(wish, weights, limit=10, return_to_start=True).day_first is True
    # Without it 15 km is one way, over the multi-day length.
    assert data.rank(wish, weights, limit=10, return_to_start=False).day_first is False
    assert data.rank({"length": 19_600.0}, weights, limit=10, return_to_start=True).day_first is False


def test_the_rows_after_leave_out_the_twin_of_a_hike_already_listed(
    data: ServingData, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1002 and 1003 are Twins starting 14 km apart, just over the 13 km grouping radius: rows of their own.
    monkeypatch.setattr(serving, "GROUP_M", 13_000.0)
    monkeypatch.setattr(serving, "LOOP_SEEDS", 0)
    rows = data.store.ids.searchsorted([1002, 1003])
    twins = np.array(data.store.twins)
    twins[rows[1]] = twins[rows[0]]
    store = dataclasses.replace(
        data.store, twins=twins, with_way_back=dataclasses.replace(data.store.with_way_back, twins=twins)
    )
    monkeypatch.setattr(data, "store", store)
    first = data.rank(RIDGE_WALK_ID, {"length": 100}, limit=1)

    following = data.rank(RIDGE_WALK_ID, {"length": 100}, limit=1, after=[tuple(row["trailhead"]) for row in first])

    assert _rows(first) == [(1002, [])]
    assert _rows(following) == [(1007, [])]


def test_every_ratio_measure_says_its_floor_in_words() -> None:
    from dayhike.similarity import MEASURES

    ratio = {measure.name: measure.constant for measure in MEASURES if measure.kind == "ratio"}
    assert set(FLOOR_TEXT) == set(ratio)
    assert FLOOR_TEXT["elevation_gain"] == "both 100 m or less"
    assert FLOOR_TEXT["climb_grade"] == "both 3 % or less"


def test_an_open_similarity_store_holds_no_file_descriptor_for_its_arrays(export: Path) -> None:
    import gc

    from open_files import open_under

    from dayhike.similarity import SimilarityStore

    gc.collect()  # what the export's build left to collect is not the arrays'
    opened = [SimilarityStore.open_dir(export / "similarity") for _ in range(5)]

    assert open_under(export / "similarity") == []
    assert all(len(held.ids) == 4 for held in opened)
