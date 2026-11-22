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
# Every country a Geofabrik Europe extract has hikes in (the Catalog's coverage today), the US, and the
# Canada and Mexico trailheads a US extract takes in across the border.
COVERED = set(
    "AL AD AT BY BE BA BG HR CY CZ DK EE FO FI FR GE DE GI GR GG JE HU IS IE IM IT XK LV LI LT LU MK MT MD MC ME "
    "NL NO PL PT RO RU SM RS SK SI ES SE CH TR UA GB AX SJ US CA MX".split()
)
# The ADM0_A3 code Natural Earth names a country by, which its boundary lines use.
A3 = {"HR": "HRV", "SI": "SVN", "IT": "ITA"}
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
    return _boxes(path, *((code, west, south, west + 2, south + 2) for code, west, south in squares))


def _boxes(path: Path, *boxes: tuple[str, float, float, float, float]) -> Path:
    """Natural Earth-like countries: a box per (code, west, south, east, north), as ``_countries``."""
    features = []
    for code, west, south, east, north in boxes:
        ring = [[west, south], [east, south], [east, north], [west, north], [west, south]]
        properties = {"NAME": code, "ISO_A2": "-99", "ISO_A2_EH": code, "ADM0_A3": A3.get(code, code + "X")}
        features.append({"type": "Feature", "properties": properties, "geometry": {"type": "Polygon", "coordinates": [ring]}})
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return path


def _borders(path: Path, *lines: tuple[str, str, list[tuple[float, float]]]) -> Path:
    """Natural Earth-like land boundary lines: per (left, right, [(lon, lat), ...]) one line between two
    countries, named by their ADM0_A3 codes as Natural Earth names them."""
    features = [
        {
            "type": "Feature",
            "properties": {"ADM0_A3_L": left, "ADM0_A3_R": right},
            "geometry": {"type": "LineString", "coordinates": [list(point) for point in points]},
        }
        for left, right, points in lines
    ]
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return path


def _osm_borders(
    path: Path,
    codes: tuple[str, str],
    *ways: list[tuple[float, float]],
    maritime: bool = False,
    partial: str | None = None,
) -> Path:
    """An OpenStreetMap file as ``osmium tags-filter <extract> r/admin_level=2`` writes one: the national
    boundary relations of the two ``codes`` sharing the ``ways`` through their points ((lon, lat)), a
    point two ways share being one node; the relation of the code ``partial`` also lists a way the
    file leaves out, as one of a country reaching beyond the extract does."""
    ids = {point: i for i, point in enumerate(dict.fromkeys(point for way in ways for point in way), 1)}
    nodes = "".join(f'<node id="{i}" lat="{lat}" lon="{lon}" version="1"/>' for (lon, lat), i in ids.items())
    tags = '<tag k="boundary" v="administrative"/><tag k="admin_level" v="2"/>'
    sea = '<tag k="maritime" v="yes"/>' if maritime else ""
    lines = "".join(
        f'<way id="{w}" version="1">{"".join(f'<nd ref="{ids[point]}"/>' for point in way)}{tags}{sea}</way>'
        for w, way in enumerate(ways, 1)
    )
    members = "".join(f'<member type="way" ref="{w}" role="outer"/>' for w in range(1, len(ways) + 1))
    beyond = f'<member type="way" ref="{len(ways) + 1}" role="outer"/>'
    relations = "".join(
        f'<relation id="{i}" version="1">{members}{beyond if code == partial else ""}'
        f'<tag k="type" v="boundary"/>{tags}<tag k="ISO3166-1:alpha2" v="{code}"/></relation>'
        for i, code in enumerate(codes, 1)
    )
    path.write_text(f'<?xml version="1.0" encoding="UTF-8"?><osm version="0.6">{nodes}{lines}{relations}</osm>')
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
    assert rows["CA"]["numbers"] == ["911"] and rows["MX"]["numbers"] == ["911"]


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
    with pytest.raises(ServingExportError, match="no country"):  # a shape beside the hikes is not theirs
        export_countries(bare, _countries(tmp_path / "near.geojson", ("HR", 16, 44)))
    assert ServingData(bare).detail(RIDGE_WALK_ID)["emergency"] is None


@pytest.mark.parametrize("code", ["GB", "US", "CA", "MX"])
def test_a_hike_in_the_uk_the_us_canada_or_mexico_shows_their_numbers(export: Path, tmp_path: Path, code: str) -> None:
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


# The fixture's Ridge Walk starts at 45.00 N 15.00 E and walks north to 45.09 N; the next hike starts at 45.20 N 15.10 E.
HIKELAND = (("HR", 14, 44), ("SI", 14, 46), ("IT", 20, 44))


