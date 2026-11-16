"""Commons photos for a hike.

Seams: ``kept_photo`` (which licences stay), ``photos_for_hike`` (at most two,
named POIs, one file once), ``CommonsClient`` (batched API, project User-Agent,
disk cache), ``export_photos`` plus ``ServingData.detail`` (the serving export
gains the photos; an export without them stays as it is).
"""

import json
import sqlite3
import zlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dayhike import build_catalog, build_similarity
from dayhike.catalog import geometry_store_path_for
from dayhike.photos import CommonsClient, export_photos, kept_photo, photo_from_page, photos_for_hike
from dayhike.serving import ServingData, export_serving

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
RIDGE_WALK_ID = 1001
VALLEY_PATH_ID = 1002
THUMB = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Triglav.jpg/480px-Triglav.jpg"
LICENCE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"

TRIGLAV = {
    "file": "Triglav.jpg",
    "author": "Ada Hiker",
    "licence": "CC BY-SA 4.0",
    "licence_url": LICENCE_URL,
    "thumb": THUMB,
}


def test_a_non_free_commons_licence_is_left_out_and_a_free_one_is_kept() -> None:
    kept = kept_photo(
        file="Triglav.jpg",
        author='<a href="https://commons.wikimedia.org/wiki/User:Ada">Ada Hiker</a>',
        licence="CC BY-SA 4.0",
        licence_url=LICENCE_URL,
        thumb=THUMB,
    )

    assert kept == TRIGLAV
    for licence in ("CC0", "CC0 1.0", "CC BY 2.0", "CC BY 4.0", "CC BY-SA 1.0", "CC BY-SA 3.0"):
        photo = kept_photo(file="Triglav.jpg", author="Ada Hiker", licence=licence, licence_url=LICENCE_URL, thumb=THUMB)
        assert photo is not None and photo["licence"] == licence
    for licence in ("CC BY-NC-SA 4.0", "CC BY-NC 4.0", "CC BY-ND 2.0", "GFDL", "Public domain"):
        assert kept_photo(
            file="Triglav.jpg", author="Ada Hiker", licence=licence, licence_url=LICENCE_URL, thumb=THUMB
        ) is None
    assert kept_photo(file="Triglav.jpg", author="Ada Hiker", licence="CC BY-SA 4.0", licence_url=LICENCE_URL, thumb="https://evil.example/t.jpg") is None
    assert kept_photo(file="Triglav.jpg", author="  ", licence="CC0", licence_url=LICENCE_URL, thumb=THUMB) is None


def test_a_hike_keeps_at_most_two_free_photos_from_its_named_pois() -> None:
    photo_a = {**TRIGLAV, "file": "Peak.jpg"}
    photo_b = {**TRIGLAV, "file": "Lake.jpg", "licence": "CC0"}
    photo_c = {**TRIGLAV, "file": "View.jpg"}
    library = {
        "File:Peak.jpg": photo_a,
        "File:Paid.jpg": None,
        "File:Hidden.jpg": {**TRIGLAV, "file": "Hidden.jpg"},
        "Q1": photo_b,
        "File:View.jpg": photo_c,
    }

    def resolve(poi: dict) -> dict | None:
        if not poi["name"]:
            raise AssertionError("an unnamed POI is not asked")
        if poi.get("wikimedia_commons"):
            return library[poi["wikimedia_commons"]]
        return library[poi["wikidata"]]

    pois = [
        {"name": "Peak", "wikimedia_commons": "File:Peak.jpg", "wikidata": ""},
        {"name": "Hut", "wikimedia_commons": "File:Paid.jpg", "wikidata": ""},
        {"name": "", "wikimedia_commons": "File:Hidden.jpg", "wikidata": ""},
        {"name": "Lake", "wikimedia_commons": "", "wikidata": "Q1"},
        {"name": "View", "wikimedia_commons": "File:View.jpg", "wikidata": ""},
    ]

    assert photos_for_hike(pois, resolve) == [photo_a, photo_b]


def test_the_same_file_is_kept_once() -> None:
    pois = [
        {"name": "North", "wikimedia_commons": "File:Triglav.jpg"},
        {"name": "South", "wikimedia_commons": "File:Triglav.jpg"},
    ]

    assert photos_for_hike(pois, lambda _poi: TRIGLAV) == [TRIGLAV]


def _page(title: str, licence: str, author: str = "Ada Hiker") -> dict:
    return {
        "title": title,
        "imageinfo": [
            {
                "thumburl": THUMB,
                "extmetadata": {
                    "LicenseShortName": {"value": licence},
                    "LicenseUrl": {"value": LICENCE_URL},
                    "Artist": {"value": f'<a href="https://commons.wikimedia.org/wiki/User:Ada">{author}</a>'},
                },
            }
        ],
    }


