"""External hike sources as separate licence layers, starting with Ireland (ADR-0062)."""

import io
import json
import sqlite3
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from dayhike import build_catalog, build_similarity
import dayhike.external as external_module
from dayhike.built import BUILT_ID_MIN
from dayhike.catalog import geometry_store_path_for
from dayhike.cli import main
from dayhike.external import (
    EXTERNAL_ID_MIN,
    external_id,
    fetch_external,
    joined_line,
    read_external,
)
from dayhike.serving import ServingData, export_serving
from dayhike.web import create_app

from test_built_hikes import FIXTURE, RIDGE_WALK_ID

EXTERNAL_DIR = Path(__file__).parent / "fixtures" / "external"
SOURCE = "ie-sport-ireland"
LIPAS = "fi-lipas"
TURRUTE = "no-turrutebasen"
LOUGH_URL = "https://www.sportireland.ie/outdoors/walking/trails/lough-fixture-way"
LIPAS_PAGE = Path(__file__).parent / "fixtures" / "external" / "fi-lipas-api-page.json"
TURRUTE_PAGE = Path(__file__).parent / "fixtures" / "external" / "no-turrutebasen-page.gpx"
KORPI_URL = "https://www.lipas.fi/liikuntapaikka/100"


def test_the_sport_ireland_adapter_reads_walking_trails_from_their_start() -> None:
    hikes = {hike.ref: hike for hike in read_external(SOURCE, EXTERNAL_DIR)}

    # The cycle route is not a walk.
    assert sorted(hikes) == ["trail/1", "trail/2", "trail/3", "trail/5"]
    lough = hikes["trail/2"]
    assert (lough.name, lough.url) == ("Lough Fixture Way", LOUGH_URL)
    # Listed from its far end, it starts at the trail's own start; the part 13 km away is left out.
    assert lough.points == ((45.4, 15.3), (45.41, 15.31), (45.42, 15.3))
    # Two parts that meet are one line.
    assert len(hikes["trail/1"].points) == 3
    assert hikes["trail/3"].url is None


def test_joined_line_extends_the_longest_part_by_the_parts_that_meet_its_ends() -> None:
    a = [(45.0, 15.0), (45.02, 15.0)]
    b = [(45.03, 15.0), (45.02, 15.0)]  # meets a's end, listed backwards
    far = [(46.0, 15.0), (46.0, 15.01)]

    assert joined_line([b, far, a]) == [(45.0, 15.0), (45.02, 15.0), (45.03, 15.0)]
    assert joined_line([a, b], start=(45.03, 15.0))[0] == (45.03, 15.0)
    assert joined_line([[(45.0, 15.0)]]) == []


def test_a_source_the_register_does_not_list_as_used_is_refused() -> None:
    with pytest.raises(ValueError, match="fr-ecrins-randonnees"):
        read_external("fr-ecrins-randonnees", EXTERNAL_DIR)
    with pytest.raises(ValueError, match="no external source"):
        fetch_external("osm", EXTERNAL_DIR)


def test_an_uncached_source_says_how_to_fetch_it(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="dayhike fetch-external --source ie-sport-ireland"):
        read_external(SOURCE, tmp_path)


def test_an_external_id_is_stable_and_apart_from_osm_and_built_ids() -> None:
    first = external_id(SOURCE, "trail/2")

    assert first == external_id(SOURCE, "trail/2")
    assert first != external_id(SOURCE, "trail/3")
    assert first != external_id("fi-lipas", "trail/2")
    assert EXTERNAL_ID_MIN <= first < BUILT_ID_MIN


@pytest.fixture(scope="module")
def catalog_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("external")
    path = directory / "catalog.sqlite"
    build_catalog(FIXTURE, path, sources=[], external=[SOURCE], external_dir=EXTERNAL_DIR)
    build_similarity(path, directory / "dem")
    return path


