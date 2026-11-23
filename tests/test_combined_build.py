"""A Europe and US build (ADR-0069): one Catalog from two regions' snapshots,
each read at its own paths level, and one serving export naming both regions.

Two tiny OSM XML snapshots stand for the regions. Each has a 2 km hiking
relation "Blue Trail" with a town at its start, and a 2 km named graded path
"Ridge Trail" in no relation: Europe is read at ``graded``, as a Europe build
is, so its path stays out of the snapshot and no named trail is built from it;
the US is read at ``trails``, so its path becomes a named-trail hike.
"""

import json
import math
from pathlib import Path

import pytest

from dayhike import Catalog, build_catalog, build_similarity
from dayhike.cli import main
from dayhike.regions import extracts_of, load_region
from dayhike.serving import export_serving
from dayhike.web import site_pages

KM_LAT = 1_000.0 / 6_371_000 * 180 / math.pi


def _snapshot(path: Path, lat0: float, lon0: float, town: str, first_id: int) -> Path:
    """Relation ``first_id`` "Blue Trail" north from (lat0, lon0), a town at its start,
    and a named graded path "Ridge Trail" 3 km east; OSM ids from ``first_id`` up."""

    def at(x_km: float, y_km: float) -> tuple[float, float]:
        return lat0 + y_km * KM_LAT, lon0 + x_km * KM_LAT / math.cos(math.radians(lat0))

    nodes, ways = [], []
    for way_id, x_km in ((first_id + 1, 0.0), (first_id + 2, 3.0)):
        refs = []
        for step in range(5):
            node_id = way_id * 10 + step
            lat, lon = at(x_km, step * 0.5)
            nodes.append(f'<node id="{node_id}" lat="{lat:.10f}" lon="{lon:.10f}" version="1"/>')
            refs.append(f'<nd ref="{node_id}"/>')
        name = "" if way_id == first_id + 1 else '<tag k="name" v="Ridge Trail"/>'
        ways.append(
            f'<way id="{way_id}" version="1">{"".join(refs)}'
            f'<tag k="highway" v="path"/><tag k="sac_scale" v="hiking"/>{name}</way>'
        )
    lat, lon = at(0.3, 0)
    nodes.append(
        f'<node id="{first_id * 10}" lat="{lat:.10f}" lon="{lon:.10f}" version="1">'
        f'<tag k="place" v="town"/><tag k="name" v="{town}"/></node>'
    )
    relation = (
        f'<relation id="{first_id}" version="1"><member type="way" ref="{first_id + 1}" role=""/>'
        '<tag k="type" v="route"/><tag k="route" v="hiking"/><tag k="name" v="Blue Trail"/></relation>'
    )
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6">\n'
        + "\n".join([*sorted(nodes), *ways, relation])
        + "\n</osm>\n"
    )
    return path


@pytest.fixture(scope="module")
def inputs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("regions")
    (root / "europe").mkdir()
    (root / "us").mkdir()
    europe = _snapshot(root / "europe" / "europe.osm", 46.3, 13.8, "Bled", 1_000)
    _snapshot(root / "us" / "us.osm", 40.0, -105.3, "Boulder", 2_000)
    return europe.parent, root / "us"


@pytest.fixture(scope="module")
def combined(inputs: tuple[Path, Path], tmp_path_factory: pytest.TempPathFactory) -> Catalog:
    europe, us = inputs
    return build_catalog([europe, us], tmp_path_factory.mktemp("combined") / "catalog.sqlite", paths_of={us: "trails"})


def _hikes(catalog: Catalog) -> list[tuple[int, str, str, int]]:
    return sorted((hike.id, hike.kind, hike.name, round(hike.length_m, -1)) for hike in catalog.dayhikes)


def test_a_europe_and_us_build_reads_europe_as_a_europe_build_and_the_us_with_its_trails(
    inputs: tuple[Path, Path], combined: Catalog, tmp_path: Path
) -> None:
    europe, us = inputs
    alone = build_catalog(europe, tmp_path / "europe" / "catalog.sqlite")
    us_alone = build_catalog(us, tmp_path / "us" / "catalog.sqlite", paths="trails")

    assert _hikes(alone) == [(1_000, "relation", "Blue Trail", 2_000)]
    assert [(kind, name) for _, kind, name, _ in _hikes(us_alone)] == [
        ("relation", "Blue Trail"),
        ("trail", "Ridge Trail"),
    ]
    # Each region's hikes as it builds them alone; only the shared name now says where each is.
    assert _hikes(combined) == [
        (1_000, "relation", "Blue Trail near Bled", 2_000),
        (2_000, "relation", "Blue Trail near Boulder", 2_000),
        (*_hikes(us_alone)[1][:3], 2_000),
    ]


def test_a_paths_level_for_something_that_is_not_an_input_is_refused(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    europe, us = inputs
    with pytest.raises(ValueError, match="not inputs"):
        build_catalog(europe, tmp_path / "catalog.sqlite", paths_of={us: "trails"})


def test_the_ingest_command_takes_a_paths_level_per_input(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    europe, us = inputs
    path = tmp_path / "catalog.sqlite"

    assert main(["ingest", str(europe), str(us), "--catalog", str(path), "--paths", f"{us}=trails"]) == 0

    assert sorted(hike.name for hike in Catalog.open(path).dayhikes) == [
        "Blue Trail near Bled",
        "Blue Trail near Boulder",
        "Ridge Trail",
    ]


def test_the_us_region_is_the_48_states_from_four_geofabrik_groups() -> None:
    us = load_region("us-48")

    assert (us.label, us.continent) == ("the contiguous United States", "North America")
    assert [url.rsplit("/", 1)[1] for url in us.extracts] == [
        f"us-{group}-latest.osm.pbf" for group in ("midwest", "northeast", "south", "west")
    ]
    assert extracts_of([load_region("europe"), us]) == [*load_region("europe").extracts, *us.extracts]


def test_one_export_serves_both_regions_and_its_pages_name_them(combined: Catalog, tmp_path: Path) -> None:
    build_similarity(combined.path, tmp_path / "dem")
    regions = [load_region("europe"), load_region("us-48")]
    out = export_serving(combined.path, tmp_path / "web-serving", regions=regions)
    meta = json.loads((out / "meta.json").read_text())

    assert meta["regions"] == [
        {"label": "Europe", "continent": "Europe"},
        {"label": "the contiguous United States", "continent": "North America"},
    ]
    assert meta["dayhikes"] == 3
    pages = site_pages(None, regions=meta["regions"])
    assert "hikes across Europe and the contiguous United States.</p>" in pages["index"]
