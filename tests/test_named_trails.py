"""Named-trail hikes (ADR-0066): same-name paths in no relation joined into hikes.

Each snapshot is hand-built on a kilometre grid around 45N 15E: ``Trails.way``
lays a way through (x km, y km) points, a node per point, the same point the
same node. The seam is the built-hike source ``named_trails`` (ADR-0038); the
last tests build a fixture PBF through ``build_catalog(paths=...)``.
"""

import math
import shutil
import sqlite3
from pathlib import Path

import pytest
from test_streaming_ingest import _sorted_pbf

from dayhike import Catalog, build_catalog
from dayhike.built import BuildContext, built_dayhike, built_id
from dayhike.catalog import assemble_main_walk, geometry_store_path_for
from dayhike.ingest import run_ingest
from dayhike.named_trails import named_trails
from dayhike.names import unique_names
from dayhike.osm import OsmRelation, OsmSnapshot, OsmWay

KM_LAT = 1_000.0 / 6_371_000 * 180 / math.pi


def _lat_lon(x_km: float, y_km: float) -> tuple[float, float]:
    lat = 45.0 + y_km * KM_LAT
    return lat, 15.0 + x_km * KM_LAT / math.cos(math.radians(lat))


class Trails:
    def __init__(self) -> None:
        self.snapshot = OsmSnapshot()
        self.node_of: dict[tuple[float, float], int] = {}
        self.in_relation: set[int] = set()
        self.dayhikes: list = []

    def way(self, way_id: int, *points: tuple[float, float], member: bool = False, **tags: str) -> None:
        """A way through ``points``; ``member`` puts it in a hiking relation."""
        node_ids = []
        for xy in points:
            node_id = self.node_of.setdefault(xy, 1 + len(self.node_of))
            self.snapshot.nodes[node_id] = _lat_lon(*xy)
            node_ids.append(node_id)
        self.snapshot.ways[way_id] = OsmWay(tuple(node_ids), {"highway": "path", **tags})
        if member:
            self.in_relation.add(way_id)

    def trailhead(self, x_km: float, y_km: float) -> None:
        self.snapshot.trailheads.append(_lat_lon(x_km, y_km))

    def place(self, x_km: float, y_km: float, name: str) -> None:
        self.snapshot.places.append((*_lat_lon(x_km, y_km), name))

    def built(self) -> list:
        self.snapshot.path_ways = frozenset(set(self.snapshot.ways) - self.in_relation)
        snapshot = self.snapshot
        context = BuildContext(
            snapshot,
            tuple(self.dayhikes),
            lambda relation: assemble_main_walk(relation, snapshot.ways, snapshot.nodes, None),
        )
        return named_trails(context)

    def hikes(self) -> list[tuple[str, int]]:
        """(name, metres) of each named-trail hike built, by name."""
        made = [built_dayhike(walk, self.snapshot, None)[0] for walk in self.built()]
        return sorted((hike.name, round(hike.length_m, -1)) for hike in made)

    def labelled(self) -> list[tuple[str, int]]:
        """``hikes`` as the build names them, every name unique (ADR-0068), the relation DayHikes left out."""
        made = [built_dayhike(walk, self.snapshot, None)[0] for walk in self.built()]
        named = unique_names([*self.dayhikes, *made], self.snapshot.places, self.snapshot.destinations)
        return sorted((hike.name, round(hike.length_m, -1)) for hike in named[len(self.dayhikes) :])


def test_same_name_paths_that_share_a_node_are_one_hike():
    trails = Trails()
    trails.way(1, (0, 0), (0, 1), name="Ridge Trail")
    trails.way(2, (0, 1), (0, 2.5), name="Ridge Trail")

    assert trails.hikes() == [("Ridge Trail", 2_500)]
    (walk,) = trails.built()
    assert (walk.kind, walk.parts, set(walk.way_ids)) == ("trail", (), {1, 2})


def test_another_name_or_no_shared_node_is_another_hike():
    trails = Trails()
    trails.way(1, (0, 0), (0, 1.5), name="Ridge Trail")
    trails.way(2, (0, 1.5), (0, 3), name="Lake Trail")
    trails.way(3, (5, 0), (5, 2), name="Ridge Trail")

    assert trails.hikes() == [("Lake Trail", 1_500), ("Ridge Trail", 1_500), ("Ridge Trail", 2_000)]