def test_a_hike_whose_route_crosses_a_border_also_shows_the_other_countrys_services(export: Path, tmp_path: Path) -> None:
    out = _copy(export, tmp_path / "out")
    crossed = _borders(tmp_path / "b.geojson", ("HRV", "SVN", [(14.9, 45.05), (15.05, 45.05)]))  # 5.6 km north of the start
    export_countries(out, _countries(tmp_path / "c.geojson", *HIKELAND), crossed)

    data = ServingData(out)
    ridge = data.detail(RIDGE_WALK_ID)
    assert ridge["emergency"]["country"] == "HR"
    assert ridge["border_emergency"] == emergency_services()["SI"]
    assert data.detail(1002)["border_emergency"] is None  # 17 km from that line, and its route does not reach it


@pytest.mark.parametrize(
    ("line", "flagged"),
    [
        ([(15.02, 44.9), (15.02, 45.1)], True),  # 1.6 km east of the start
        ([(15.03, 44.9), (15.03, 45.1)], False),  # 2.4 km east
        ([(15.1, 44.9825), (14.9, 44.9825)], True),  # 1.9 km south
        ([(15.1, 44.9775), (14.9, 44.9775)], False),  # 2.5 km south
    ],
)
def test_a_hike_starting_within_2_km_of_a_border_also_shows_the_other_countrys_services(
    export: Path, tmp_path: Path, line: list[tuple[float, float]], flagged: bool
) -> None:
    out = _copy(export, tmp_path / "out")
    border = _borders(tmp_path / "b.geojson", ("SVN", "HRV", line))  # Croatia on its right
    export_countries(out, _countries(tmp_path / "c.geojson", *HIKELAND), border)

    shown = ServingData(out).detail(RIDGE_WALK_ID)["border_emergency"]
    assert shown == (emergency_services()["SI"] if flagged else None)


def test_borders_are_exported_with_the_countries_or_beside_an_export(catalog_path: Path, tmp_path: Path) -> None:
    countries = _countries(tmp_path / "c.geojson", *HIKELAND)
    borders = _borders(tmp_path / "b.geojson", ("HRV", "SVN", [(14.9, 45.05), (15.05, 45.05)]))
    out = export_serving(catalog_path, tmp_path / "out", countries_path=countries, borders_path=borders)
    assert np.load(out / "borders.npy").tolist() == ["SI", "", "", ""]  # Ridge Walk's route crosses it
    export_countries(out, countries)  # a rerun without border lines drops them
    assert not (out / "borders.npy").exists()

    assert main(["export-countries", "--out", str(out), "--countries", str(countries), "--borders", str(borders)]) == 0
    assert np.load(out / "borders.npy").tolist() == ["SI", "", "", ""]
    assert main(["export-countries", "--out", str(out), "--borders", str(borders)]) == 1  # sides need the countries
    assert not (export_serving(catalog_path, out) / "borders.npy").exists()


# Natural Earth's border at 15.01 E puts the Ridge Walk's start (15.00 E) in the country west of it; the
# OpenStreetMap border just west of the start puts it in the one east of it, where it is.
def _west_of_start(metres: float) -> float:
    return 15 - metres / (111_195 * np.cos(np.radians(45)))


@pytest.mark.parametrize(
    ("start", "across", "metres"),
    [
        ("US", "MX", 150),  # Boquillas Canyon Trail, 150 m inside the US on the Rio Grande
        ("BA", "ME", 400),  # Kočanj kuk, in Bosnia and Herzegovina near Montenegro
        ("US", "CA", 3),  # Long Trail near Carleton Mountain, 3 m inside Vermont
    ],
)
def test_a_hike_starting_near_a_border_leads_with_the_country_its_trailhead_is_in_by_the_osm_border(
    export: Path, tmp_path: Path, start: str, across: str, metres: float
) -> None:
    out = _copy(export, tmp_path / "out")
    countries = _boxes(tmp_path / "c.geojson", (across, 13, 44, 15.01, 46), (start, 15.01, 44, 17, 46))
    coarse = _borders(tmp_path / "b.geojson", (across + "X", start + "X", [(15.01, 44.9), (15.01, 45.1)]))
    osm = _osm_borders(tmp_path / "borders.osm", (start, across), [(_west_of_start(metres), 44.9), (_west_of_start(metres), 45.1)])

    export_countries(out, countries, coarse)
    assert ServingData(out).detail(RIDGE_WALK_ID)["emergency"]["country"] == across  # Natural Earth alone
    export_countries(out, countries, coarse, [osm])

    ridge = ServingData(out).detail(RIDGE_WALK_ID)
    assert ridge["emergency"] == emergency_services()[start]
    assert ridge["border_emergency"] == emergency_services()[across]  # both blocks stay, the other second
    assert np.load(out / "countries.npy")[0] == start