def test_ingest_adds_an_external_hike_and_drops_its_twin_of_an_osm_hike(catalog_path: Path, tmp_path: Path) -> None:
    from dayhike import Catalog

    catalog = Catalog.open(catalog_path)
    plain = build_catalog(FIXTURE, tmp_path / "plain.sqlite", sources=[])

    (lough,) = [hike for hike in catalog.dayhikes if hike.kind == "external"]
    assert lough.id == external_id(SOURCE, "trail/2")
    assert (lough.name, lough.name_origin) == ("Lough Fixture Way", "external")
    assert (lough.source, lough.source_ref, lough.source_url) == (SOURCE, "trail/2", LOUGH_URL)
    assert (lough.trailhead.latitude, lough.trailhead.longitude) == (45.4, 15.3)
    assert lough.difficulty.sac_scale is None
    # Ridge Walk Trail lies 16 m beside Ridge Walk: the OSM hike is listed. Lough Fixture Way Again is
    # Lough Fixture Way listed twice, so it is a duplicate, not a Twin of an OSM hike. Short Fixture Loop is under 1 km.
    report = catalog.ingest_report
    assert (
        report.external_added,
        report.dropped_external_twin,
        report.dropped_external_duplicate,
        report.dropped_external_length,
    ) == (1, 1, 1, 1)
    assert report.kept == plain.ingest_report.kept + 1
    # The OSM hikes are as they were.
    assert [hike for hike in catalog.dayhikes if hike.kind != "external"] == list(plain.dayhikes)
    with sqlite3.connect(catalog_path) as db:
        assert db.execute("SELECT source, source_ref, source_url FROM dayhikes WHERE id = ?", (lough.id,)).fetchone() == (
            SOURCE,
            "trail/2",
            LOUGH_URL,
        )
    with sqlite3.connect(geometry_store_path_for(catalog_path)) as geo:
        vertices = geo.execute("SELECT latitude, longitude FROM vertices WHERE dayhike_id = ? ORDER BY seq", (lough.id,)).fetchall()
        assert geo.execute("SELECT COUNT(*) FROM ways WHERE dayhike_id = ?", (lough.id,)).fetchone() == (0,)
    assert vertices == [(45.4, 15.3), (45.41, 15.31), (45.42, 15.3)]


@pytest.fixture(scope="module")
def export(catalog_path: Path) -> Path:
    return export_serving(catalog_path, catalog_path.parent / "web-serving")


def test_the_api_credits_an_external_hike_to_its_source_under_its_licence(export: Path) -> None:
    client = TestClient(create_app(ServingData(export), None))
    lough = client.get(f"/api/hikes/{external_id(SOURCE, 'trail/2')}").json()

    assert lough["source"] == {
        "id": SOURCE,
        "ref": "trail/2",
        "url": LOUGH_URL,
        "name": "Sport Ireland Trail Routes",
        "licence": "CC BY 4.0",
        "licence_url": "https://creativecommons.org/licenses/by/4.0/",
    }
    # Not built from OSM relations, so not offered to edit on the map.
    assert lough["built"] is None
    assert lough["facts"]["length_m"] > 2_000
    attribution = client.get("/api/info").json()["attribution"]
    assert attribution[SOURCE] == "Sport Ireland Trail Routes (https://data.gov.ie/dataset/getirelandactive_trailroutes), CC BY 4.0"
    credits = client.get("/credits.html").text
    assert "Hiking routes from Sport Ireland Trail Routes" in credits
    assert 'href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>' in credits
    assert client.get(f"/api/hikes/{RIDGE_WALK_ID}").json()["source"]["id"] == "osm"


def test_an_export_without_external_hikes_credits_none(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, path, sources=[])
    build_similarity(path, tmp_path / "dem")
    data = ServingData(export_serving(path, tmp_path / "web-serving"))

    assert data.meta["sources"] == {}
    assert "Hiking routes from" not in TestClient(create_app(data, None)).get("/credits.html").text


