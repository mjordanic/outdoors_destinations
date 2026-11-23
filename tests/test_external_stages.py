"""External routes are cut into DayStages and named after the nearest place as OSM relations are (ADR-0062, ADR-0040, ADR-0017).

Every test ingests ``long_tour.osm`` (a 40 km tour north along 16.0 E from 46.0 N, huts at
km 13 and km 27 100 m east of it, the village Saint-Départ 200 m east of its start) with an
external source cached in a temporary directory: the route is a line along the same
meridian, a vertex every km.
"""

import json
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from dayhike import build_catalog, build_similarity
from dayhike.external import EXTERNAL_ID_MAX, EXTERNAL_ID_MIN, read_external
from dayhike.serving import ServingData, export_serving
from dayhike.stages import day_stages
from dayhike.web import create_app

LONG_TOUR = Path(__file__).parent / "fixtures" / "long_tour.osm"
TURRUTE = "no-turrutebasen"
LIPAS = "fi-lipas"
SPORT_IRELAND = "ie-sport-ireland"
KM_LAT = 0.0089932161  # one km of latitude, as the fixture's nodes


def column(km: float, lon: float = 16.0, from_km: float = 0.0, to_km: float | None = None) -> list[list[float]]:
    """A line north along ``lon`` from ``from_km`` to ``to_km`` (default ``km``), a vertex every km, as [lon, lat]."""
    end = km if to_km is None else to_km
    return [[lon, round(46.0 + step * KM_LAT, 10)] for step in range(round(from_km), round(end) + 1)]