def test_a_hike_far_from_any_osm_border_keeps_its_natural_earth_country(export: Path, tmp_path: Path) -> None:
    out = _copy(export, tmp_path / "out")
    # Natural Earth puts the other hikes' starts (15.1-15.5 E) back in Slovenia; the OSM border, 8 km and more
    # west of them, has Croatia on its east side: only the Ridge Walk's start, 150 m from it, takes its side.
    countries = _boxes(tmp_path / "c.geojson", ("SI", 13, 44, 15.01, 46), ("HR", 15.01, 44, 15.08, 46), ("SI", 15.08, 44, 17, 46))
    osm = _osm_borders(tmp_path / "borders.osm", ("HR", "SI"), [(_west_of_start(150), 44.9), (_west_of_start(150), 45.1)])
    export_countries(out, countries)
    before = np.load(out / "countries.npy")
    export_countries(out, countries, None, [osm])

    after = np.load(out / "countries.npy")
    assert before.tolist() == ["SI", "SI", "SI", "SI"]
    assert after.tolist() == ["HR", "SI", "SI", "SI"]
    data = ServingData(out)
    assert data.detail(1002)["emergency"]["country"] == "SI" and data.detail(1002)["border_emergency"] is None


def test_a_hike_the_osm_borders_move_still_shows_the_country_natural_earth_put_it_in(export: Path, tmp_path: Path) -> None:
    out = _copy(export, tmp_path / "out")
    countries = _boxes(tmp_path / "c.geojson", ("SI", 13, 44, 15.01, 46), ("HR", 15.01, 44, 17, 46))
    osm = _osm_borders(tmp_path / "borders.osm", ("HR", "SI"), [(_west_of_start(150), 44.9), (_west_of_start(150), 45.1)])
    export_countries(out, countries, None, [osm])  # no Natural Earth lines: nothing else flags it

    ridge = ServingData(out).detail(RIDGE_WALK_ID)
    assert ridge["emergency"]["country"] == "HR" and ridge["border_emergency"] == emergency_services()["SI"]


def test_a_start_inside_a_whole_osm_country_takes_that_country(export: Path, tmp_path: Path) -> None:
    out = _copy(export, tmp_path / "out")
    # San Marino as an enclave Natural Earth leaves out: its samples 2.5 km off its border are all in Italy,
    # so only its OSM shape, whole in the extract, says the Ridge Walk starts in it.
    countries = _boxes(tmp_path / "c.geojson", ("IT", 13, 44, 17, 46))
    ring = [(14.995, 44.995), (15.005, 44.995), (15.005, 45.005), (14.995, 45.005), (14.995, 44.995)]
    osm = _osm_borders(tmp_path / "borders.osm", ("SM", "IT"), ring, partial="IT")
    export_countries(out, countries, None, [osm])

    assert np.load(out / "countries.npy").tolist() == ["SM", "IT", "IT", "IT"]
    ridge = ServingData(out).detail(RIDGE_WALK_ID)
    assert ridge["emergency"]["country"] == "SM" and ridge["border_emergency"] == emergency_services()["IT"]


def test_an_osm_border_way_takes_its_sides_from_the_whole_border(export: Path, tmp_path: Path) -> None:
    out = _copy(export, tmp_path / "out")
    # South of 45.08 N Natural Earth has Croatia on both sides of the border, so the way there cannot tell
    # its sides; the way north of it, drawn the other way from their shared node, can.
    countries = _boxes(tmp_path / "c.geojson", ("HR", 13, 44, 17, 45.08), ("HR", 13, 45.08, 15.01, 46), ("SI", 15.01, 45.08, 17, 46))
    x = _west_of_start(150)
    osm = _osm_borders(tmp_path / "borders.osm", ("HR", "SI"), [(x, 45.1), (x, 45.3)], [(x, 45.1), (x, 44.9)])
    export_countries(out, countries, None, [osm])
    assert np.load(out / "countries.npy")[0] == "SI"


def test_a_maritime_osm_border_does_not_move_a_start(export: Path, tmp_path: Path) -> None:
    out = _copy(export, tmp_path / "out")
    countries = _boxes(tmp_path / "c.geojson", ("SI", 13, 44, 15.01, 46), ("HR", 15.01, 44, 17, 46))
    osm = _osm_borders(tmp_path / "borders.osm", ("HR", "SI"), [(_west_of_start(150), 44.9), (_west_of_start(150), 45.1)], maritime=True)
    export_countries(out, countries, None, [osm])
    assert np.load(out / "countries.npy")[0] == "SI"


def test_osm_borders_are_exported_with_the_countries_or_beside_an_export(catalog_path: Path, tmp_path: Path) -> None:
    countries = _boxes(tmp_path / "c.geojson", ("SI", 13, 44, 15.01, 46), ("HR", 15.01, 44, 17, 46))
    osm = _osm_borders(tmp_path / "borders.osm", ("HR", "SI"), [(_west_of_start(150), 44.9), (_west_of_start(150), 45.1)])
    out = export_serving(catalog_path, tmp_path / "out", countries_path=countries, osm_borders_paths=[osm])
    assert np.load(out / "countries.npy")[0] == "HR"

    assert main(["export-countries", "--out", str(out), "--countries", str(countries)]) == 0
    assert np.load(out / "countries.npy")[0] == "SI"
    assert main(["export-countries", "--out", str(out), "--countries", str(countries), "--osm-borders", str(osm)]) == 0
    assert np.load(out / "countries.npy")[0] == "HR"
    assert main(["export-countries", "--out", str(out), "--osm-borders", str(osm)]) == 1  # sides need the countries