def test_same_name_paths_whose_ends_are_within_50_m_are_one_hike():
    trails = Trails()
    trails.way(1, (0, 0), (0, 1), name="Ridge Trail")
    trails.way(2, (0, 1.03), (0, 2), name="Ridge Trail")  # across a road, 30 m on
    trails.way(3, (0, 2.2), (0, 3.5), name="Ridge Trail")  # 200 m on: another piece

    assert trails.hikes() == [("Ridge Trail", 1_300), ("Ridge Trail", 2_000)]


def test_a_trail_under_1_km_is_left_out():
    trails = Trails()
    trails.way(1, (0, 0), (0, 0.5), name="Short Trail")
    trails.way(2, (0, 0.5), (0, 0.9), name="Short Trail")

    assert trails.hikes() == []


def test_one_constant_sets_the_shortest_named_trail_at_1_km(monkeypatch):
    import dayhike.named_trails

    assert dayhike.named_trails.MIN_TRAIL_LENGTH_M == 1_000
    trails = Trails()
    trails.way(1, (0, 0), (0, 1.5), name="Ridge Trail")
    trails.way(2, (5, 0), (5, 2.5), name="Lake Trail")
    monkeypatch.setattr(dayhike.named_trails, "MIN_TRAIL_LENGTH_M", 2_000)

    assert trails.hikes() == [("Lake Trail", 2_500)]


def test_tracks_unnamed_paths_and_relation_members_are_not_joined():
    trails = Trails()
    trails.way(1, (0, 0), (0, 1.2), name="Ridge Trail")
    trails.way(2, (0, 1.2), (0, 3), name="Ridge Trail", highway="track")
    trails.way(3, (0, 1.2), (1, 1.2))
    trails.way(4, (0, 0), (-2, 0), name="Ridge Trail", member=True)

    assert trails.hikes() == [("Ridge Trail", 1_200)]


def test_a_hike_starts_at_its_end_with_a_trailhead_node():
    for end in ((0, 0), (0, 2)):
        trails = Trails()
        trails.way(1, (0, 0), (0, 1), (0, 2), name="Ridge Trail")
        trails.trailhead(*end)

        (walk,) = trails.built()
        assert walk.vertices[0][1] == _lat_lon(*end)


def test_a_mostly_paved_or_lit_walk_and_a_closed_path_are_not_trails():
    trails = Trails()
    trails.way(1, (0, 0), (0, 2), name="River Greenway", surface="asphalt")
    trails.way(2, (2, 0), (2, 2), name="Park Walk", highway="footway", lit="yes")
    trails.way(3, (4, 0), (4, 2), name="Summit Path", highway="footway")
    trails.way(4, (6, 0), (6, 2), name="Private Trail", access="private")
    trails.way(5, (8, 0), (8, 2), name="Mall Walk", highway="footway", footway="sidewalk")

    assert trails.hikes() == [("Summit Path", 2_000)]


def test_a_short_paved_stretch_does_not_break_or_drop_a_trail():
    trails = Trails()
    trails.way(1, (0, 0), (0, 1), name="Gorge Trail", surface="ground")
    trails.way(2, (0, 1), (0, 1.2), name="Gorge Trail", surface="asphalt")
    trails.way(3, (0, 1.2), (0, 2), name="Gorge Trail")

    assert trails.hikes() == [("Gorge Trail", 2_000)]


def test_a_snowmobile_corridor_not_signed_for_walkers_is_not_a_trail():
    trails = Trails()
    trails.way(1, (0, 0), (0, 2), name="Corridor 5", snowmobile="designated")
    trails.way(2, (2, 0), (2, 2), name="Pond Trail", snowmobile="designated", foot="yes")

    assert trails.hikes() == [("Pond Trail", 2_000)]


def test_a_trail_that_a_relation_hike_already_walks_is_left_out():
    trails = Trails()
    trails.way(1, (0, 0), (0, 2), name="Ridge Trail", member=True)
    trails.way(2, (0.01, 0), (0.01, 2), name="Ridge Trail Old")
    relation = OsmRelation(900, (("way", 1),), {"route": "hiking", "name": "Ridge"}, ("",))
    trails.snapshot.relations.append(relation)
    walk = assemble_main_walk(relation, trails.snapshot.ways, trails.snapshot.nodes, None)
    from dayhike.catalog import _dayhike

    trails.dayhikes.append(_dayhike(900, "Ridge", "osm_name", walk.vertices, None, trails.snapshot, None))

    assert trails.hikes() == []


