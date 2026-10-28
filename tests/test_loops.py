"""SuggestedLoops (ADR-0020): RoundTrips drawn on the TrailGraph that a Ranking proposes itself.

The network is tests/fixtures/trailgraph.osm with a Middle Path (way 111) from
W across the Lake Loop to G, 1.1 km by a node 200 m north of the straight
line: it cuts the Lake Loop into two 3.1 km loops that are no Catalog
DayHike, one each side of it. Each walks two thirds of its length on the Lake
Loop, so from the Lake Loop neither is suggested; from the Meadow Path (3 km
there and back, south of the Lake Loop) both are. Every hike there starts
within 10 km of the others, so a Ranking lists one row; the tests that look
at the loops themselves list every hike in its own row.
"""

import pytest
from test_trailgraph import FIXTURE, LAKE_LOOP, MEADOW_PATH, _write_rasters

from dayhike import build_catalog, build_similarity, sample_climate, sample_dem, sample_landcover
from dayhike.similarity import DEFAULT_WEIGHTS
from dayhike.trailgraph import Route, sample_trailgraph

MIDDLE_PATH = 2008


@pytest.fixture(scope="module")
def data(tmp_path_factory: pytest.TempPathFactory):
    from dayhike.serving import ServingData, export_serving

    root = tmp_path_factory.mktemp("loops")
    text = FIXTURE.read_text()
    text = text.replace("  <way ", '  <node id="61" lat="46.0562952512" lon="14.0564789788" version="1"/>\n  <way ', 1)
    text = text.replace(
        "  <relation ",
        '  <way id="111" version="1"><nd ref="8"/><nd ref="61"/><nd ref="4"/><tag k="highway" v="path"/></way>\n'
        "  <relation ",
        1,
    )
    text = text.replace(
        "</osm>",
        f'  <relation id="{MIDDLE_PATH}" version="1"><member type="way" ref="111" role=""/><tag k="type" v="route"/>'
        '<tag k="route" v="hiking"/><tag k="name" v="Middle Path"/></relation>\n</osm>',
    )
    (root / "loops.osm").write_text(text)
    dem, landcover, climate = _write_rasters(root)
    path = root / "catalog.sqlite"
    build_catalog(root / "loops.osm", path)
    sample_dem(path, dem)
    sample_landcover(path, landcover)
    sample_climate(path, climate)
    sample_trailgraph(path, dem_dir=dem, landcover_dir=landcover, climate_dir=climate)
    build_similarity(path, dem, landcover_dir=landcover)
    return ServingData(export_serving(path, root / "web-serving"))


@pytest.fixture
def rows_apart(monkeypatch) -> None:
    """Every hike its own row, as if no two started within 10 km of each other."""
    monkeypatch.setattr("dayhike.serving.GROUP_M", 0.0)


def _loops(results: list[dict]) -> list[dict]:
    return [result for result in results if result["id"] is None]


def _segments(data, loop: dict) -> set[int]:
    return set(data.compose(Route.parse(loop["route"]["text"]))["route"]["segments"])


@pytest.mark.usefixtures("rows_apart")
def test_a_ranking_lists_loops_drawn_from_segments_beside_dayhikes(data) -> None:
    results = data.rank(MEADOW_PATH, DEFAULT_WEIGHTS)
    loops = _loops(results)

    # Both sides of the Middle Path.
    assert len(loops) == 2 and _segments(data, loops[0]) != _segments(data, loops[1])
    assert {r["id"] for r in results} - {None} == set(data.store.ids.tolist()) - {MEADOW_PATH}
    assert [r["score"] for r in results] == sorted((r["score"] for r in results), reverse=True)
    assert all(r["versions"] == [] for r in results)
    for loop in loops:
        # Named by a hiking place near its start (the Lakeside Inn is a restaurant), not by its trails.
        assert loop["name"] == "Loop near Cold Spring"
        assert loop["length_m"] == pytest.approx(3077, abs=2)
        route = Route.parse(loop["route"]["text"])
        for return_to_start in (True, False):  # it ends where it starts: measured the same either way
            hike = data.compose(route, return_to_start=return_to_start)
            assert hike["way_back"] in (None, {"m": 0.0, "kind": "none"}) and hike["facts"]["round_trip"] == 1.0
        assert len(set(hike["route"]["segments"])) == len(hike["route"]["segments"])
        assert (loop["line"], loop["trailhead"]) == (hike["line"], hike["trailhead"])
        assert (loop["length_m"], loop["climb_m"], loop["descent_m"]) == (
            pytest.approx(hike["facts"]["length_m"]), hike["climb_m"], hike["descent_m"])  # fmt: skip
        # Scored as the same pair is from the loop's side.
        pair = data.pair(route, MEADOW_PATH, DEFAULT_WEIGHTS)
        assert loop["score"] == pytest.approx(pair["score"])
        for name, scores in pair["measures"].items():
            assert loop["measures"][name] == pytest.approx(scores), name
        assert loop["drive_hours"] is None