def _opener(pages: dict[str, dict]):
    calls: list = []

    def opener(request) -> bytes:
        calls.append(request)
        query = parse_qs(urlparse(request.full_url).query)
        if "titles" in query:
            titles = query["titles"][0].split("|")
            body = {"query": {"pages": {str(i): pages[title] for i, title in enumerate(titles)}}}
        elif "ids" in query:
            body = {"entities": {qid: pages[qid] for qid in query["ids"][0].split("|")}}
        else:
            title = query["cmtitle"][0]
            body = {"query": {"categorymembers": [{"title": name} for name in pages[title]]}}
        return json.dumps(body).encode()

    opener.calls = calls  # type: ignore[attr-defined]
    return opener


def test_imageinfo_drops_a_non_free_file_and_a_rebuild_does_not_ask_again(tmp_path: Path) -> None:
    pages = {
        "File:Triglav.jpg": _page("File:Triglav.jpg", "CC BY-SA 4.0"),
        "File:Hut.jpg": _page("File:Hut.jpg", "CC BY-NC-SA 4.0", "Bob"),
    }
    opener = _opener(pages)
    cache = tmp_path / "commons.sqlite"
    client = CommonsClient(cache, opener, pause_s=0)

    found = client.files(["File:Triglav.jpg", "File:Hut.jpg"])

    assert found["File:Triglav.jpg"] == TRIGLAV
    assert found["File:Hut.jpg"] is None
    agent = opener.calls[0].get_header("User-agent")
    assert agent == "HikesAlike (https://hikesalike.com)"
    assert "@" not in agent
    CommonsClient(cache, opener, pause_s=0).files(["File:Triglav.jpg", "File:Hut.jpg"])
    assert len(opener.calls) == 1