def write_cache(directory: Path, source: str, features: list[dict]) -> Path:
    folder = directory / source
    folder.mkdir(parents=True)
    (folder / f"{source}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return directory


def foot_route(number: str, name: str, maintainer: str, coordinates: list[list[float]]) -> dict:
    return {
        "type": "Feature",
        "properties": {"rutenummer": number, "rutenavn": name, "maintainer": maintainer, "type": "Fotrute"},
        "geometry": {"type": "LineString", "coordinates": coordinates},
    }


def ingest(directory: Path, source: str, tmp_path: Path, **kwargs):
    tmp_path.mkdir(exist_ok=True)
    return build_catalog(LONG_TOUR, tmp_path / "catalog.sqlite", external=[source], external_dir=directory, **kwargs)


def external_hikes(catalog) -> dict[str, object]:
    return {hike.name: hike for hike in catalog.dayhikes if hike.kind == "external"}


def test_a_norway_route_without_a_name_of_its_own_is_named_after_the_nearest_place(tmp_path: Path) -> None:
    directory = write_cache(
        tmp_path / "cache",
        TURRUTE,
        [
            # No rutenavn, 3 km north from the village Saint-Départ.
            foot_route("7", "", "DNT", column(3)),
            # No rutenavn and no place within 5 km: the maintainer and the number, the last fallback.
            foot_route("8", "Ukjent", "Statskog", column(3, lon=17.0)),
            # A name of its own is kept, however near a place.
            foot_route("9", "Fjellvegen", "DNT", column(3, lon=16.01)),
        ],
    )

    catalog = ingest(directory, TURRUTE, tmp_path, sources=[])

    names = sorted(hike.name for hike in catalog.dayhikes if hike.kind == "external")
    assert names == ["Fjellvegen", "Hike near Saint-Départ", "Statskog 8"]


def test_a_finland_and_an_ireland_record_without_a_name_are_named_after_the_nearest_place(tmp_path: Path) -> None:
    lipas = write_cache(
        tmp_path / "fi",
        LIPAS,
        [
            {
                "type": "Feature",
                "properties": {"lipas-id": 1, "name": "", "status": "active", "type-code": 4405},
                "geometry": {"type": "LineString", "coordinates": column(3)},
            }
        ],
    )
    ireland = write_cache(
        tmp_path / "ie",
        SPORT_IRELAND,
        [
            {
                "type": "Feature",
                "properties": {"TrailID": 5, "Name": None, "Activity": "Walking", "Website": None},
                "geometry": {"type": "LineString", "coordinates": column(3)},
            }
        ],
    )

    finland = ingest(lipas, LIPAS, tmp_path / "fi-out", sources=[])
    ireland_catalog = ingest(ireland, SPORT_IRELAND, tmp_path / "ie-out", sources=[])

    for catalog in (finland, ireland_catalog):
        assert [hike.name for hike in catalog.dayhikes if hike.kind == "external"] == ["Hike near Saint-Départ"]


def test_a_finland_and_an_ireland_record_with_no_name_and_no_place_are_shown_as_unnamed_hikes(tmp_path: Path) -> None:
    # Lipas 123 starts 6 km south of Saint-Départ, so no place lies within 5 km of its start; 66 km long, it is cut
    # at the huts. Sport Ireland trail 5 lies at 17 E, far from any place.
    lipas = write_cache(
        tmp_path / "fi",
        LIPAS,
        [
            {
                "type": "Feature",
                "properties": {"lipas-id": 123, "name": "", "status": "active", "type-code": 4405},
                "geometry": {"type": "LineString", "coordinates": column(0, from_km=-6, to_km=60)},
            }
        ],
    )
    ireland = write_cache(
        tmp_path / "ie",
        SPORT_IRELAND,
        [
            {
                "type": "Feature",
                "properties": {"TrailID": 5, "Name": None, "Activity": "Walking", "Website": None},
                "geometry": {"type": "LineString", "coordinates": column(3, lon=17.0)},
            }
        ],
    )

    shown = {}
    for directory, source, out in ((lipas, LIPAS, tmp_path / "fi-out"), (ireland, SPORT_IRELAND, tmp_path / "ie-out")):
        catalog = ingest(directory, source, out, sources=[])
        hikes = [hike for hike in catalog.dayhikes if hike.kind == "external"]
        build_similarity(out / "catalog.sqlite", out / "dem")
        client = TestClient(create_app(ServingData(export_serving(out / "catalog.sqlite", out / "serving")), None))
        for hike in hikes:
            shown[hike.name] = (hike.name_origin, client.get(f"/api/hikes/{hike.id}").json()["name"])

    assert shown == {
        "Unnamed hike (Lipas 123)": ("synthetic", "Unnamed hike (Lipas 123)"),
        "Unnamed hike (Lipas 123), stage 1: to Refuge du Lac": (
            "stage",
            "Unnamed hike (Lipas 123), stage 1: to Refuge du Lac",
        ),
        "Unnamed hike (Lipas 123), stage 2: Refuge du Lac – Refuge du Col": (
            "stage",
            "Unnamed hike (Lipas 123), stage 2: Refuge du Lac – Refuge du Col",
        ),
        "Unnamed hike (Sport Ireland trail/5)": ("synthetic", "Unnamed hike (Sport Ireland trail/5)"),
    }


def test_a_long_norway_route_is_cut_into_daystages_beside_the_route(tmp_path: Path) -> None:
    # 60 km: stages end at the huts (km 13, 27); the 33 km after the last hut has no cut point.
    directory = write_cache(tmp_path / "cache", TURRUTE, [foot_route("L1", "Langruta", "DNT", column(60))])

    catalog = ingest(directory, TURRUTE, tmp_path, sources=[])

    hikes = {hike.name: hike for hike in catalog.dayhikes if hike.kind == "external"}
    assert sorted(hikes) == [
        "Langruta",
        "Langruta, stage 1: Saint-Départ – Refuge du Lac",
        "Langruta, stage 2: Refuge du Lac – Refuge du Col",
    ]
    route = hikes["Langruta"]
    first, second = (hikes[name] for name in sorted(hikes) if "stage" in name)
    assert [round(hike.length_m) for hike in (first, second)] == [13_000, 14_000]
    for stage in (first, second):
        # An external hike, so its licence stays the source's; its record is the route's, as a relation DayStage's is its tour's.
        assert (stage.kind, stage.name_origin, stage.source, stage.source_ref) == ("external", "stage", TURRUTE, "L1")
        assert stage.source_url == route.source_url
        assert EXTERNAL_ID_MIN <= stage.id < EXTERNAL_ID_MAX
    assert len({route.id, first.id, second.id}) == 3
    assert (first.trailhead.latitude, first.trailhead.longitude) == (46.0, 16.0)
    report = catalog.ingest_report
    assert (report.external_added, report.external_stages) == (3, 2)


def test_a_stage_keeps_its_id_when_the_rest_of_the_source_changes(tmp_path: Path) -> None:
    alone = write_cache(tmp_path / "alone", TURRUTE, [foot_route("L1", "Langruta", "DNT", column(60))])
    crowded = write_cache(
        tmp_path / "crowded",
        TURRUTE,
        [foot_route("A0", "Aaa", "DNT", column(3, lon=17.0)), foot_route("L1", "Langruta", "DNT", column(60))],
    )

    ids = [
        {hike.name: hike.id for hike in ingest(directory, TURRUTE, out, sources=[]).dayhikes if "stage" in hike.name}
        for directory, out in ((alone, tmp_path / "a"), (crowded, tmp_path / "b"))
    ]

    assert len(ids[0]) == 2
    assert ids[0] == ids[1]


def test_a_finland_route_of_joined_parts_is_cut_like_any_long_route(tmp_path: Path) -> None:
    directory = write_cache(
        tmp_path / "cache",
        LIPAS,
        [
            {
                "type": "Feature",
                "properties": {"lipas-id": 42, "name": "Kuusamon reitti", "status": "active", "type-code": 4405},
                "geometry": {"type": "MultiLineString", "coordinates": [column(30), column(0, from_km=30, to_km=60)]},
            }
        ],
    )

    catalog = ingest(directory, LIPAS, tmp_path, sources=[])

    stages = sorted(hike.name for hike in catalog.dayhikes if hike.kind == "external" and hike.name_origin == "stage")
    assert stages == [
        "Kuusamon reitti, stage 1: Saint-Départ – Refuge du Lac",
        "Kuusamon reitti, stage 2: Refuge du Lac – Refuge du Col",
    ]
    assert {hike.source_ref for hike in catalog.dayhikes if hike.name_origin == "stage"} == {"42"}


def test_a_route_of_25_km_or_less_is_not_cut(tmp_path: Path) -> None:
    directory = write_cache(tmp_path / "cache", TURRUTE, [foot_route("S1", "Kortruta", "DNT", column(24))])

    catalog = ingest(directory, TURRUTE, tmp_path, sources=[])

    assert [hike.name for hike in catalog.dayhikes if hike.kind == "external"] == ["Kortruta"]
    assert catalog.ingest_report.external_stages == 0


def test_a_stage_that_is_a_twin_of_an_osm_daystage_is_left_out_and_counted(tmp_path: Path) -> None:
    # The route's first two stages walk what the OSM tour's first two stages walk: the OSM hikes are listed.
    directory = write_cache(tmp_path / "cache", TURRUTE, [foot_route("L1", "Langruta", "DNT", column(60))])

    catalog = ingest(directory, TURRUTE, tmp_path, sources=[day_stages])

    assert [hike.name for hike in catalog.dayhikes if hike.kind == "external"] == ["Langruta"]
    report = catalog.ingest_report
    assert (report.external_added, report.external_stages, report.dropped_external_twin) == (1, 0, 2)


def test_a_stage_that_is_a_twin_of_a_kept_route_is_left_out(tmp_path: Path) -> None:
    # The long route's first stage walks what the route "Første dag" walks: the route is listed, the stage is not.
    directory = write_cache(
        tmp_path / "cache",
        TURRUTE,
        [foot_route("L1", "Langruta", "DNT", column(60)), foot_route("L2", "Første dag", "DNT", column(13, lon=16.0))],
    )

    catalog = ingest(directory, TURRUTE, tmp_path, sources=[])

    names = sorted(hike.name for hike in catalog.dayhikes if hike.kind == "external")
    assert "Langruta, stage 1: Saint-Départ – Refuge du Lac" not in names
    assert "Langruta, stage 2: Refuge du Lac – Refuge du Col" in names
    assert "Første dag" in names


def test_the_ireland_adapter_reads_a_record_with_a_blank_name(tmp_path: Path) -> None:
    ireland = write_cache(
        tmp_path / "ie",
        SPORT_IRELAND,
        [
            {
                "type": "Feature",
                "properties": {"TrailID": 5, "Name": " ", "Activity": "Walking", "Website": None},
                "geometry": {"type": "LineString", "coordinates": column(3)},
            }
        ],
    )

    (hike,) = read_external(SPORT_IRELAND, ireland)

    assert (hike.ref, hike.name) == ("trail/5", None)


@pytest.mark.parametrize("name", ["", "Ukjent", "ukjent"])
def test_a_norway_route_name_that_says_unknown_is_no_name(tmp_path: Path, name: str) -> None:
    directory = write_cache(tmp_path / "cache", TURRUTE, [foot_route("7", name, "DNT", column(3))])

    (hike,) = read_external(TURRUTE, directory)

    assert hike.name is None
    assert hike.fallback == "DNT 7"
