"""France: schema_randonnee datasets from data.gouv.fr as external hike layers (ADR-0062)."""

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dayhike import Catalog, build_catalog, build_similarity
import dayhike.external as external_module
from dayhike.cli import main
from dayhike.external import (
    EXTERNAL_SOURCES,
    FR_LICENCES,
    external_id,
    fetch_external,
    french_datasets,
    read_external,
    schema_problem,
)
from dayhike.serving import ServingData, export_serving
from dayhike.sources import read_register
from dayhike.web import create_app

from test_built_hikes import FIXTURE

EXTERNAL_DIR = Path(__file__).parent / "fixtures" / "external"
ECRINS = "fr-ecrins-randonnees"
TREK_URL = "https://rando.example.fr/trek/200-Le-lac-Fixture"


def _feature(id_local: str, name: str = "Une rando", practice: str = "A pied", **extra) -> dict:
    return {
        "type": "Feature",
        "properties": {"id_local": id_local, "nom_itineraire": name, "pratique": practice, **extra},
        "geometry": {"type": "LineString", "coordinates": [[6.0, 44.0, 1500], [6.0, 44.02, 1600]]},
    }


def _serve(monkeypatch: pytest.MonkeyPatch, features: list[dict]) -> list[str]:
    """Make every download the given GeoJSON; the list collects the URLs asked for."""
    asked: list[str] = []

    def urlopen(request, timeout):
        asked.append(request.full_url)
        return io.BytesIO(json.dumps({"type": "FeatureCollection", "features": features}).encode())

    monkeypatch.setattr(external_module, "urlopen", urlopen)
    return asked


def test_the_adapter_keeps_walks_and_reads_them_from_their_first_point() -> None:
    hikes = {hike.ref: hike for hike in read_external(ECRINS, EXTERNAL_DIR)}

    # Mountaineering and mountain-bike routes are not walks.
    assert sorted(hikes) == ["100", "200", "500", "600"]
    lake = hikes["200"]
    assert (lake.name, lake.url) == ("Le lac Fixture", TREK_URL)
    # The parts that meet are one line (the part 13 km away is left out), as (lat, lon).
    assert lake.points == ((45.4, 15.3), (45.41, 15.31), (45.42, 15.3))
    # A record with no name is read too: ingest names it after the nearest place.
    assert hikes["500"].name is None
    assert hikes["500"].url is None


@pytest.fixture(scope="module")
def catalog_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("france")
    path = directory / "catalog.sqlite"
    build_catalog(FIXTURE, path, sources=[], external=[ECRINS], external_dir=EXTERNAL_DIR)
    build_similarity(path, directory / "dem")
    return path


def test_ingest_adds_french_walks_and_drops_the_twin_of_an_osm_hike(catalog_path: Path) -> None:
    catalog = Catalog.open(catalog_path)

    french = {hike.source_ref: hike for hike in catalog.dayhikes if hike.kind == "external"}
    # 100 lies on an OSM hike (a Twin), 600 is under 1 km.
    assert sorted(french) == ["200", "500"]
    lake = french["200"]
    assert lake.id == external_id(ECRINS, "200")
    assert (lake.name, lake.name_origin, lake.source, lake.source_url) == ("Le lac Fixture", "external", ECRINS, TREK_URL)
    report = catalog.ingest_report
    assert (report.external_added, report.dropped_external_twin, report.dropped_external_length) == (2, 1, 1)
    # The record with no name is named after the nearest place, or else is synthetic.
    assert french["500"].name == "Unnamed hike (Écrins 500)"
    assert french["500"].name_origin == "synthetic"


def test_the_api_credits_a_french_walk_under_licence_ouverte(catalog_path: Path) -> None:
    data = ServingData(export_serving(catalog_path, catalog_path.parent / "web-serving"))
    client = TestClient(create_app(data, None))

    source = client.get(f"/api/hikes/{external_id(ECRINS, '200')}").json()["source"]
    assert source["id"] == ECRINS
    assert (source["licence"], source["url"]) == ("Licence Ouverte 2.0", TREK_URL)
    credits = client.get("/credits.html").text
    assert "Hiking routes from Randonnées du Parc national des Écrins" in credits
    assert "Licence Ouverte 2.0" in credits


