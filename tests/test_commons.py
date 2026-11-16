"""Commons contributors: distinct Wikimedia Commons uploaders of geotagged
photos within 150 m of a DayHike's line, shown beside it and never scored (#170)."""

import gzip
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from dayhike import build_catalog, build_similarity, commons
from dayhike.catalog import geometry_store_path_for
from dayhike.commons import CommonsPoints, commons_points, contributor_counts, count_commons
from dayhike.dem import Bounds
from dayhike.serving import ServingData, export_serving

# One degree of latitude, in metres, on the 6,371 km sphere.
DEGREE_M = 111_194.93


def north(metres: float) -> float:
    return metres / DEGREE_M


def test_a_hike_counts_each_uploader_once_within_150_m_of_its_line() -> None:
    # An east-west line along 46° N from 14.00° E to 14.02° E (about 1.5 km), one segment.
    lat, lon = np.array([46.0, 46.0]), np.array([14.0, 14.02])
    east_end_m = DEGREE_M * np.cos(np.radians(46.0))  # metres per degree of longitude there
    points = CommonsPoints(
        lat=np.array([46.0 + north(100), 46.0 + north(40), 46.0 + north(200), 46.0, 46.0, 46.0 - north(149)]),
        lon=np.array([14.01, 14.012, 14.01, 14.02 + 140 / east_end_m, 14.02 + 160 / east_end_m, 14.005]),
        # Uploader 1 twice near the middle; 2 at 200 m; 3 past the east end by 140 m,
        # 4 by 160 m; 5 at 149 m south.
        uploader=np.array([1, 1, 2, 3, 4, 5]),
    )

    counts = contributor_counts({7: (lat, lon), 8: (lat + 1.0, lon)}, points)

    assert counts == {7: 3, 8: 0}


def write_dumps(directory: Path, geo_tags: list[str], pages: list[str], images: list[str]) -> Path:
    """Tiny Commons dumps in the layout of the real ones: one row per line after the INSERT."""
    directory.mkdir(parents=True, exist_ok=True)
    for table, rows in (("geo_tags", geo_tags), ("page", pages), ("image", images)):
        text = f"-- MySQL dump\nINSERT INTO `{table}` VALUES\n" + ",\n".join(rows) + ";\n"
        with gzip.open(directory / f"commonswiki-latest-{table}.sql.gz", "wt", encoding="utf-8") as out:
            out.write(text)
    return directory


def image(name: str, actor: int) -> str:
    # Real image rows carry an escaped JSON metadata blob, with commas and quotes, before the actor.
    metadata = '{\\"data\\":{\\"Make\\":\\"Canon\\",\\"Note\\":\\"a, b \\\\\' c\\"}}'
    return f"('{name}',1916010,1666,1111,'{metadata}',8,'BITMAP','image','jpeg',44,{actor},'20170930201259','c9aanc')"


def test_commons_points_are_the_primary_photo_spots_of_files_in_the_bounds(tmp_path: Path) -> None:
    dumps = write_dumps(
        tmp_path / "commons",
        geo_tags=[
            "(1,10,'earth',1,46.01234567,14.01234567,NULL,'camera',NULL,NULL,NULL,NULL,NULL)",
            "(2,11,'earth',1,46.02345678,14.02345678,NULL,NULL,NULL,NULL,NULL,NULL,NULL)",
            "(3,12,'earth',1,46.03456789,14.03456789,NULL,NULL,NULL,NULL,NULL,NULL,NULL)",
            # Not primary (an object's place, not the photo spot), on the moon, a round
            # coordinate, outside the bounds, and a gallery page in another namespace.
            "(4,13,'earth',0,46.04567891,14.04567891,NULL,NULL,NULL,NULL,NULL,NULL,NULL)",
            "(5,14,'moon',1,46.05678912,14.05678912,NULL,NULL,NULL,NULL,NULL,NULL,NULL)",
            "(6,15,'earth',1,46.10000000,14.06789123,NULL,NULL,NULL,NULL,NULL,NULL,NULL)",
            "(7,16,'earth',1,48.01234567,14.01234567,NULL,NULL,NULL,NULL,NULL,NULL,NULL)",
            "(8,17,'earth',1,46.07891234,14.07891234,NULL,NULL,NULL,NULL,NULL,NULL,NULL)",
        ],
        pages=[
            f"({page},{6 if page != 17 else 0},'{title}',0,0,0.1,'20261101000000','20261101000000',1,100,'wikitext',NULL)"
            for page, title in (
                (10, "Ridge_view.jpg"),
                (11, "O\\'Brien\\'s_hut,_north.jpg"),
                (12, "Lake.jpg"),
                (13, "Object.jpg"),
                (14, "Moon.jpg"),
                (15, "Round.jpg"),
                (16, "Far.jpg"),
                (17, "Gallery"),
            )
        ],
        images=[
            image("Lake.jpg", 501),
            image("Moon.jpg", 504),
            image("O\\'Brien\\'s_hut,_north.jpg", 500),
            image("Object.jpg", 503),
            image("Ridge_view.jpg", 500),
            image("Round.jpg", 505),
            image("Far.jpg", 506),
        ],
    )

    points = commons_points(dumps, Bounds(south=45.5, west=13.5, north=46.5, east=14.5))

    order = np.argsort(points.lat)
    assert points.lat[order] == pytest.approx([46.01234567, 46.02345678, 46.03456789])
    assert points.lon[order] == pytest.approx([14.01234567, 14.02345678, 14.03456789])
    # The two files of one uploader share a number, the other's differs.
    first, second, third = points.uploader[order]
    assert first == second != third


FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
RIDGE_WALK_ID = 1001


def geotag(gt_id: int, page: int, lat: float, lon: float) -> str:
    return f"({gt_id},{page},'earth',1,{lat:.8f},{lon:.8f},NULL,NULL,NULL,NULL,NULL,NULL,NULL)"


def file_page(page: int, title: str) -> str:
    return f"({page},6,'{title}',0,0,0.1,'20261101000000','20261101000000',1,100,'wikitext',NULL)"


@pytest.fixture(scope="module")
def exports(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    """The fixture Catalog exported before and after counting Commons contributors,
    with three uploaders photographing within 150 m of Ridge Walk and one 5 km off."""
    directory = tmp_path_factory.mktemp("commons")
    catalog = directory / "catalog.sqlite"
    build_catalog(FIXTURE, catalog)
    build_similarity(catalog, directory / "dem")
    before = export_serving(catalog, directory / "before")
    with sqlite3.connect(geometry_store_path_for(catalog)) as geo:
        lat, lon = geo.execute(
            "SELECT latitude, longitude FROM vertices WHERE dayhike_id = ? ORDER BY seq LIMIT 1", (RIDGE_WALK_ID,)
        ).fetchone()
    # Fixture coordinates are round, so every spot moves in both axes, else it reads as a place.
    spots = [(lat + 0.0003, lon + 0.00011), (lat - 0.0004, lon + 0.0001), (lat + 0.0001, lon - 0.0002), (lat + 0.045, lon + 0.0001)]
    dumps = write_dumps(
        directory / "dumps",
        geo_tags=[geotag(i, 100 + i, *spot) for i, spot in enumerate(spots)],
        pages=[file_page(100 + i, f"Photo_{i}.jpg") for i in range(len(spots))],
        images=[image(f"Photo_{i}.jpg", actor) for i, actor in enumerate((900, 901, 902, 903))],
    )

    count_commons(catalog, dumps)

    return before, export_serving(catalog, directory / "after"), catalog


def test_a_hike_shows_how_many_commons_contributors_photographed_near_it(exports: tuple[Path, Path, Path]) -> None:
    _, after, _ = exports

    assert ServingData(after).detail(RIDGE_WALK_ID)["commons_contributors"] == 3


def test_an_export_from_a_catalog_never_counted_shows_no_commons_count(exports: tuple[Path, Path, Path]) -> None:
    before, _, _ = exports

    assert ServingData(before).detail(RIDGE_WALK_ID)["commons_contributors"] is None


def test_commons_contributors_change_no_ranking(exports: tuple[Path, Path, Path]) -> None:
    before, after, _ = exports
    weights = {"length": 100, "hut_along": 40}

    ranked = [
        [(hike["id"], hike["score"]) for hike in ServingData(export).rank(RIDGE_WALK_ID, weights)]
        for export in (before, after)
    ]

    assert ranked[0] and ranked[1] == ranked[0]


def test_fetch_commons_downloads_the_three_dumps_it_lacks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fetched = []
    monkeypatch.setattr(commons, "download", lambda url, filename: fetched.append(url) or Path(filename).write_bytes(b"x"))
    (tmp_path / "commonswiki-latest-page.sql.gz").write_bytes(b"already here")

    commons.fetch_commons(tmp_path)

    assert fetched == [
        "https://dumps.wikimedia.org/commonswiki/latest/commonswiki-latest-geo_tags.sql.gz",
        "https://dumps.wikimedia.org/commonswiki/latest/commonswiki-latest-image.sql.gz",
    ]