def test_the_same_trail_keeps_its_id_when_another_is_added():
    trails = Trails()
    trails.way(1, (0, 0), (0, 2), name="Ridge Trail")
    before = [built_dayhike(walk, trails.snapshot, None)[0].id for walk in trails.built()]
    trails.way(2, (5, 0), (5, 2), name="Lake Trail")
    after = [built_dayhike(walk, trails.snapshot, None)[0].id for walk in trails.built()]

    assert before[0] in after and len(after) == 2


def test_a_mostly_motor_bike_first_or_piste_walk_is_not_a_trail():
    trails = Trails()
    trails.way(1, (0, 0), (0, 2), name="ORV Trail", motorcycle="yes")
    trails.way(2, (2, 0), (2, 2), name="Jump Line", bicycle="designated")
    trails.way(3, (4, 0), (4, 2), name="Flow", **{"mtb:scale": "2"})
    trails.way(4, (6, 0), (6, 2), name="Summit Run", **{"piste:type": "downhill"})
    trails.way(5, (8, 0), (8, 2), name="Shared Trail", bicycle="designated", foot="designated")
    trails.way(6, (10, 0), (10, 2), name="Ridge Path", sac_scale="mountain_hiking", **{"mtb:scale": "3"})

    assert trails.hikes() == [("Ridge Path", 2_000), ("Shared Trail", 2_000)]


def test_a_short_motor_stretch_does_not_drop_a_trail():
    trails = Trails()
    trails.way(1, (0, 0), (0, 1.5), name="Creek Trail")
    trails.way(2, (0, 1.5), (0, 2), name="Creek Trail", atv="designated")

    assert trails.hikes() == [("Creek Trail", 2_000)]


def test_a_name_two_trails_share_adds_the_nearest_place_in_the_build():
    trails = Trails()
    trails.way(1, (0, 0), (0, 2), name="Ridge Trail")
    trails.way(2, (20, 0), (20, 2), name="Ridge Trail")
    trails.way(3, (40, 0), (40, 2), name="Lake Trail")
    trails.place(1, 0, "Concord")
    trails.place(21, 0, "Dover")
    trails.place(41, 0, "Keene")

    assert trails.hikes() == [("Lake Trail", 2_000), ("Ridge Trail", 2_000), ("Ridge Trail", 2_000)]
    assert trails.labelled() == [
        ("Lake Trail", 2_000),
        ("Ridge Trail near Concord", 2_000),
        ("Ridge Trail near Dover", 2_000),
    ]


def test_a_colour_name_adds_the_nearest_place_and_a_bare_trail_is_a_hike_near_it():
    trails = Trails()
    trails.way(1, (0, 0), (0, 2), name="Red Trail")
    trails.way(2, (20, 0), (20, 2), name="Blue")
    trails.way(3, (40, 0), (40, 2), name="Trail")
    trails.way(4, (60, 0), (60, 2), name="Yellow Trail")  # no place within 5 km
    trails.place(1, 0, "Concord")
    trails.place(21, 0, "Dover")
    trails.place(41, 0, "Keene")

    assert trails.labelled() == [
        ("Blue near Dover", 2_000),
        ("Hike near Keene", 2_000),
        ("Red Trail near Concord", 2_000),
        ("Yellow Trail", 2_000),
    ]


def test_a_name_a_relation_hike_has_adds_the_nearest_place_in_the_build():
    trails = Trails()
    trails.way(1, (0, 0), (0, 2), name="Lake Trail", member=True)
    trails.way(2, (20, 0), (20, 2), name="Lake Trail")
    trails.place(1, 0, "Concord")
    trails.place(21, 0, "Dover")
    relation = OsmRelation(900, (("way", 1),), {"route": "hiking", "name": "Lake Trail"}, ("",))
    trails.snapshot.relations.append(relation)
    walk = assemble_main_walk(relation, trails.snapshot.ways, trails.snapshot.nodes, None)
    from dayhike.catalog import _dayhike

    trails.dayhikes.append(_dayhike(900, "Lake Trail", "osm_name", walk.vertices, None, trails.snapshot, None))

    assert trails.labelled() == [("Lake Trail near Dover", 2_000)]


# The build: tests/fixtures/named_trails.osm ingested as a PBF, through the osmium
# catalog extract, as a real build is. See the fixture's comment for its ways.
BUILD_FIXTURE = Path(__file__).parent / "fixtures" / "named_trails.osm"
VALLEY_TRAIL = 3001
RIDGE_D_E, RIDGE_E_F, RIVER_GREENWAY, OLD_VALLEY_PATH, FOREST_ROAD = 301, 302, 303, 304, 305
F, TRAILHEAD = 13, 95
needs_osmium = pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium-tool")