def test_fetch_external_caches_every_page_of_the_walking_trails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    feature = {"type": "Feature", "properties": {"TrailID": 1}, "geometry": None}
    asked = []

    def urlopen(request, timeout):
        query = parse_qs(urlparse(request.full_url).query)
        asked.append(query)
        more = int(query["resultOffset"][0]) == 0
        page = {"features": [feature, feature] if more else [feature], "properties": {"exceededTransferLimit": more}}
        return io.BytesIO(json.dumps(page).encode())

    monkeypatch.setattr(external_module, "urlopen", urlopen)
    assert main(["fetch-external", "--source", SOURCE, "--dest", str(tmp_path)]) == 0

    cached = json.loads((tmp_path / SOURCE / f"{SOURCE}.geojson").read_text())
    assert len(cached["features"]) == 3
    assert [query["resultOffset"] for query in asked] == [["0"], ["2"]]
    assert asked[0]["where"] == ["Activity='Walking'"]
    assert asked[0]["outSR"] == ["4326"]


def test_the_lipas_adapter_names_active_routes_and_splits_parts_that_do_not_meet() -> None:
    hikes = {hike.ref: hike for hike in read_external(LIPAS, EXTERNAL_DIR)}

    assert sorted(hikes) == ["100", "200", "300/1", "300/2", "600"]
    korpi = hikes["100"]
    assert (korpi.name, korpi.url) == ("Korpi trail", KORPI_URL)
    assert korpi.points == ((60.0, 24.0), (60.01, 24.0), (60.02, 24.01))
    # Two parts that meet within 50 m are one walk, under the Lipas id.
    assert hikes["200"].points == ((60.0, 25.0), (60.01, 25.0), (60.02, 25.0))
    # Parts that do not meet are pieces, numbered from the southern start.
    assert hikes["300/1"].points == ((60.0, 24.0), (60.01, 24.0))
    assert hikes["300/2"].points == ((61.0, 24.0), (61.01, 24.0))
    assert hikes["300/1"].name == hikes["300/2"].name == "Split walk"
    # A nature trail under 1 km is still read; ingest applies the floor.
    assert hikes["600"].name == "Pieni luontopolku"


def test_the_turrutebasen_adapter_chains_foot_segments_by_route_number() -> None:
    hikes = {hike.ref: hike for hike in read_external(TURRUTE, EXTERNAL_DIR)}

    assert sorted(hikes) == ["F_2/1", "F_2/2", "F_3/1", "F_3/2", "F_9", "bare1", "kort1", "osl30"]
    # Two segments of one route number that meet are one walk. "Ukjent" is not a name.
    osl = hikes["osl30"]
    assert (osl.name, osl.url) == ("Blåmerket sti", None)
    assert osl.points == ((60.0, 10.0), (60.02, 10.0), (60.03, 10.0))
    # Parts that stay apart are pieces, numbered from the southern start.
    assert hikes["F_2/1"].points == ((60.0, 10.5), (60.01, 10.5))
    assert hikes["F_2/2"].points == ((61.0, 10.5), (61.01, 10.5))
    assert hikes["F_2/1"].name == hikes["F_2/2"].name == "Split route"
    # A spur that meets the walk mid-way is its own piece of the same name.
    assert hikes["F_3/1"].points == ((60.10, 11.0), (60.12, 11.0), (60.13, 11.0))
    assert hikes["F_3/2"].points == ((60.12, 11.0), (60.12, 11.01))
    assert hikes["F_3/1"].name == "Spur route"
    # No route name: the maintainer and the route number. A short walk is still read.
    assert hikes["F_9"].name == "DNT Oslo F_9"
    assert hikes["kort1"].name == "Kort sti"
    assert hikes["bare1"].name == "bare1"


