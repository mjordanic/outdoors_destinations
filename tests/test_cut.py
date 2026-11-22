"""``dayhike cut-serving`` (ADR-0082): a Europe + US export cut to one side, or to a cut file's
areas, is a complete export of its own, and ranks as the uncut export with that side ticked.

The test export is the web fixture twice: as it is (Croatia), and moved 120° west into the United
States with every OSM id moved too, built into one Catalog as a Europe and US build is (ADR-0069).
Highway snaps, a tile extract, countries and one external source are added beside it by hand.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import struct
import tarfile
from pathlib import Path

import numpy as np
import pytest

from dayhike import build_catalog, build_similarity
from dayhike.cli import main
from dayhike.cut import CUTS_DIR, CutError, Keep, cut_serving, kept_twins, load_keep, tile_box
from dayhike.serving import NAME_INDEX, REGION_SPLIT_LON, ServingData, _read_name_index, export_serving
from dayhike.similarity import CORE_MEASURES

FIXTURES = Path(__file__).parent / "fixtures"
SHIFT = 120.0
US_IDS = 100_000  # added to every OSM id of the moved copy
WEIGHTS = {name: 100.0 for name in CORE_MEASURES}
WISHES = ({"length": 5_000}, {"length": 12_000, "elevation_gain": 400})


def _moved(text: str, lon_field: str) -> str:
    return re.sub(rf'{lon_field}"([-0-9.]+)"', lambda m: f'{lon_field}"{float(m.group(1)) - SHIFT:.10f}"', text)


def _tile_name(level: int, lat: float, lon: float) -> str:
    size = {0: 4.0, 2: 0.25}[level]
    index = int((lat + 90) // size) * round(360 / size) + int((lon + 180) // size)
    digits = f"{index:09d}" if level == 2 else f"{index:06d}"
    return "/".join([str(level), *(digits[i : i + 3] for i in range(0, len(digits), 3))]) + ".gph"


EU_TILE, US_TILE = _tile_name(2, 45.1, 15.1), _tile_name(2, 45.1, 15.1 - SHIFT)  # in Croatia and in the moved copy
FAR_TILE = _tile_name(2, 60.1, 25.1)  # in Finland, far from every hike
CLIMATE_GRID = [[1 / 120, 0.0, -180.0, 0.0, -1 / 120, 90.0], 21_600, 43_200]  # 30" cells, as CHELSA's


def _add_climate_and_water(graph: Path) -> None:
    """Climate cells and water tiles at each Trailhead and beside it, as ``sample-trailgraph`` writes
    them (the fixture TrailGraph is built without rasters)."""
    with sqlite3.connect(graph) as db:
        db.execute("INSERT OR REPLACE INTO trailgraph_meta VALUES ('climate_grid', ?)", (json.dumps(CLIMATE_GRID),))
        db.execute("CREATE TABLE climate_cells (cell INTEGER PRIMARY KEY, months BLOB NOT NULL)")
        db.execute(
            "CREATE TABLE water_tiles (south INTEGER NOT NULL, west INTEGER NOT NULL, transform TEXT NOT NULL,"
            " height INTEGER NOT NULL, width INTEGER NOT NULL, PRIMARY KEY (south, west))"
        )
        db.execute(
            "CREATE TABLE water_cells (south INTEGER NOT NULL, west INTEGER NOT NULL, cell INTEGER NOT NULL,"
            " PRIMARY KEY (south, west, cell)) WITHOUT ROWID"
        )
        for lat, lon in db.execute("SELECT latitude, longitude FROM junctions").fetchall():
            row, col = int((90 - lat) * 120), int((lon + 180) * 120)
            db.execute("INSERT OR IGNORE INTO climate_cells VALUES (?, ?)", (row * 43_200 + col, b"\0" * 4))
            south, west = int(lat // 3 * 3), int(lon // 3 * 3)
            db.execute("INSERT OR IGNORE INTO water_tiles VALUES (?, ?, '[]', 1, 1)", (south, west))
            db.execute("INSERT OR IGNORE INTO water_cells VALUES (?, ?, ?)", (south, west, col))


def _write_tiles(directory: Path, names: list[str]) -> None:
    """A tile extract as ``valhalla_build_extract`` writes it: ``index.bin`` first, then the tiles."""
    directory.mkdir()
    with tarfile.open(directory / "valhalla_tiles.tar", "w", format=tarfile.PAX_FORMAT) as tar:
        index = tarfile.TarInfo("index.bin")
        index.size = 16 * len(names)
        tar.addfile(index, _io(bytes(index.size)))
        for name in names:
            info = tarfile.TarInfo(name)
            body = name.encode() * 7
            info.size = len(body)
            tar.addfile(info, _io(body))
    (directory / "valhalla.json").write_text(json.dumps({"mjolnir": {"tile_extract": "/data/valhalla/valhalla_tiles.tar"}}))


def _io(body: bytes):
    import io

    return io.BytesIO(body)


def _index(tar_path: Path) -> list[tuple[int, int, int]]:
    with tarfile.open(tar_path) as tar:
        members = list(tar)
        data = tar.extractfile(members[0]).read()
    assert members[0].name == "index.bin"
    return [struct.unpack_from("<QII", data, at) for at in range(0, len(data), 16)]


@pytest.fixture(scope="module")
def whole(tmp_path_factory: pytest.TempPathFactory) -> ServingData:
    root = tmp_path_factory.mktemp("cut")
    text = (FIXTURES / "catalog.osm").read_text()
    moved = _moved(text, "lon=")
    moved = re.sub(r'(<(?:node|way|relation) id="|ref=")(\d+)', lambda m: f"{m.group(1)}{int(m.group(2)) + US_IDS}", moved)
    for name, body in (("europe", text), ("us", moved)):
        (root / name).mkdir()
        (root / name / f"{name}.osm").write_text(body)
    cities = (FIXTURES / "geonames.txt").read_text().splitlines()
    us_cities = []
    for line in cities:
        fields = line.split("\t")
        fields[0], fields[5], fields[8] = str(int(fields[0]) + US_IDS), f"{float(fields[5]) - SHIFT:.5f}", "US"
        us_cities.append("\t".join(fields))
    geonames = root / "geonames.txt"
    geonames.write_text("\n".join(cities + us_cities) + "\n")
    path = root / "catalog.sqlite"
    build_catalog([root / "europe", root / "us"], path)
    build_similarity(path, root / "dem")
    out = export_serving(path, root / "web-serving", geonames_path=geonames)
    trailheads = np.load(out / "trailheads.npy")
    west = trailheads[:, 1] < REGION_SPLIT_LON
    np.save(out / "countries.npy", np.where(west, "US", "HR"))
    np.save(out / "borders.npy", np.where(west, "CA", ""))
    lat, lon = trailheads[:, 0], trailheads[:, 1]
    np.savez_compressed(  # each Trailhead snapped where it is, and a place no Trailhead is at
        out / "snaps.npz",
        lat=np.append(lat, 10.0), lon=np.append(lon, 10.0),
        snap_lat=np.append(lat, 10.0), snap_lon=np.append(lon, 10.0), metres=np.zeros(len(lat) + 1),
    )  # fmt: skip
    _add_climate_and_water(out / "trailgraph.sqlite")
    tiles = [EU_TILE, US_TILE, FAR_TILE, _tile_name(0, 45.0, 15.0), _tile_name(0, 45.0, 15.0 - SHIFT)]
    _write_tiles(out / "valhalla", tiles)
    with sqlite3.connect(out / "hikes.sqlite") as db:  # one US hike from an external source
        us_hike = db.execute("SELECT id FROM hikes WHERE id > ? ORDER BY id LIMIT 1", (US_IDS,)).fetchone()[0]
        db.execute("UPDATE hikes SET source = 'us-test' WHERE id = ?", (us_hike,))
    meta = json.loads((out / "meta.json").read_text())
    meta["sources"] = {"us-test": {"name": "US test trails", "licence": "CC0", "licence_url": "", "url": ""}}
    meta["attribution"]["us-test"] = "US test trails"
    meta["regions"] = [
        {"label": "Europe", "continent": "Europe"},
        {"label": "the contiguous United States", "continent": "North America"},
    ]
    meta["drive_time"] = True
    (out / "meta.json").write_text(json.dumps(meta))
    data = ServingData(out)
    assert data.regions == ["europe", "us"] and data.trailgraph is not None
    data.geonames = geonames  # type: ignore[attr-defined]
    return data


@pytest.fixture(scope="module")
def cuts(whole: ServingData, tmp_path_factory: pytest.TempPathFactory) -> dict[str, ServingData]:
    root = tmp_path_factory.mktemp("cuts")
    return {
        side: ServingData(cut_serving(whole.directory, root / side, load_keep(side), geonames_path=whole.geonames))
        for side in ("europe", "us")
    }


def _side_ids(data: ServingData, side: str) -> list[int]:
    return data.store.ids[data.region_rows([side])].tolist()


def _same(a, b) -> str:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


@pytest.mark.parametrize("side", ["europe", "us"])
@pytest.mark.parametrize("return_to_start", [True, False])
def test_a_cut_ranks_as_the_whole_export_with_only_its_side_ticked(
    whole: ServingData, cuts: dict[str, ServingData], side: str, return_to_start: bool
) -> None:
    cut = cuts[side]
    allowed = whole.region_rows([side])
    seeds = [*_side_ids(whole, side), *WISHES]
    for seed in seeds:
        for loops in (False, True):
            expected = whole.rank(seed, WEIGHTS, allowed_rows=allowed, return_to_start=return_to_start, loops=loops)
            found = cut.rank(seed, WEIGHTS, return_to_start=return_to_start, loops=loops)
            assert expected, seed
            assert _same(list(found), list(expected)), (seed, loops)
            assert (found.other_areas, found.loop_search) == (expected.other_areas, expected.loop_search)


@pytest.mark.parametrize("side", ["europe", "us"])
def test_a_cut_shows_each_hike_it_keeps_as_the_whole_export_does(
    whole: ServingData, cuts: dict[str, ServingData], side: str
) -> None:
    cut = cuts[side]
    for dayhike_id in _side_ids(whole, side):
        assert _same(cut.detail(dayhike_id), whole.detail(dayhike_id)), dayhike_id
        assert _same(cut.way_back_walk(dayhike_id), whole.way_back_walk(dayhike_id)), dayhike_id
    south, north = (40.0, 50.0)
    west, east = (10.0, 20.0) if side == "europe" else (10.0 - SHIFT, 20.0 - SHIFT)
    assert _same(cut.segments_in_box(south, west, north, east, 1_000), whole.segments_in_box(south, west, north, east, 1_000))
    assert _same(cut.in_box(south, west, north, east, 100), whole.in_box(south, west, north, east, 100))


@pytest.mark.parametrize("side", ["europe", "us"])
def test_a_cut_holds_only_its_side(whole: ServingData, cuts: dict[str, ServingData], side: str) -> None:
    cut = cuts[side]
    west = side == "us"

    assert cut.regions == [side]
    assert cut.store.ids.tolist() == _side_ids(whole, side)
    assert ((np.asarray(cut.trailheads[:, 1]) < REGION_SPLIT_LON) == west).all()
    assert set(cut.countries.tolist()) == {"US" if west else "HR"}
    with sqlite3.connect(cut.directory / "hikes.sqlite") as db:
        assert [i for (i,) in db.execute("SELECT id FROM hikes ORDER BY id")] == cut.store.ids.tolist()
        assert [i for (i,) in db.execute("SELECT id FROM hike_boxes ORDER BY id")] == cut.store.ids.tolist()
    with sqlite3.connect(cut.directory / "trailgraph.sqlite") as db:
        for table, lon in (("junctions", "longitude"), ("amenities", "longitude"), ("segment_boxes", "west")):
            sides = {bool(w) for (w,) in db.execute(f"SELECT {lon} < ? FROM {table}", (REGION_SPLIT_LON,))}
            assert sides == {west}, table
        assert db.execute("SELECT count(*) FROM segments").fetchone()[0] > 0
    with sqlite3.connect(cut.directory / "places.sqlite") as db:
        countries = {country for (country,) in db.execute("SELECT DISTINCT country FROM places")}
        assert countries and ("US" in countries) == west and (countries == {"US"}) == west
    with sqlite3.connect(cut.directory / "landmarks.sqlite") as db:
        hikes = {hike for (hike,) in db.execute("SELECT hike FROM landmark_hikes")}
        assert hikes and hikes <= set(cut.store.ids.tolist())
    with np.load(cut.directory / "snaps.npz") as snaps:
        assert sorted(zip(snaps["lat"].tolist(), snaps["lon"].tolist())) == sorted(map(tuple, np.asarray(cut.trailheads).tolist()))
    with tarfile.open(cut.directory / "valhalla" / "valhalla_tiles.tar") as tar:
        tiles = [m.name for m in tar if m.name.endswith(".gph")]
    assert tiles == [US_TILE, _tile_name(0, 45.0, 15.0 - SHIFT)] if west else [EU_TILE, FAR_TILE, _tile_name(0, 45.0, 15.0)]
    with sqlite3.connect(cut.directory / "trailgraph.sqlite") as db:
        cells = [cell for (cell,) in db.execute("SELECT cell FROM climate_cells")]
        assert cells and all(((cell % 43_200) / 120 - 180 < REGION_SPLIT_LON) == west for cell in cells)
        assert {(w < REGION_SPLIT_LON) for (w,) in db.execute("SELECT west FROM water_tiles")} == {west}
        assert {(w < REGION_SPLIT_LON) for (w,) in db.execute("SELECT west FROM water_cells")} == {west}


@pytest.mark.parametrize("side", ["europe", "us"])
def test_a_cut_has_the_name_index_of_its_hikes_and_searches_as_the_whole_export(
    whole: ServingData, cuts: dict[str, ServingData], side: str
) -> None:
    cut = cuts[side]
    kept = set(cut.store.ids.tolist())

    assert (whole.directory / NAME_INDEX).is_file()
    assert _read_name_index(cut.directory, cut.store.ids) is not None  # its own hikes', so the server reads it
    for query in ("ridge", "walk", "rdige wlak", "lake"):
        expected = [hike for hike in whole.search(query, limit=100) if hike["id"] in kept]
        assert _same(cut.search(query, limit=100), expected), query


def test_both_cuts_together_hold_every_row_of_the_whole_export(whole: ServingData, cuts: dict[str, ServingData]) -> None:
    def count(data: ServingData, table: str) -> int:
        with sqlite3.connect(data.directory / "trailgraph.sqlite") as db:
            return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    tables = ("segments", "junctions", "segment_ways", "segment_boxes", "amenities", "climate_cells")
    for table in (*tables, "water_tiles", "water_cells"):
        assert count(cuts["europe"], table) + count(cuts["us"], table) == count(whole, table), table

    def named(data: ServingData) -> set[int]:  # the relations a Segment names, all a server reads
        with sqlite3.connect(data.directory / "trailgraph.sqlite") as db:
            return {i for (i,) in db.execute("SELECT DISTINCT r.value FROM segments s, json_each(s.relation_ids) r")}

    for cut in cuts.values():
        with sqlite3.connect(cut.directory / "trailgraph.sqlite") as db:
            assert {i for (i,) in db.execute("SELECT id FROM relations")} == named(cut)
    assert named(cuts["europe"]) | named(cuts["us"]) == named(whole) and not named(cuts["europe"]) & named(cuts["us"])


@pytest.mark.parametrize("side", ["europe", "us"])
def test_a_cut_names_its_side_and_credits_only_the_sources_its_hikes_come_from(
    whole: ServingData, cuts: dict[str, ServingData], side: str
) -> None:
    meta = cuts[side].meta

    assert meta["dayhikes"] == len(_side_ids(whole, side))
    assert meta["data_tag"] == f"{whole.meta['data_tag']}-{'us' if side == 'us' else 'eu'}"
    assert meta["drive_time"] is True and meta["compose"] is True
    if side == "us":
        assert meta["regions"] == [{"label": "the contiguous United States", "continent": "North America"}]
        assert set(meta["sources"]) == {"us-test"} and "us-test" in meta["attribution"]
    else:
        assert meta["regions"] == [{"label": "Europe", "continent": "Europe"}]
        assert meta["sources"] == {} and "us-test" not in meta["attribution"]
    assert {key for key in meta["attribution"] if key != "us-test"} == {key for key in whole.meta["attribution"] if key != "us-test"}


def test_the_tile_index_points_at_every_tile_it_keeps(cuts: dict[str, ServingData]) -> None:
    tar_path = cuts["us"].directory / "valhalla" / "valhalla_tiles.tar"
    with tarfile.open(tar_path) as tar:
        tiles = [m for m in tar if m.name.endswith(".gph")]
        bodies = {m.name: tar.extractfile(m).read() for m in tiles}
    raw = tar_path.read_bytes()
    entries = _index(tar_path)

    assert len(entries) == len(tiles)
    for (offset, graph_id, size), member in zip(entries, tiles):
        level, index, _ = tile_box(member.name)
        assert graph_id == level | index << 3
        assert raw[offset : offset + size] == bodies[member.name]


def test_a_tile_is_named_by_its_box() -> None:
    level, index, box = tile_box(EU_TILE)

    assert EU_TILE == "2/000/778/380.gph" and (level, index) == (2, 778_380)  # row 540 of 720, column 780 of 1440
    assert (box.south, box.west, box.north, box.east) == (45.0, 15.0, 45.25, 15.25)
    assert tile_box("0/002/451.gph")[2].west == -96.0


def test_a_segment_crossing_the_split_is_refused(whole: ServingData, tmp_path: Path) -> None:
    source = tmp_path / "crossing"
    shutil.copytree(whole.directory, source)
    with sqlite3.connect(source / "trailgraph.sqlite") as db:
        db.execute("UPDATE segment_boxes SET west = -50, east = -30 WHERE id = (SELECT min(id) FROM segment_boxes)")

    with pytest.raises(CutError, match="1 Segments cross 40° W"):
        cut_serving(source, tmp_path / "out", load_keep("europe"), geonames_path=whole.geonames)


def test_a_cut_written_over_links_to_the_whole_export_leaves_it_as_it_was(whole: ServingData, tmp_path: Path) -> None:
    def digests(directory: Path) -> dict[str, str]:
        return {
            str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob("*"))
            if path.is_file()
        }

    before = digests(whole.directory)
    out = tmp_path / "linked"
    shutil.copytree(whole.directory, out, copy_function=os.link)  # cp -al

    cut_serving(whole.directory, out, load_keep("us"), geonames_path=whole.geonames)

    assert digests(whole.directory) == before
    assert ServingData(out).regions == ["us"]


def test_a_cut_file_keeps_the_hikes_starting_in_its_areas(whole: ServingData, tmp_path: Path) -> None:
    lat, lon = np.asarray(whole.trailheads[:, 0]), np.asarray(whole.trailheads[:, 1])
    west = lon < REGION_SPLIT_LON
    europe = np.flatnonzero(~west)
    one = int(europe[np.argmin(lat[europe])])  # the southernmost hike in Europe, by a box around it
    corners = [[lat[one] - 0.01, lon[one] - 0.01], [lat[one] - 0.01, lon[one] + 0.01], [lat[one] + 0.01, lon[one]]]
    (tmp_path / "show.toml").write_text(
        f"""