def test_the_register_lists_every_dataset_it_uses_under_a_licence_the_gate_accepts() -> None:
    register = {row["id"]: row for row in read_register()}
    datasets = french_datasets()

    assert ECRINS in {dataset.id for dataset in datasets}
    for dataset in datasets:
        assert dataset.id in EXTERNAL_SOURCES, dataset.id
        row = register[dataset.id]
        assert row["url"] == dataset.page and row["url"].startswith("https://www.data.gouv.fr/fr/datasets/")
        # A used dataset is under a licence the gate accepts, and says so; every other one is rejected with its reason
        # (a dataset the gate accepts can still be rejected for its file, see ``schema_problem``).
        if row["status"] == "used":
            assert dataset.licence in FR_LICENCES and row["licence"] == FR_LICENCES[dataset.licence], dataset.id
        else:
            assert row["status"] == "rejected" and row["reason"], dataset.id
        assert row["status"] == "used" or dataset.licence not in FR_LICENCES or "schema" in row["reason"], dataset.id


def test_fetch_caches_only_what_the_adapter_reads_and_drops_heights(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _serve(monkeypatch, [_feature("1", "La Meije", url="https://rando.example.fr/1", medias=[{"licence": "tous droits réservés"}])])

    assert main(["fetch-external", "--source", ECRINS, "--dest", str(tmp_path)]) == 0

    assert asked == [next(dataset.file for dataset in french_datasets() if dataset.id == ECRINS)]
    cached = json.loads((tmp_path / ECRINS / f"{ECRINS}.geojson").read_text())
    (feature,) = cached["features"]
    assert feature["properties"] == {
        "id_local": "1",
        "nom_itineraire": "La Meije",
        "pratique": "A pied",
        "url": "https://rando.example.fr/1",
    }
    assert feature["geometry"]["coordinates"] == [[6.0, 44.0], [6.0, 44.02]]


@pytest.mark.parametrize(
    ("features", "problem"),
    [
        ([], "no records"),
        ([_feature("1"), _feature("1", "Une autre")], "id_local repeats"),
        ([{"type": "Feature", "properties": {"NOM_BOUCLE": "Boucle"}, "geometry": None}], "nom_itineraire"),
        ([_feature("1"), _feature("2", "")], None),
    ],
)
def test_a_file_that_does_not_follow_the_schema_is_named(features: list[dict], problem: str | None) -> None:
    found = schema_problem(features)

    assert (found is None) if problem is None else (problem in found)


def test_a_file_that_does_not_follow_the_schema_is_not_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, [_feature("0"), _feature("0", "Une autre")])

    with pytest.raises(ValueError, match="id_local repeats"):
        fetch_external(ECRINS, tmp_path)
    assert not list(tmp_path.rglob("*.geojson"))


def test_a_dataset_under_another_licence_is_skipped_before_it_is_downloaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _serve(monkeypatch, [_feature("1")])
    older = next(dataset for dataset in french_datasets() if dataset.licence not in FR_LICENCES)

    with pytest.raises(ValueError, match=f"{older.id}.*{older.licence}"):
        fetch_external(older.id, tmp_path)
    assert asked == []
    assert not (tmp_path / older.id).exists()


def test_fetching_france_caches_the_datasets_that_pass_and_says_why_it_skips_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _serve(monkeypatch, [_feature("1")])

    assert main(["fetch-external", "--source", "fr", "--dest", str(tmp_path)]) == 0

    out = capsys.readouterr()
    accepted = [dataset.id for dataset in french_datasets() if dataset.licence in FR_LICENCES]
    assert sorted(path.name for path in tmp_path.rglob("*.geojson")) == sorted(f"{id}.geojson" for id in accepted)
    skipped = [dataset.id for dataset in french_datasets() if dataset.licence not in FR_LICENCES]
    for id in skipped:
        assert f"skipped {id}:" in out.err