def _write_turrute(directory: Path, segments: list[tuple[str, str, str, list[list[float]]]]) -> Path:
    features = [
        {
            "type": "Feature",
            "properties": {"rutenummer": number, "rutenavn": name, "maintainer": maintainer, "type": "Fotrute"},
            "geometry": {"type": "LineString", "coordinates": coordinates},
        }
        for number, name, maintainer, coordinates in segments
    ]
    folder = directory / TURRUTE
    folder.mkdir()
    (folder / f"{TURRUTE}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return directory


def test_the_turrutebasen_adapter_keeps_one_route_number_of_two_maintainers_apart(tmp_path: Path) -> None:
    directory = _write_turrute(
        tmp_path,
        [
            ("5", "Toppturen", "Bergen Turlag", [[5.3, 60.4], [5.3, 60.41]]),
            ("5", "Fjellstien", "Tromsø kommune", [[18.9, 69.6], [18.9, 69.61]]),
            ("5", "Bysti", "", [[10.7, 59.9], [10.7, 59.91]]),
        ],
    )
    hikes = {hike.ref: hike for hike in read_external(TURRUTE, directory)}

    assert sorted(hikes) == ["5", "Bergen Turlag/5", "Tromsø kommune/5"]
    assert hikes["Bergen Turlag/5"].name == "Toppturen"
    assert hikes["Bergen Turlag/5"].points == ((60.4, 5.3), (60.41, 5.3))
    assert hikes["Tromsø kommune/5"].name == "Fjellstien"
    assert hikes["Tromsø kommune/5"].points == ((69.6, 18.9), (69.61, 18.9))
    assert (hikes["5"].name, hikes["5"].points) == ("Bysti", ((59.9, 10.7), (59.91, 10.7)))


def test_the_turrutebasen_adapter_names_each_piece_from_its_own_segments(tmp_path: Path) -> None:
    directory = _write_turrute(
        tmp_path,
        [
            ("1", "Toppturen", "DNT", [[10.0, 60.0], [10.0, 60.01]]),
            ("1", "Toppturen", "DNT", [[10.0, 60.01], [10.0, 60.02]]),
            ("1", "Fjellstien", "DNT", [[10.0, 61.0], [10.0, 61.01]]),
        ],
    )
    hikes = {hike.ref: hike for hike in read_external(TURRUTE, directory)}

    assert sorted(hikes) == ["1/1", "1/2"]
    assert hikes["1/1"].name == "Toppturen"
    assert hikes["1/1"].points == ((60.0, 10.0), (60.01, 10.0), (60.02, 10.0))
    assert hikes["1/2"].name == "Fjellstien"
    assert hikes["1/2"].points == ((61.0, 10.0), (61.01, 10.0))


def test_fetch_external_names_a_download_without_a_gpx_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("readme.txt", "no routes")
    download = "https://nedlasting.geonorge.no/api/download/order/fixture/file"
    order = {"files": [{"downloadUrl": download}]}

    def urlopen(request, timeout):
        if request.full_url == external_module.TURRUTE_ORDER:
            return io.BytesIO(json.dumps(order).encode())
        return io.BytesIO(archive.getvalue())

    monkeypatch.setattr(external_module, "urlopen", urlopen)
    with pytest.raises(OSError, match=download):
        fetch_external(TURRUTE, tmp_path)


def test_fetch_external_caches_the_national_foot_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("turrute.gpx", TURRUTE_PAGE.read_bytes())
    download = "https://nedlasting.geonorge.no/api/download/order/fixture/file"
    order = {"files": [{"downloadUrl": download, "status": "ReadyForDownload"}]}
    asked = []

    def urlopen(request, timeout):
        asked.append(request)
        if request.full_url == external_module.TURRUTE_ORDER:
            return io.BytesIO(json.dumps(order).encode())
        return io.BytesIO(archive.getvalue())

    monkeypatch.setattr(external_module, "urlopen", urlopen)
    assert main(["fetch-external", "--source", "no", "--dest", str(tmp_path)]) == 0

    body = json.loads(asked[0].data.decode())
    line = body["orderLines"][0]
    assert line["metadataUuid"] == external_module.TURRUTE_UUID
    assert line["areas"] == [{"code": "0000", "name": "Hele landet", "type": "landsdekkende"}]
    assert line["formats"] == [{"name": "GPX "}]
    assert line["projections"][0]["code"] == "4326"
    assert asked[1].full_url == download
    cached = json.loads((tmp_path / TURRUTE / f"{TURRUTE}.geojson").read_text())
    assert len(cached["features"]) == 1
    props = cached["features"][0]["properties"]
    assert (props["rutenummer"], props["rutenavn"], props["maintainer"], props["type"]) == (
        "osl30",
        "Blåmerket sti",
        "DNT",
        "Fotrute",
    )
    assert cached["features"][0]["geometry"]["coordinates"] == [[10.0, 60.0], [10.0, 60.01]]


def test_the_lipas_adapter_keeps_a_branch_as_a_piece_of_its_own(tmp_path: Path) -> None:
    feature = {
        "type": "Feature",
        "properties": {"lipas-id": 800, "name": "Haara walk", "status": "active", "type-code": 4405},
        "geometry": {
            "type": "MultiLineString",
            "coordinates": [
                [[24.0, 60.0], [24.0, 60.01]],
                [[24.0, 60.01], [24.0, 60.02]],
                [[24.0, 60.01], [24.01, 60.01]],
            ],
        },
    }
    cache = tmp_path / LIPAS / f"{LIPAS}.geojson"
    cache.parent.mkdir()
    cache.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}))

    hikes = {hike.ref: hike for hike in read_external(LIPAS, tmp_path)}

    assert sorted(hikes) == ["800/1", "800/2"]
    assert hikes["800/1"].points == ((60.0, 24.0), (60.01, 24.0), (60.02, 24.0))
    # The spur meets the walk mid-way, not at its ends, so it is its own piece.
    assert hikes["800/2"].points == ((60.01, 24.0), (60.01, 24.01))