def test_a_second_uncached_batch_waits(tmp_path: Path, monkeypatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr("dayhike.photos.time.sleep", lambda seconds: slept.append(seconds))
    pages = {
        "File:One.jpg": _page("File:One.jpg", "CC0"),
        "File:Two.jpg": _page("File:Two.jpg", "CC BY 2.0"),
    }
    opener = _opener(pages)
    client = CommonsClient(tmp_path / "commons.sqlite", opener, pause_s=1)

    client.files(["File:One.jpg"])
    client.files(["File:Two.jpg"])

    assert slept and slept[0] > 0.5


def test_a_wikidata_image_and_a_category_skip_a_non_free_file(tmp_path: Path) -> None:
    pages = {
        "Q42": {"claims": {"P18": [{"mainsnak": {"datavalue": {"value": "Triglav.jpg"}}}]}},
        "File:Triglav.jpg": _page("File:Triglav.jpg", "CC0"),
        "Category:Ridge": ["File:Paid.jpg", "File:Triglav.jpg"],
        "File:Paid.jpg": _page("File:Paid.jpg", "CC BY-ND 4.0"),
    }
    opener = _opener(pages)
    client = CommonsClient(tmp_path / "commons.sqlite", opener, pause_s=0)

    from_wikidata = client.one({"name": "Triglav", "wikidata": "Q42"})
    from_category = client.one({"name": "Ridge", "wikimedia_commons": "Category:Ridge"})

    assert from_wikidata == {**TRIGLAV, "licence": "CC0"}
    assert from_category == {**TRIGLAV, "licence": "CC0"}


def test_a_thumbnail_from_the_thumb_host_is_stored_on_upload_wikimedia(tmp_path: Path) -> None:
    page = _page("File:Triglav.jpg", "CC BY-SA 4.0")
    page["imageinfo"][0]["thumburl"] = (
        "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/Triglav.jpg/480px-Triglav.jpg?utm_source=commons"
    )

    assert photo_from_page(page) == TRIGLAV


def test_a_free_wikidata_image_skips_the_commons_category(tmp_path: Path) -> None:
    page = _page("File:Triglav.jpg", "CC0")
    pages = {
        "Q42": {"claims": {"P18": [{"mainsnak": {"datavalue": {"value": "Triglav.jpg"}}}]}},
        "File:Triglav.jpg": page,
        "Category:Ridge": ["File:Other.jpg"],
        "File:Other.jpg": _page("File:Other.jpg", "CC BY 2.0", "Cara"),
    }
    opener = _opener(pages)
    client = CommonsClient(tmp_path / "commons.sqlite", opener, pause_s=0)

    client.warm([{"name": "Ridge", "wikimedia_commons": "Category:Ridge", "wikidata": "Q42"}])

    assert client.one({"name": "Ridge", "wikimedia_commons": "Category:Ridge", "wikidata": "Q42"}) == {
        **TRIGLAV, "licence": "CC0",
    }
    assert not any("cmtitle" in request.full_url for request in opener.calls)


def test_export_stores_the_free_photo_and_drops_the_non_free_one(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.sqlite"
    catalog.write_bytes(b"")
    geo = sqlite3.connect(geometry_store_path_for(catalog))
    geo.execute("CREATE TABLE amenities (osm_key TEXT, kind TEXT, latitude REAL, longitude REAL, tags TEXT)")
    geo.executemany(
        "INSERT INTO amenities VALUES (?, ?, ?, ?, ?)",
        [
            ("n1", "peak", 46.37834, 13.83662, json.dumps({"name": "Triglav", "wikimedia_commons": "File:Triglav.jpg"})),
            ("n2", "hut", 46.381, 13.84, json.dumps({"name": "Kredarica", "wikimedia_commons": "File:Hut.jpg"})),
            ("n3", "viewpoint", 46.39, 13.85, json.dumps({"name": "View", "wikimedia_commons": "File:View.jpg"})),
        ],
    )
    geo.commit()
    geo.close()
    out = tmp_path / "web"
    out.mkdir()
    db = sqlite3.connect(out / "hikes.sqlite")
    db.execute("CREATE TABLE hikes (id INTEGER PRIMARY KEY, points BLOB NOT NULL)")
    points = [
        ["peak", "line", round(46.37834, 5), round(13.83662, 5), 10, 1, "Triglav", 2864],
        ["hut", "line", round(46.381, 5), round(13.84, 5), 20, 1, "Kredarica", 2515],
        ["viewpoint", "line", round(46.39, 5), round(13.85, 5), 30, 1, "View", 2000],
    ]
    db.execute("INSERT INTO hikes VALUES (?, ?)", (7, zlib.compress(json.dumps(points).encode())))
    db.commit()
    db.close()
    pages = {
        "File:Triglav.jpg": _page("File:Triglav.jpg", "CC BY-SA 4.0"),
        "File:Hut.jpg": _page("File:Hut.jpg", "CC BY-NC 4.0", "Bob"),
        "File:View.jpg": _page("File:View.jpg", "CC BY 4.0", "Cara"),
        "File:Extra.jpg": _page("File:Extra.jpg", "CC0"),
    }
    opener = _opener(pages)

    count = export_photos(catalog, out, tmp_path / "cache.sqlite", opener=opener, pause_s=0)

    assert count == 1
    saved = sqlite3.connect(out / "hikes.sqlite")
    (raw,) = saved.execute("SELECT photos FROM hikes WHERE id = 7").fetchone()
    photos = json.loads(raw)
    assert photos == [TRIGLAV, {**TRIGLAV, "file": "View.jpg", "author": "Cara", "licence": "CC BY 4.0"}]
    assert all("Hut" not in photo["file"] for photo in photos)
    export_photos(catalog, out, tmp_path / "cache.sqlite", opener=opener, pause_s=0)
    assert len(opener.calls) == 1


def test_an_export_without_photo_data_has_none_and_export_photos_adds_the_free_one(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    build_similarity(path, tmp_path / "dem")
    out = export_serving(path, tmp_path / "web")

    assert "photos" not in ServingData(out).detail(RIDGE_WALK_ID)

    geo = sqlite3.connect(geometry_store_path_for(path))
    for key, text in geo.execute("SELECT osm_key, tags FROM amenities"):
        tags = json.loads(text)
        if tags.get("name") == "Ridge Peak":
            tags["wikimedia_commons"] = "File:Ridge Peak.jpg"
        elif tags.get("name") == "Ridge View":
            tags["wikimedia_commons"] = "File:Ridge View.jpg"
        else:
            continue
        geo.execute("UPDATE amenities SET tags = ? WHERE osm_key = ?", (json.dumps(tags), key))
    geo.commit()
    geo.close()
    pages = {
        "File:Ridge Peak.jpg": _page("File:Ridge Peak.jpg", "CC BY-SA 4.0"),
        "File:Ridge View.jpg": _page("File:Ridge View.jpg", "GFDL", "Bob"),
    }
    # The canned thumb is Triglav's; the file name comes from the page title.
    opener = _opener(pages)

    export_photos(path, out, tmp_path / "cache.sqlite", opener=opener, pause_s=0)
    detail = ServingData(out).detail(RIDGE_WALK_ID)

    assert detail["photos"] == [{**TRIGLAV, "file": "Ridge Peak.jpg"}]
    assert "photos" not in ServingData(out).detail(VALLEY_PATH_ID)
