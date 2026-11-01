import dataclasses
import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from dayhike import Catalog, build_catalog, build_similarity, rank_dayhikes
from dayhike import serving
from dayhike.catalog import Origin
from dayhike.serving import (
    ELEVATION_POINTS,
    ServingData,
    ServingExportError,
    _fold,
    _relevance,
    decode_polyline,
    drawn_profile,
    encode_polyline,
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

    assert meta["format"] == 4
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
    assert [hit["id"] for hit in data.search("1002")] == [1002]
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


def test_search_relevance_puts_exact_then_prefix_then_word_starts_first() -> None:
    assert _relevance("triglav", "triglav", ["triglav"]) == 0
    assert _relevance("triglav north face", "triglav", ["triglav"]) == 1
    assert _relevance("dolic - triglav", "triglav dolic", ["triglav", "dolic"]) == 2
    assert _relevance("pod triglavom", "riglav", ["riglav"]) == 3


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
    assert results[0]["measures"]["elevation_gain"] == {"score": None, "raw": None, "note": None}


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
            "within the dead band",
            "Counted as the same (within the dead band); 84% of random hike pairs are this alike, so it counts little",
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
    facts[column["shelter_along"], :2] = 0  # neither has one mapped
    profile = np.repeat(data.store.profile[:1], len(facts[0]), 0)
    # Random-pair scores for every Measure, as a Catalog with land cover has them.
    quantiles = np.tile(np.linspace(0, 1, data.store.quantiles.shape[1]), (len(data.store.quantiles), 1))
    store = dataclasses.replace(data.store, facts=facts, profile=profile, quantiles=quantiles)

    (notes,) = scored_measures(store, 0, np.array([1]), None)

    assert notes["waterside"]["note"].startswith("Counted as the same (within the dead band); ")
    assert notes["water_count"]["note"].startswith("Counted as the same (both 3 or more); ")
    assert notes["highest"]["note"].startswith("Identical; ")
    assert notes["round_trip"]["note"].startswith("Identical; ")
    assert notes["length"]["note"].startswith("More alike than")
    assert notes["shelter_along"] == {"score": None, "raw": None, "note": "Neither has one mapped: not compared"}


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


CITIES = Path(__file__).parent / "fixtures" / "cities1000.txt"


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


def test_place_search_puts_its_name_then_another_exact_name_before_a_more_populous_prefix(
    placed: ServingData,
) -> None:
    assert [place["name"] for place in placed.find_place("vrh")] == ["Vrh", "Vrhnika"]
    assert [place["name"] for place in placed.find_place("velikovec")] == ["Völkermarkt", "Velikovecki Breg"]


def test_place_file_can_be_added_beside_an_existing_export(catalog_path: Path, tmp_path: Path, capsys) -> None:
    from dayhike.cli import main

    out = export_serving(catalog_path, tmp_path / "out")
    meta = (out / "meta.json").read_bytes()

    assert main(["export-places", "--geonames", str(CITIES), "--out", str(out)]) == 0

    assert (out / "meta.json").read_bytes() == meta
    assert [place["name"] for place in ServingData(out).find_place("ogulin")] == ["Ogulin"]
    assert capsys.readouterr().out.strip() == str(out / "places.sqlite")


def test_place_file_reads_the_geonames_zip(catalog_path: Path, tmp_path: Path) -> None:
    import zipfile

    with zipfile.ZipFile(tmp_path / "cities1000.zip", "w") as archive:
        archive.write(CITIES, "cities1000.txt")
    out = export_serving(catalog_path, tmp_path / "out", geonames_path=tmp_path / "cities1000.zip")

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

    target = tmp_path / "geonames" / "cities1000.zip"
    assert fetch_geonames(target, urlretrieve) == target
    assert fetch_geonames(target, urlretrieve) == target
    assert asked == [GEONAMES_URL] == ["https://download.geonames.org/export/dump/cities1000.zip"]
