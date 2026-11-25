"""The "Find a base" API (ADR-0084): ``TestClient`` on the fixture export with a hand-made ``bases/`` folder."""

import shutil
import sqlite3
import threading
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from dayhike import build_catalog, build_similarity
from dayhike.bases import REACHES, BaseFacts, HikeTypes, _write_bases
from dayhike.hike_filter import HikeFilter, hike_mask
from dayhike.serving import ServingData, _fold, export_serving
from dayhike.web import TAB_HEADER, create_app

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
KESWICK, BETHESDA, CHAMONIX, MOAB, KESSEL = 1, 2, 3, 4, 5
# id, name, country, population, lat, lon. Kessel is a place but no Base.
PLACES = (
    (KESWICK, "Keswick", "GB", 4_658, 45.0, 7.0),
    (BETHESDA, "Bethesda", "GB", 4_735, 47.0, 7.0),
    (CHAMONIX, "Chamonix-Mont-Blanc", "FR", 10_614, 49.0, 7.0),
    (MOAB, "Moab", "US", 5_235, 38.5, -109.5),
    (KESSEL, "Kessel", "DE", 400, 45.0, 9.0),
)
# The Bases: each fixture hike is a HikeGroup of its own HikeType; the minutes to each.
# Bethesda reaches what Keswick does; Chamonix half of it, Moab the other half.
# Hike 0 is 10 km long, the others 5 km: SHORT leaves it out.
REACHED = {
    KESWICK: {0: 5, 1: 10, 2: 20, 3: 50},
    BETHESDA: {0: 5, 1: 10, 2: 20, 3: 50},
    CHAMONIX: {0: 5, 1: 25},
    MOAB: {2: 5, 3: 25},
}
MILD = [9.0] * 12
SHORT = {"length_max_km": 6}


def _with_bases(export: Path, out: Path) -> Path:
    """A copy of ``export`` with ``places.sqlite`` of PLACES and a ``bases/`` folder of REACHED."""
    shutil.copytree(export, out)
    db = sqlite3.connect(out / "places.sqlite")
    db.execute("CREATE TABLE places (id INTEGER PRIMARY KEY, name TEXT, country TEXT, population INTEGER, lat REAL, lon REAL)")
    db.execute("CREATE TABLE place_names (name TEXT, place INTEGER, alternate INTEGER, PRIMARY KEY (name, place))")
    db.executemany("INSERT INTO places VALUES (?, ?, ?, ?, ?, ?)", PLACES)
    db.executemany("INSERT INTO place_names VALUES (?, ?, 0)", [(_fold(name), place) for place, name, *_ in PLACES])
    db.commit()
    db.close()
    store = ServingData(out).store
    hikes = len(store.ids)
    assert hikes == len(next(iter(REACHED.values())))
    bases = list(REACHED)
    at = {place: (lat, lon) for place, _, _, _, lat, lon in PLACES}
    population = {place: people for place, _, _, people, _, _ in PLACES}
    reached = [
        (np.array(list(REACHED[b]), dtype=np.int32), np.array(list(REACHED[b].values()), dtype=np.uint8)) for b in bases
    ]
    months = np.array([[MILD] * len(REACHES)] * len(bases), dtype=np.float32)
    months[bases.index(MOAB)] = 25.0  # never mild
    _write_bases(
        out,
        np.array(bases, dtype=np.int64),
        np.array([at[b] for b in bases], dtype=np.float64),
        reached,
        np.arange(hikes),
        np.arange(hikes, dtype=np.int16),
        HikeTypes(types=np.zeros(0), middles=np.zeros(0), table=np.eye(hikes, dtype=np.float32), version="t"),
        BaseFacts(population=np.array([population[b] for b in bases], dtype=np.float64), month_temperatures=months),
        store,
    )
    return out


