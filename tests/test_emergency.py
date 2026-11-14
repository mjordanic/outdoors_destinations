import json
import re
import shutil
from pathlib import Path

import numpy as np
import pytest

from dayhike import build_catalog, build_similarity
from dayhike.cli import main
from dayhike.serving import ServingData, ServingExportError, emergency_services, export_countries, export_serving

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
CITIES = Path(__file__).parent / "fixtures" / "geonames.txt"
RIDGE_WALK_ID = 1001
# Every country a Geofabrik Europe extract has hikes in (the Catalog's coverage today), and the US.
COVERED = set(
    "AL AD AT BY BE BA BG HR CY CZ DK EE FO FI FR GE DE GI GR GG JE HU IS IE IM IT XK LV LI LT LU MK MT MD MC ME "
    "NL NO PL PT RO RU SM RS SK SI ES SE CH TR UA GB AX SJ US".split()
)
# Sources that speak for no country's own services: a row with only these says why in its notes.
THIRD_PARTY = ("https://www.gov.uk/foreign-travel-advice/", "https://digital-strategy.ec.europa.eu/")


@pytest.fixture(scope="module")
def catalog_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("emergency")
    path = directory / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    build_similarity(path, directory / "dem")
    return path


@pytest.fixture(scope="module")
def export(catalog_path: Path) -> Path:
    return export_serving(catalog_path, catalog_path.parent / "web-serving", geonames_path=CITIES)


def _countries(path: Path, *squares: tuple[str, float, float]) -> Path:
    """Natural Earth-like countries: a 2° square per (code, west, south); France's ISO_A2 is -99 there."""
    features = []
    for code, west, south in squares:
        ring = [[west, south], [west + 2, south], [west + 2, south + 2], [west, south + 2], [west, south]]
        properties = {"NAME": code, "ISO_A2": "-99", "ISO_A2_EH": code}
        features.append({"type": "Feature", "properties": properties, "geometry": {"type": "Polygon", "coordinates": [ring]}})
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return path


def _copy(export: Path, to: Path) -> Path:
    shutil.copytree(export, to)
    (to / "countries.npy").unlink(missing_ok=True)
    return to


def test_every_covered_country_has_a_row_with_numbers_and_a_source() -> None:
    rows = emergency_services()
    assert COVERED <= set(rows), sorted(COVERED - set(rows))
    for code, row in rows.items():
        assert re.fullmatch(r"[A-Z]{2}", code)
        assert row["numbers"] and all(re.fullmatch(r"\d{2,5}", number) for number in row["numbers"]), code
        assert row["sources"] and all(source.startswith("https://") for source in row["sources"]), code
        assert row["how"], code
        assert (row["rescue"] is None) == (row["rescue_url"] is None), code
        assert row["rescue_url"] is None or row["rescue_url"].startswith("https://"), code
        official = [source for source in row["sources"] if not source.startswith(THIRD_PARTY)]
        assert official or row["notes"], f"{code}: no official national source and no note why"
    assert rows["HR"]["rescue"] == "HGSS" and rows["HR"]["numbers"] == ["112"]
    assert rows["GB"]["numbers"] == ["999", "112"] and rows["GB"]["rescue"] == "Mountain Rescue"
    assert rows["US"]["numbers"] == ["911"]


def test_a_hike_shows_the_emergency_services_of_the_country_its_trailhead_is_in(export: Path, tmp_path: Path) -> None:
    out = _copy(export, tmp_path / "out")
    # The fixture's hikes start in Hikeland (14-16 E, 44-46 N), here Croatia.
    export_countries(out, _countries(tmp_path / "c.geojson", ("HR", 14, 44), ("SI", 20, 44)))

    data = ServingData(out)
    assert set(np.load(out / "countries.npy").tolist()) == {"HR"}
    emergency = data.detail(RIDGE_WALK_ID)["emergency"]
    assert emergency["country"] == "HR" and emergency["numbers"] == ["112"]
    assert emergency["rescue"] == "HGSS" and emergency["rescue_url"].startswith("https://")


def test_a_trailhead_outside_every_shape_takes_the_country_of_the_nearest_place(export: Path, tmp_path: Path) -> None:
    out = _copy(export, tmp_path / "out")
    export_countries(out, _countries(tmp_path / "c.geojson", ("SI", 30, 44)))  # a coast the shapes leave out
    assert set(np.load(out / "countries.npy").tolist()) == {"HR"}  # the fixture's places near the hikes

    # Without countries.npy at all, the server asks the town nearest the start.
    (out / "countries.npy").unlink()
    assert ServingData(out).detail(RIDGE_WALK_ID)["emergency"]["country"] == "HR"


def test_a_hike_in_a_country_with_no_row_fails_the_export(export: Path, catalog_path: Path, tmp_path: Path) -> None:
    out = _copy(export, tmp_path / "out")
    with pytest.raises(ServingExportError, match=r"ZZ \(\d+ Trailheads\)"):
        export_countries(out, _countries(tmp_path / "c.geojson", ("ZZ", 14, 44)))
    assert not (out / "countries.npy").exists()

    bare = export_serving(catalog_path, tmp_path / "bare")  # no places, no shapes: no country at all
    with pytest.raises(ServingExportError, match="no country"):
        export_countries(bare)
    assert ServingData(bare).detail(RIDGE_WALK_ID)["emergency"] is None


@pytest.mark.parametrize("code", ["GB", "US"])
def test_a_hike_in_the_uk_or_the_us_shows_their_numbers(export: Path, tmp_path: Path, code: str) -> None:
    out = _copy(export, tmp_path / "out")
    trailheads = np.load(out / "trailheads.npy")
    np.save(out / "countries.npy", np.full(len(trailheads), code, dtype="<U2"))

    assert ServingData(out).detail(RIDGE_WALK_ID)["emergency"] == emergency_services()[code]


def test_countries_are_exported_with_the_serving_export_or_beside_one(catalog_path: Path, tmp_path: Path, capsys) -> None:
    countries = _countries(tmp_path / "c.geojson", ("HR", 14, 44))
    out = export_serving(catalog_path, tmp_path / "out", countries_path=countries)
    assert set(np.load(out / "countries.npy").tolist()) == {"HR"}
    assert not (export_serving(catalog_path, tmp_path / "out") / "countries.npy").exists()  # a rerun without drops it

    meta = (out / "meta.json").read_bytes()
    assert main(["export-countries", "--out", str(out), "--countries", str(countries)]) == 0
    assert capsys.readouterr().out.strip() == str(out / "countries.npy")
    assert (out / "meta.json").read_bytes() == meta
    assert main(["export-countries", "--out", str(out), "--countries", str(_countries(tmp_path / "z.geojson", ("ZZ", 14, 44)))]) == 1
