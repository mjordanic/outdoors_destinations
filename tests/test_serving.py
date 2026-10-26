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
    encode_polyline,
    export_serving,
    simplify_line,
)
from dayhike.similarity import FACT_COLUMNS, MEASURE_NAMES

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

    assert meta["format"] == 3
    assert meta["dayhikes"] == 4
    assert meta["attribution"]["osm"] == "© OpenStreetMap contributors"
    assert meta["drive_time"] is False
    for name in ("ids.npy", "facts.npy", "profile.npy", "twins.npy", "quantiles.npy", "meta.json"):
        assert (export / "similarity" / name).is_file()
    assert np.load(export / "trailheads.npy").shape == (4, 2)


def test_export_keeps_the_route_the_true_metre_profile_and_the_measured_points(
    data: ServingData,
) -> None:
    ridge = data.detail(RIDGE_WALK_ID)

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


def test_a_dayhike_without_elevation_has_no_profile(data: ServingData) -> None:
    valley = data.detail(1002)

    assert valley["elevation"] is None
    assert valley["facts"]["elevation_gain_m"] is None
    assert (valley["climb_m"], valley["descent_m"]) == (None, None)


def test_in_box_finds_a_line_crossing_the_box_away_from_its_trailhead(data: ServingData) -> None:
    # Ridge Walk runs north from its Trailhead at 45.0, 15.0 to 45.09.
    hikes, more = data.in_box(45.05, 14.99, 45.06, 15.01, 10)

    assert [hike["id"] for hike in hikes] == [RIDGE_WALK_ID]
    assert more is False
    assert hikes[0]["name"] == "Ridge Walk"
    assert decode_polyline(hikes[0]["line"])[0] == pytest.approx((45.0, 15.0), abs=1e-5)
    assert hikes[0]["walk_m"] == pytest.approx(data.detail(RIDGE_WALK_ID)["walk_m"])
    assert data.in_box(46.0, 16.0, 46.1, 16.1, 10) == ([], False)


def test_in_box_returns_at_most_the_limit_and_says_there_are_more(data: ServingData) -> None:
    hikes, more = data.in_box(44.0, 14.0, 46.0, 16.0, 2)
    every, none_left = data.in_box(44.0, 14.0, 46.0, 16.0, 10)

    assert len(hikes) == 2 and more is True
    assert len(every) == 4 and none_left is False


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
    assert results[0]["measures"]["elevation_gain"] == {"score": None, "raw": None}


def test_rank_respects_the_limit_and_the_allowed_rows(data: ServingData) -> None:
    assert len(data.rank(RIDGE_WALK_ID, {"length": 100}, limit=2)) == 2
    allowed = np.array([data.row_of(1003)])
    assert [r["id"] for r in data.rank(RIDGE_WALK_ID, {"length": 100}, allowed_rows=allowed)] == [1003]


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