@pytest.mark.usefixtures("rows_apart")
def test_a_loop_mostly_on_the_seed_is_no_suggestion(data) -> None:
    loops = _loops(data.rank(MEADOW_PATH, DEFAULT_WEIGHTS))

    # Two thirds of either loop is the Lake Loop.
    assert _loops(data.rank(LAKE_LOOP, DEFAULT_WEIGHTS)) == []
    # From one loop, the other is a third the same: suggested; the Seed itself, walked either way, is not.
    for seed, other in (loops, loops[::-1]):
        results = data.rank(Route.parse(seed["route"]["text"]), DEFAULT_WEIGHTS)
        assert [_segments(data, r) for r in _loops(results)] == [_segments(data, other)]
        assert LAKE_LOOP in [r["id"] for r in results]


def test_hikes_starting_near_each_other_share_a_row_of_versions(data) -> None:
    apart = {r["id"] if r["id"] is not None else r["route"]["text"]: r for r in _rows_apart(data)}

    (row,) = data.rank(MEADOW_PATH, DEFAULT_WEIGHTS)

    # The best hike leads; the Middle Path and the Lake Loop, though better than
    # the Connector, lie mostly on it, so the Connector and the Summit Line are its versions.
    assert row["id"] is None and row["score"] == max(r["score"] for r in apart.values())
    assert [v["id"] for v in row["versions"]] == [2004, 2002]
    assert row["score"] > apart[2008]["score"] > apart[LAKE_LOOP]["score"] > row["versions"][0]["score"]
    for version in row["versions"]:  # each as it is listed in a row of its own, but for versions
        assert version == {k: v for k, v in apart[version["id"]].items() if k != "versions"}


def _rows_apart(data) -> list[dict]:
    import dayhike.serving as serving

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(serving, "GROUP_M", 0.0)
        return data.rank(MEADOW_PATH, DEFAULT_WEIGHTS)


@pytest.mark.parametrize(
    ("name", "value", "versions"),
    [
        ("VERSIONS", 1, []),  # no room for a second version
        ("VERSION_DIFFERS", 0.99, [2005, 2006]),  # only hikes all but wholly off the others
    ],
)
def test_a_row_keeps_few_versions_each_mostly_a_different_route(
    data, monkeypatch, name: str, value: float, versions: list[int]
) -> None:
    monkeypatch.setattr(f"dayhike.serving.{name}", value)

    (row,) = data.rank(MEADOW_PATH, DEFAULT_WEIGHTS)

    assert [v["id"] for v in row["versions"]] == versions


@pytest.mark.usefixtures("rows_apart")
def test_a_loop_starts_within_the_drive_and_shows_its_hours(data) -> None:
    def everywhere(places: list) -> list:
        return [1.5] * len(places)

    def nowhere(places: list) -> list:
        return [None] * len(places)

    loops = _loops(data.rank(MEADOW_PATH, DEFAULT_WEIGHTS, reach=everywhere))
    assert len(loops) == 2 and {r["drive_hours"] for r in loops} == {1.5}
    assert _loops(data.rank(MEADOW_PATH, DEFAULT_WEIGHTS, reach=nowhere)) == []


@pytest.mark.parametrize(
    ("module", "name", "value"),
    [
        ("dayhike.loops", "LOOP_EXPANSIONS", 0),
        ("dayhike.loops", "LOOP_READ", 1),
        ("dayhike.serving", "LOOP_SEEDS", 0),
        ("dayhike.serving", "LOOP_BUDGET_S", 0.0),
    ],
)
@pytest.mark.usefixtures("rows_apart")
def test_a_search_stops_at_its_budget(data, monkeypatch, module: str, name: str, value: float) -> None:
    monkeypatch.setattr(f"{module}.{name}", value)

    results = data.rank(MEADOW_PATH, DEFAULT_WEIGHTS)

    assert _loops(results) == [] and len(results) == len(data.store.ids) - 1