@pytest.fixture(scope="module")
def pbf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _sorted_pbf(BUILD_FIXTURE, tmp_path_factory.mktemp("trails-pbf") / "trails.osm.pbf")


def _trails(catalog) -> list:
    return [hike for hike in catalog.dayhikes if hike.kind == "trail"]


@needs_osmium
def test_the_trails_level_builds_a_named_trail_into_the_catalog(pbf: Path, tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    catalog = build_catalog(pbf, path, paths="trails")

    # The paved greenway, the Twin of the Valley Trail and the track are left out.
    (trail,) = _trails(catalog)
    assert (trail.name, trail.name_origin, trail.parts) == ("Ridge Trail", "trail", ())
    assert trail.length_m == pytest.approx(2_500, abs=5)
    # It starts at F, the end with the trailhead node, and walks both ways against their node order.
    assert trail.route == f"{F}:-{RIDGE_E_F}.12,-{RIDGE_D_E}.11"
    assert trail.id == built_id(trail.route)
    with sqlite3.connect(path) as db:
        (source_ref,) = db.execute("SELECT source_ref FROM dayhikes WHERE id = ?", (trail.id,)).fetchone()
    assert source_ref == f"way/{RIDGE_E_F}"
    with sqlite3.connect(geometry_store_path_for(path)) as geo:
        nodes = [row[0] for row in geo.execute("SELECT node_id FROM vertices WHERE dayhike_id = ? ORDER BY seq", (trail.id,))]
        ways = geo.execute(
            "SELECT way_id FROM ways WHERE dayhike_id = ? ORDER BY walk_order", (trail.id,)
        ).fetchall()
    assert nodes == [F, 12, 11]
    assert [way for (way,) in ways] == [RIDGE_E_F, RIDGE_D_E]


@needs_osmium
def test_the_trails_level_reads_trail_ways_and_trailheads_but_no_tracks(pbf: Path, tmp_path: Path) -> None:
    _, _, _, snapshot = run_ingest([pbf], tmp_path / "catalog.sqlite", paths="trails")

    assert {RIDGE_D_E, RIDGE_E_F, RIVER_GREENWAY, OLD_VALLEY_PATH} <= snapshot.path_ways
    assert FOREST_ROAD not in snapshot.ways
    assert len(snapshot.trailheads) == 1


@needs_osmium
def test_the_default_build_as_for_europe_builds_no_named_trail(pbf: Path, tmp_path: Path) -> None:
    default = build_catalog(pbf, tmp_path / "default" / "catalog.sqlite")
    trails = build_catalog(pbf, tmp_path / "trails" / "catalog.sqlite", paths="trails")

    assert _trails(default) == []
    assert [hike for hike in trails.dayhikes if hike.kind != "trail"] == list(default.dayhikes)
    assert [hike.id for hike in default.dayhikes] == [VALLEY_TRAIL]


@needs_osmium
def test_the_ingest_command_takes_the_trails_level(pbf: Path, tmp_path: Path) -> None:
    from dayhike.cli import main

    path = tmp_path / "catalog.sqlite"
    assert main(["ingest", str(pbf), "--catalog", str(path), "--paths", "trails"]) == 0

    assert [hike.name for hike in _trails(Catalog.open(path))] == ["Ridge Trail"]


@needs_osmium
def test_the_export_serves_a_named_trail_with_its_route_and_way(pbf: Path, tmp_path: Path) -> None:
    from dayhike import build_similarity
    from dayhike.serving import ServingData, export_serving

    path = tmp_path / "catalog.sqlite"
    build_catalog(pbf, path, paths="trails")
    build_similarity(path, tmp_path / "dem")
    data = ServingData(export_serving(path, tmp_path / "web-serving"))
    (found,) = data.search("Ridge Trail")
    hike = data.detail(found["id"])

    assert hike["built"] == {"kind": "trail", "parts": [], "route": f"{F}:-{RIDGE_E_F}.12,-{RIDGE_D_E}.11"}
    assert hike["source"]["ref"] == f"way/{RIDGE_E_F}"
    assert hike["source"]["url"] == f"https://www.openstreetmap.org/way/{RIDGE_E_F}"