def test_fetch_external_caches_every_lipas_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = json.loads(LIPAS_PAGE.read_text())
    second = {
        "items": [
            {
                "lipas-id": 600,
                "name": "Pieni luontopolku",
                "status": "active",
                "type": {"type-code": 4404},
                "location": {
                    "geometries": {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "geometry": {"type": "LineString", "coordinates": [[26.0, 62.0], [26.02, 62.0]]},
                            }
                        ],
                    }
                },
            }
        ],
        "pagination": {"current-page": 2, "page-size": 100, "total-items": 2, "total-pages": 2},
    }
    asked = []

    def urlopen(request, timeout):
        query = parse_qs(urlparse(request.full_url).query)
        asked.append(query)
        page = first if query["page"] == ["1"] else second
        return io.BytesIO(json.dumps(page).encode())

    monkeypatch.setattr(external_module, "urlopen", urlopen)
    assert main(["fetch-external", "--source", "fi", "--dest", str(tmp_path)]) == 0

    cached = json.loads((tmp_path / LIPAS / f"{LIPAS}.geojson").read_text())
    assert [feature["properties"]["lipas-id"] for feature in cached["features"]] == [100, 600]
    assert [query["page"] for query in asked] == [["1"], ["2"]]
    assert asked[0]["page-size"] == ["100"]
    assert asked[0]["statuses"] == ["active"]
    assert set(asked[0]["type-codes"]) == {"4405", "4404"}
    hikes = {hike.ref: hike for hike in read_external(LIPAS, tmp_path)}
    assert hikes["100"].points == ((60.0, 25.0), (60.01, 25.0))
    assert (hikes["600"].name, hikes["600"].url) == (
        "Pieni luontopolku",
        "https://www.lipas.fi/liikuntapaikka/600",
    )