def test_api_ranks_loops_with_their_link_versions_and_drive_hours(data) -> None:
    from fastapi.testclient import TestClient

    from dayhike.web import create_app

    class Hour:
        def sources_to_targets(self, origin, trailheads):
            return [3600.0] * len(trailheads)

    client = TestClient(create_app(data, lambda max_hours: Hour()))
    (plain,) = client.post("/api/rank", json={"seed": MEADOW_PATH}).json()["results"]
    (driven,) = client.post(
        "/api/rank", json={"seed": MEADOW_PATH, "origin": {"lat": 46.0, "lon": 14.0}, "max_hours": 2}
    ).json()["results"]

    # One row, a loop with two Catalog DayHikes as its versions, each with the hours to its start.
    assert driven["route"]["text"] == plain["route"]["text"]
    assert [v["id"] for v in driven["versions"]] == [v["id"] for v in plain["versions"]] == [2004, 2002]
    assert {r["drive_hours"] for r in (plain, *plain["versions"])} == {None}
    assert {r["drive_hours"] for r in (driven, *driven["versions"])} == {1.0}
    hike = client.post("/api/compose", json={"route": plain["route"]["text"], "name": plain["name"]}).json()
    assert (hike["line"], hike["name"]) == (plain["line"], plain["name"])
    for version in plain["versions"]:  # drawn on the map as the hike's own page draws it
        assert client.get(f"/api/hikes/{version['id']}").json()["line"] == version["line"]


@pytest.mark.usefixtures("rows_apart")
def test_cli_compose_lists_loops_by_their_link(data, capsys) -> None:
    from dayhike.cli import main

    seed = _loops(data.rank(MEADOW_PATH, DEFAULT_WEIGHTS))[1]["route"]["text"]

    assert main(["compose", "--data", str(data.directory), seed]) == 0
    listed = [line.split("\t")[0] for line in capsys.readouterr().out.strip().splitlines()[1:]]
    assert str(LAKE_LOOP) in listed and str(MIDDLE_PATH) in listed
    assert [item for item in listed if not item.isdigit()] == [
        r["route"]["text"] for r in _loops(data.rank(Route.parse(seed), DEFAULT_WEIGHTS))
    ]


def test_cli_compose_prints_each_rows_versions_under_it(data, capsys) -> None:
    from dayhike.cli import main

    seed = data.rank(MEADOW_PATH, DEFAULT_WEIGHTS)[0]["route"]["text"]
    rows = data.rank(Route.parse(seed), DEFAULT_WEIGHTS)

    assert main(["compose", "--data", str(data.directory), seed]) == 0
    listed = [line.split("\t")[0] for line in capsys.readouterr().out.rstrip("\n").splitlines()[1:]]
    assert any(r["versions"] for r in rows)
    assert listed == [
        f"{indent}{hike['id'] if hike['id'] is not None else hike['route']['text']}"
        for row in rows
        for indent, hike in (("", row), *(("  ", version) for version in row["versions"]))
    ]


def test_a_found_loop_names_whole_segments_from_its_start(data) -> None:
    from dayhike.loops import LoopTarget, find_loops

    loop = _loops(data.rank(MEADOW_PATH, DEFAULT_WEIGHTS))[0]
    target = LoopTarget(3_100.0, float("nan"), float("nan"), float("nan"), None)

    found = find_loops(data.trailgraph, target, [tuple(loop["trailhead"])], limit=10, deadline=float("inf"))

    assert found and all(f.drive_hours is None for f in found)
    walk = data.trailgraph.walk_segments(found[0].start, found[0].segments)
    assert data.trailgraph.walk(walk.route).segments == list(found[0].segments)
    assert (walk.lat[0], walk.lon[0]) == (walk.lat[-1], walk.lon[-1])


@pytest.mark.usefixtures("rows_apart")
def test_a_loop_with_no_hiking_place_near_its_start_is_named_by_its_trails(data, monkeypatch) -> None:
    import dayhike.serving as serving

    monkeypatch.setattr(serving, "LOOP_NAME_M", 600.0)  # the Cold Spring is 1 km away

    names = {loop["name"] for loop in _loops(data.rank(MEADOW_PATH, DEFAULT_WEIGHTS))}

    assert names and all(name.startswith("Loop on ") for name in names), names
