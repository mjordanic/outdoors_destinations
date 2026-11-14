from pathlib import Path

import pytest

from dayhike import build_catalog, cli
from dayhike.coverage import CENTRE, VIEW, coverage_map
from dayhike.dem import DEFAULT_DEM_BOUNDS, Bounds
from dayhike.extracts import GEOFABRIK_EXTRACTS
from dayhike.regions import (
    DEFAULT_REGION,
    RegionError,
    bounds_around,
    extracts_of,
    load_region,
    region_names,
)
from test_coverage import FIXTURE, _countries


def _region_file(path: Path, *, label: str, west: float, east: float, south: float = 44.0, north: float = 46.0) -> Path:
    path.write_text(
        f'label = "{label}"\n'
        f'extracts = ["https://download.geofabrik.de/{path.stem}-latest.osm.pbf"]\n'
        f"bounds = {{ south = {south}, west = {west}, north = {north}, east = {east} }}\n"
        "[map]\n"
        f"view = {{ west = {west}, south = {south}, east = {east}, north = {north} }}\n"
        f"centre = {{ lon = {(west + east) / 2}, lat = {(south + north) / 2} }}\n"
    )
    return path


def test_the_defaults_are_todays_europe_build() -> None:
    default = load_region(DEFAULT_REGION)
    assert default.bounds == DEFAULT_DEM_BOUNDS
    assert default.extracts == GEOFABRIK_EXTRACTS
    europe = load_region("europe")
    assert (europe.view.west, europe.view.south, europe.view.east, europe.view.north) == VIEW
    assert europe.centre == CENTRE


def test_every_packaged_region_loads() -> None:
    assert {"alps-dinarides", "europe", "us-colorado"} <= set(region_names())
    for name in region_names():
        region = load_region(name)
        assert region.extracts and all(url.startswith("https://download.geofabrik.de/") for url in region.extracts)
        assert region.bounds.west < region.bounds.east and region.view.west < region.view.east


def test_a_box_must_run_west_to_east(tmp_path: Path) -> None:
    with pytest.raises(RegionError, match="180"):
        load_region(_region_file(tmp_path / "aleutians.toml", label="Aleutians", west=172.0, east=-130.0))
    with pytest.raises(RegionError, match="no region 'atlantis'"):
        load_region("atlantis")


def test_two_regions_cover_the_box_around_both_and_all_their_extracts() -> None:
    regions = [load_region("alps-dinarides"), load_region("us-colorado")]
    assert bounds_around(regions) == Bounds(south=36.9, west=-109.1, north=50.6, east=20.4)
    assert extracts_of(regions) == [*GEOFABRIK_EXTRACTS, "https://download.geofabrik.de/north-america/us/colorado-latest.osm.pbf"]
    assert extracts_of([regions[0], regions[0]]) == list(GEOFABRIK_EXTRACTS)


def test_fetch_commands_read_the_regions(tmp_path: Path, monkeypatch) -> None:
    calls: dict[str, object] = {}
    monkeypatch.setattr(cli, "fetch_dem", lambda dest, bounds, **_: calls.setdefault("dem", []).append(bounds) or [])
    monkeypatch.setattr(cli, "fetch_worldcover", lambda dest, bounds, **_: calls.setdefault("lc", []).append(bounds) or [])
    monkeypatch.setattr(cli, "fetch_chelsa", lambda dest, bounds, **_: calls.setdefault("cl", []).append(bounds) or [])
    monkeypatch.setattr(cli, "fetch_geofabrik_extracts", lambda dest, urls: calls.setdefault("osm", []).append(urls) or [])
    own = _region_file(tmp_path / "hikeland.toml", label="Hikeland", west=14.0, east=16.0)

    for command in ("fetch-dem", "fetch-landcover", "fetch-climate"):
        assert cli.main([command, "--dest", str(tmp_path)]) == 0
        assert cli.main([command, "--dest", str(tmp_path), "--region", "us-colorado", "--region", str(own)]) == 0
        assert cli.main([command, "--dest", str(tmp_path), "--bounds", "1,2,3,4"]) == 0
    assert cli.main(["fetch-extracts", "--dest", str(tmp_path)]) == 0
    assert cli.main(["fetch-extracts", "--dest", str(tmp_path), "--region", str(own), "--region", "us-colorado"]) == 0

    colorado, hikeland = load_region("us-colorado").bounds, load_region(own).bounds
    given = Bounds(south=1, west=2, north=3, east=4)
    # Tiles for each region's own box; CHELSA's one file per variable for the box around both.
    for key in ("dem", "lc"):
        assert calls[key] == [DEFAULT_DEM_BOUNDS, colorado, hikeland, given]
    assert calls["cl"] == [DEFAULT_DEM_BOUNDS, Bounds(south=36.9, west=-109.1, north=46.0, east=16.0), given]
    assert calls["osm"] == [
        list(GEOFABRIK_EXTRACTS),
        ["https://download.geofabrik.de/hikeland-latest.osm.pbf", "https://download.geofabrik.de/north-america/us/colorado-latest.osm.pbf"],
    ]


def test_coverage_map_of_regions_and_of_the_world(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog)
    countries = _countries(tmp_path / "countries.geojson")
    east = _region_file(tmp_path / "east.toml", label="Emptyland", west=19.0, east=23.0)
    west = _region_file(tmp_path / "west.toml", label="Hikeland", west=13.0, east=17.0)
    base = ["coverage-map", "--catalog", str(catalog), "--countries", str(countries)]

    # Without --region: Europe, as coverage_map's own defaults draw it.
    assert cli.main([*base, "--out", str(tmp_path / "default.svg")]) == 0
    coverage_map(catalog, countries, tmp_path / "europe.svg")
    assert (tmp_path / "default.svg").read_text() == (tmp_path / "europe.svg").read_text()
    assert 'aria-label="Map of Europe;' in (tmp_path / "europe.svg").read_text()

    assert cli.main([*base, "--out", str(tmp_path / "one.svg"), "--region", str(west)]) == 0
    assert cli.main([*base, "--out", str(tmp_path / "two.svg"), "--region", str(west), "--region", str(east)]) == 0
    assert cli.main([*base, "--out", str(tmp_path / "world.svg"), "--world"]) == 0
    one, two, world = ((tmp_path / f"{name}.svg").read_text() for name in ("one", "two", "world"))
    assert 'aria-label="Map of Hikeland;' in one
    assert 'aria-label="Map of Hikeland and Emptyland;' in two
    assert 'aria-label="Map of the world;' in world
    # Equal Earth: a 10° by 2° frame at 45° is about four times wider than tall.
    width, height = (float(v) for v in two.split('viewBox="0 0 ')[1].split('"')[0].split())
    assert 3.5 < width / height < 4.5

    # Areas are kept in both projections, so the same countries count as covered.
    for view, centre in (((13.0, 44.0, 23.0, 46.0), None), ((13.0, 44.0, 17.0, 46.0), (15.0, 45.0))):
        assert coverage_map(catalog, countries, tmp_path / "x.svg", min_hikes=4, min_density=0, view=view, centre=centre) == [
            ("Hikeland", 4)
        ]