[[area]]
label = "Somewhere"
continent = "Europe"
polygon = {json.dumps(corners)}

[[area]]
label = "Moved"
continent = "North America"
countries = ["US"]
"""
    )
    cut = ServingData(cut_serving(whole.directory, tmp_path / "out", load_keep(tmp_path / "show.toml"), geonames_path=whole.geonames))

    assert cut.store.ids.tolist() == sorted([int(whole.store.ids[one]), *whole.store.ids[west].tolist()])
    assert cut.meta["regions"] == [
        {"label": "Somewhere", "continent": "Europe"},
        {"label": "Moved", "continent": "North America"},
    ]
    assert cut.meta["data_tag"].endswith("-show")
    assert cut.regions == ["europe", "us"]
    with tarfile.open(cut.directory / "valhalla" / "valhalla_tiles.tar") as tar:  # only the tiles near its hikes
        assert FAR_TILE not in {m.name for m in tar} and {EU_TILE, US_TILE} <= {m.name for m in tar}
    seed = int(whole.store.ids[one])
    assert [r["id"] for r in cut.rank(seed, WEIGHTS)] == [
        r["id"] for r in whole.rank(seed, WEIGHTS, allowed_rows=np.flatnonzero(np.isin(whole.store.ids, cut.store.ids)))
    ]


def test_an_area_no_hike_starts_in_is_refused(whole: ServingData, tmp_path: Path) -> None:
    (tmp_path / "empty.toml").write_text('[[area]]\nlabel = "Nowhere"\nbounds = { south = 1, west = 1, north = 2, east = 2 }\n')

    with pytest.raises(CutError, match="no hike starts in Nowhere"):
        cut_serving(whole.directory, tmp_path / "out", load_keep(tmp_path / "empty.toml"), geonames_path=whole.geonames)


def test_a_cut_is_not_written_over_the_export(whole: ServingData) -> None:
    with pytest.raises(CutError, match="not over it"):
        cut_serving(whole.directory, whole.directory, load_keep("us"), geonames_path=whole.geonames)


def test_an_export_with_place_search_needs_its_geonames_dump(whole: ServingData, tmp_path: Path) -> None:
    with pytest.raises(CutError, match="--geonames"):
        cut_serving(whole.directory, tmp_path / "out", load_keep("us"))


def test_a_group_of_twins_keeps_its_kept_members_led_by_the_first() -> None:
    twins = np.array([0, 0, 2, 0, 2, 5])  # rows 0, 1, 3 are Twins, and 2, 4

    assert kept_twins(twins, np.array([1, 2, 3, 4, 5])).tolist() == [0, 1, 0, 1, 4]
    assert kept_twins(twins, np.array([0, 1, 2, 3, 4, 5])).tolist() == twins.tolist()


def test_the_keep_is_a_side_a_packaged_cut_or_a_file(tmp_path: Path) -> None:
    assert load_keep("europe") == Keep(name="eu", side="europe")
    assert load_keep("us") == Keep(name="us", side="us")
    demo = load_keep("demo")
    assert demo.name == "demo"
    assert [area.label for area in demo.areas] == [
        "Slovenia", "Austria", "Switzerland", "California", "Colorado", "Utah", "Washington",
    ]  # fmt: skip
    region = load_keep(Path(__file__).parents[1] / "src" / "dayhike" / "data" / "regions" / "us-colorado.toml")
    assert [(area.label, area.continent, area.bounds.west) for area in region.areas] == [("Colorado", "North America", -109.1)]
    with pytest.raises(CutError, match="no cut 'asia'"):
        load_keep("asia")
    (tmp_path / "bad.toml").write_text('[[area]]\nlabel = "Everything"\n')
    with pytest.raises(CutError, match="no bounds, polygon or countries"):
        load_keep(tmp_path / "bad.toml")


def test_the_demo_cut_keeps_its_states_by_their_outlines() -> None:
    areas = {area.label: area for area in load_keep("demo").areas}
    import shapely

    def inside(label: str, lat: float, lon: float) -> bool:
        area = areas[label]
        return bool(shapely.contains_xy(shapely.Polygon([(x, y) for y, x in area.polygon]), lon, lat))

    assert inside("California", 36.58, -118.29)  # Mount Whitney
    assert not inside("California", 36.17, -115.14)  # Las Vegas, Nevada
    assert inside("Washington", 46.85, -121.76)  # Mount Rainier
    assert not inside("Washington", 45.37, -121.70)  # Mount Hood, Oregon
    assert inside("Utah", 40.76, -111.89) and not inside("Utah", 41.5, -110.0)  # Salt Lake City; Wyoming's corner
    assert inside("Colorado", 39.74, -104.99)  # Denver
    assert all(areas[name].countries == ("US",) for name in ("California", "Colorado", "Utah", "Washington"))
    assert [areas[name].countries for name in ("Slovenia", "Austria", "Switzerland")] == [("SI",), ("AT",), ("CH",)]
    assert CUTS_DIR.joinpath("demo.toml").is_file()


def test_cli_cuts_an_export(whole: ServingData, tmp_path: Path, capsys) -> None:
    out = tmp_path / "us"
    argv = ["cut-serving", "--from", str(whole.directory), "--keep", "us", "--out", str(out), "--geonames", str(whole.geonames)]

    assert main(argv) == 0
    assert ServingData(out).meta["dayhikes"] == len(_side_ids(whole, "us"))
    with pytest.raises(SystemExit):
        main(["cut-serving", "--from", str(whole.directory), "--keep", "asia", "--out", str(out)])
