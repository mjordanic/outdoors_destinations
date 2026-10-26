import json
from pathlib import Path

import shapely

from dayhike import build_catalog
from dayhike.cli import main
from dayhike.coverage import COVERED, UNCOVERED, VIEW, coverage_map

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"


def _countries(path: Path) -> Path:
    """Two square countries: Hikeland holds the fixture Trailheads, Emptyland none."""

    def square(name: str, west: float, south: float) -> dict:
        ring = [[west, south], [west + 2, south], [west + 2, south + 2], [west, south + 2], [west, south]]
        return {"type": "Feature", "properties": {"NAME": name}, "geometry": {"type": "Polygon", "coordinates": [ring]}}

    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": [square("Hikeland", 14, 44), square("Emptyland", 20, 44)]})
    )
    return path


def test_covered_country_is_shaded_from_the_catalogs_trailheads(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog)
    out = tmp_path / "coverage.svg"

    covered = coverage_map(catalog, _countries(tmp_path / "countries.geojson"), out, min_hikes=4, min_density=0)

    assert covered == [("Hikeland", 4)]
    svg = out.read_text()
    assert svg.startswith("<svg") and f'fill="{COVERED}"' in svg and f'fill="{UNCOVERED}"' in svg
    assert coverage_map(catalog, tmp_path / "countries.geojson", out, min_hikes=5, min_density=0) == []
    assert f'fill="{COVERED}"' not in out.read_text()


def test_sparse_country_is_not_shaded(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog)

    # Four Trailheads in a 2° square (about 35,000 km²) is 0.11 per 1,000 km².
    assert coverage_map(catalog, _countries(tmp_path / "c.geojson"), tmp_path / "a.svg", min_hikes=1, min_density=0.2) == []


def test_cli_prints_covered_countries(tmp_path: Path, capsys) -> None:
    catalog = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog)
    countries = _countries(tmp_path / "countries.geojson")

    assert main(["coverage-map", "--catalog", str(catalog), "--countries", str(countries), "--out", str(tmp_path / "m.svg")]) == 0
    assert (tmp_path / "m.svg").is_file()
    assert capsys.readouterr().out == ""  # 4 Trailheads are under the default 10


def test_the_map_frame_holds_the_atlantic_islands() -> None:
    frame = shapely.box(*VIEW)
    # Flores (Azores, the westernmost hikes) and Ponta do Pargo (Madeira, the southernmost).
    assert frame.contains(shapely.points([(-31.2, 39.45), (-17.25, 32.65)])).all()