@pytest.fixture(scope="module")
def exports(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    directory = tmp_path_factory.mktemp("web-bases")
    path = directory / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    build_similarity(path, directory / "dem")
    plain = export_serving(path, directory / "web-serving")
    return plain, _with_bases(plain, directory / "with-bases")


@pytest.fixture(scope="module")
def data(exports: tuple[Path, Path]) -> ServingData:
    found = ServingData(exports[1])
    assert found.bases is not None
    return found


@pytest.fixture(autouse=True)
def _few_hikes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixture export has four hikes: a Base needs one HikeGroup, not ten."""
    monkeypatch.setattr("dayhike.bases.MIN_HIKE_GROUPS", 1)


@pytest.fixture
def client(data: ServingData) -> TestClient:
    return TestClient(create_app(data))


def _rank(client: TestClient, **body) -> dict:
    response = client.post("/api/bases/rank", json={"seed": KESWICK, "reach": 60, **body})
    assert response.status_code == 200, response.text
    return response.json()


def _passing_groups(data: ServingData, place: int, reach: int, hike_filter: dict) -> int:
    """By hand: the HikeGroups (here hikes) within ``reach`` of ``place`` that pass the filter."""
    mask = hike_mask(data.store, HikeFilter.from_mapping(hike_filter))
    return sum(1 for hike, minutes in REACHED[place].items() if minutes <= reach and mask[hike])


def test_info_says_whether_the_site_has_bases(exports: tuple[Path, Path], client: TestClient) -> None:
    plain = TestClient(create_app(ServingData(exports[0])))

    assert client.get("/api/info").json()["bases"] == {"reaches": list(REACHES)}
    assert plain.get("/api/info").json()["bases"] is None
    assert plain.get("/api/bases/search", params={"q": "kes"}).status_code == 404
    assert plain.get("/api/bases/nearest", params={"lat": 45, "lon": 7}).status_code == 404
    assert plain.post("/api/bases/rank", json={"seed": KESWICK, "reach": 30}).status_code == 404
    assert plain.post("/api/bases/detail", json={"base": KESWICK, "reach": 30}).status_code == 404


def test_a_rebuilt_bases_folder_changes_the_etag(exports: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    def etag(data: ServingData) -> str:
        return TestClient(create_app(data)).get("/api/bases/search", params={"q": "Kes"}).headers["etag"]

    before = etag(ServingData(exports[1]))
    rebuilt = ServingData(exports[1])
    monkeypatch.setitem(rebuilt.bases.meta, "version", "rebuilt")

    assert etag(ServingData(exports[1])) == before
    assert etag(rebuilt) != before


def test_base_search_finds_only_places_that_are_bases(client: TestClient) -> None:
    found = client.get("/api/bases/search", params={"q": "Kes"}).json()["results"]

    assert found == [{"id": KESWICK, "name": "Keswick", "country": "GB", "population": 4_658, "lat": 45.0, "lon": 7.0}]
    assert client.get("/api/bases/search", params={"q": "cham"}).json()["results"][0]["id"] == CHAMONIX
    assert client.get("/api/bases/search", params={"q": "nowhere"}).json()["results"] == []


def test_nearest_base_to_a_point_with_its_distance(client: TestClient) -> None:
    found = client.get("/api/bases/nearest", params={"lat": 46.9, "lon": 7.0}).json()

    assert found["id"] == BETHESDA
    assert found["name"] == "Bethesda"
    assert found["distance_km"] == pytest.approx(11.1, abs=0.1)
    assert client.get("/api/bases/nearest", params={"lat": 91, "lon": 7}).status_code == 422


def test_ranking_lists_the_bases_alike_best_first_with_the_score_explained(client: TestClient) -> None:
    body = _rank(client)

    assert body["seed"]["id"] == KESWICK
    assert body["seed"]["hike_groups"] == 4
    assert body["too_few"] is False
    rows = body["results"]
    assert [row["id"] for row in rows][:2] == [BETHESDA, CHAMONIX]
    best = rows[0]
    assert best["name"] == "Bethesda"
    assert best["country"] == "GB"
    assert best["region"] == "europe"
    assert best["score"] == pytest.approx(1.0)
    assert best["hike_score"] == best["score"]
    assert {"mix", "coverage", "back", "enough"} <= set(best)
    assert best["hike_groups"] == 4
    assert best["cuts"] == {}
    assert best["months"] == MILD
    assert next(row for row in rows if row["id"] == MOAB)["region"] == "us"


def test_a_shorter_reach_counts_only_the_hikes_within_it(client: TestClient) -> None:
    body = _rank(client, reach=15)

    assert body["reach"] == 15
    assert body["seed"]["hike_groups"] == 2
    assert {row["id"]: row["hike_groups"] for row in body["results"]}[CHAMONIX] == 1


def test_a_hike_filter_counts_only_the_passing_hikes_on_both_sides(client: TestClient, data: ServingData) -> None:
    body = _rank(client, hike_filter=SHORT)

    assert body["hike_filter"] == SHORT
    assert body["seed"]["hike_groups"] == _passing_groups(data, KESWICK, 60, SHORT) == 3
    assert {row["id"]: row["hike_groups"] for row in body["results"]} == {
        place: _passing_groups(data, place, 60, SHORT) for place in (BETHESDA, CHAMONIX, MOAB)
    }


def test_too_few_of_your_hikes_pass_is_said_and_ranks_nothing(client: TestClient) -> None:
    body = _rank(client, hike_filter={"climb_min_m": 100_000})

    assert body["too_few"] is True
    assert body["seed"]["hike_groups"] == 0
    assert body["results"] == []


def test_a_town_trait_filter_leaves_out_the_bases_that_fail_it(client: TestClient) -> None:
    bigger = _rank(client, traits={"size": {"at_least": 5_000}})
    mild = _rank(client, traits={"months": {"mild_in": [7]}})

    assert BETHESDA not in [row["id"] for row in bigger["results"]]
    assert CHAMONIX in [row["id"] for row in bigger["results"]]
    assert MOAB not in [row["id"] for row in mild["results"]]


def test_like_mine_cuts_the_score_and_says_by_how_much(client: TestClient) -> None:
    body = _rank(client, traits={"size": {"like_mine": 3}})

    chamonix = next(row for row in body["results"] if row["id"] == CHAMONIX)
    assert 0 < chamonix["cuts"]["size"] <= 0.5
    assert chamonix["score"] == pytest.approx(chamonix["hike_score"] * (1 - chamonix["cuts"]["size"]))
    assert body["traits"] == {"size": {"like_mine": 3}}


@pytest.mark.parametrize(
    "body",
    [
        {"seed": KESWICK, "reach": 45},
        {"seed": KESWICK},
        {"seed": KESWICK, "reach": 30, "hike_filter": {"difficulty": 2}},
        {"seed": KESWICK, "reach": 30, "hike_filter": {"climb_max_m": "high"}},
        {"seed": KESWICK, "reach": 30, "hike_filter": {"climb_min_m": 500, "climb_max_m": 100}},
        {"seed": KESWICK, "reach": 30, "traits": {"prices": {"like_mine": 1}}},
        {"seed": KESWICK, "reach": 30, "traits": {"size": {"like_mine": 4}}},
        {"seed": KESWICK, "reach": 30, "traits": {"size": {"like_mine": 2, "up_to": 50_000}}},
        {"seed": KESWICK, "reach": 30, "traits": {"size": {"mild_in": [7]}}},
        {"seed": KESWICK, "reach": 30, "traits": {"months": {"mild_in": [13]}}},
        {"seed": KESWICK, "reach": 30, "traits": {"size": {"up_to": -1}}},
        {"seed": KESWICK, "reach": 30, "traits": {"size": {"most": 1}}},
    ],
)
def test_ranking_refuses_bad_requests(client: TestClient, body: dict) -> None:
    assert client.post("/api/bases/rank", json=body).status_code == 422


def test_ranking_from_a_place_that_is_not_a_base_is_not_found(client: TestClient) -> None:
    assert client.post("/api/bases/rank", json={"seed": KESSEL, "reach": 30}).status_code == 404


def test_one_base_lists_its_passing_hikes_with_minutes(client: TestClient, data: ServingData) -> None:
    response = client.post("/api/bases/detail", json={"base": KESWICK, "reach": 30, "hike_filter": SHORT})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["base"]["id"] == KESWICK
    assert body["base"]["name"] == "Keswick"
    assert body["base"]["months"] == MILD
    within = [1, 2]  # hike 0 is too long, hike 3 too far
    assert [hike["id"] for hike in body["hikes"]] == [int(data.store.ids[hike]) for hike in within]
    assert [hike["minutes"] for hike in body["hikes"]] == [REACHED[KESWICK][hike] for hike in within]
    assert body["base"]["hike_groups"] == len(within)
    first = body["hikes"][0]
    assert first["name"]
    assert first["trailhead"] == pytest.approx(data.trailheads[within[0]].tolist())
    assert client.post("/api/bases/detail", json={"base": KESSEL, "reach": 30}).status_code == 404


def test_base_rankings_have_their_own_rate_limit(data: ServingData) -> None:
    client = TestClient(create_app(data, base_rank_rate=1, rank_rate=5))

    assert client.post("/api/bases/rank", json={"seed": KESWICK, "reach": 30}).status_code == 200
    assert client.post("/api/bases/rank", json={"seed": KESWICK, "reach": 30}).status_code == 429
    assert client.post("/api/rank", json={"seed": int(data.store.ids[0])}).status_code == 200


def test_a_base_ranking_does_not_wait_for_a_hike_ranking(data: ServingData, monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(create_app(data))
    started, release = threading.Event(), threading.Event()
    rank = data.rank

    def held(*args, **kwargs):
        started.set()
        release.wait(10)
        return rank(*args, **kwargs)

    monkeypatch.setattr(data, "rank", held)
    headers = {TAB_HEADER: "tab-1"}
    hike_ranking = threading.Thread(
        target=lambda: client.post("/api/rank", json={"seed": int(data.store.ids[0])}, headers=headers)
    )
    hike_ranking.start()
    try:
        assert started.wait(10)
        # The same tab's next hike ranking would be refused as its own still running.
        response = client.post("/api/bases/rank", json={"seed": KESWICK, "reach": 30}, headers=headers)
    finally:
        release.set()
        hike_ranking.join()

    assert response.status_code == 200, response.text
