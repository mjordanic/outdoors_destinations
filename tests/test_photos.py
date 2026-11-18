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
from urllib.parse import parse_qs, unquote_plus, urlparse

from dayhike import build_catalog, build_similarity
from dayhike.catalog import geometry_store_path_for
from dayhike.photos import MAX_URL, CommonsClient, export_photos, kept_photo, photo_from_page, photos_for_hike
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


def test_a_title_commons_normalizes_or_redirects_is_found_and_not_cached_missing(tmp_path: Path) -> None:
    calls: list = []

    def opener(request) -> bytes:
        calls.append(request)
        return json.dumps(
            {
                "query": {
                    "normalized": [{"from": "File:triglav_north_face.jpg", "to": "File:Triglav north face.jpg"}],
                    "redirects": [{"from": "File:Old Triglav.jpg", "to": "File:Triglav.jpg"}],
                    "pages": {
                        "1": _page("File:Triglav north face.jpg", "CC BY-SA 4.0"),
                        "2": _page("File:Triglav.jpg", "CC BY-SA 4.0"),
                    },
                }
            }
        ).encode()

    cache = tmp_path / "commons.sqlite"
    found = CommonsClient(cache, opener, pause_s=0).files(["File:triglav_north_face.jpg", "File:Old Triglav.jpg"])

    assert found["File:triglav_north_face.jpg"] == {**TRIGLAV, "file": "Triglav north face.jpg"}
    assert found["File:Old Triglav.jpg"] == TRIGLAV
    again = CommonsClient(cache, opener, pause_s=0).files(["File:triglav_north_face.jpg"])
    assert again["File:triglav_north_face.jpg"] == {**TRIGLAV, "file": "Triglav north face.jpg"}
    assert len(calls) == 1


def test_a_file_with_only_a_source_credit_and_no_author_is_dropped() -> None:
    page = _page("File:Triglav.jpg", "CC BY-SA 4.0")
    meta = page["imageinfo"][0]["extmetadata"]
    del meta["Artist"]
    meta["Credit"] = {"value": "Own work"}

    assert photo_from_page(page) is None


def _cyrillic(n: int, length: int = 60) -> list[str]:
    return [f"File:{'Ж' * length}{i}.jpg" for i in range(n)]


def test_a_batch_of_long_non_ascii_titles_is_split_so_no_url_is_too_long(tmp_path: Path) -> None:
    titles = _cyrillic(50)
    opener = _opener({title: _page(title, "CC0") for title in titles})
    client = CommonsClient(tmp_path / "commons.sqlite", opener, pause_s=0)

    found = client.files(titles)

    assert len(opener.calls) > 1
    assert all(len(request.full_url) <= MAX_URL for request in opener.calls)
    assert all(found[title] is not None for title in titles)
    asked = [t for request in opener.calls for t in parse_qs(urlparse(request.full_url).query)["titles"][0].split("|")]
    assert asked == titles


def test_a_single_title_longer_than_the_cap_is_sent_alone(tmp_path: Path) -> None:
    long = _cyrillic(1, length=MAX_URL)[0]
    short = "File:Short.jpg"
    opener = _opener({long: _page(long, "CC0"), short: _page(short, "CC0")})
    client = CommonsClient(tmp_path / "commons.sqlite", opener, pause_s=0)

    client.files([short, long])

    sent = [parse_qs(urlparse(request.full_url).query)["titles"][0].split("|") for request in opener.calls]
    assert [long] in sent


def test_warm_reports_progress_while_the_cache_fills(tmp_path: Path) -> None:
    pages = {
        "Q1": {"claims": {"P18": [{"mainsnak": {"datavalue": {"value": "One.jpg"}}}]}},
        "File:One.jpg": _page("File:One.jpg", "CC BY 2.0", "Ann"),
        "File:Direct.jpg": _page("File:Direct.jpg", "CC0"),
        "Category:Ridge": ["File:Cat.jpg"],
        "File:Cat.jpg": _page("File:Cat.jpg", "CC0"),
    }
    lines: list[str] = []
    client = CommonsClient(tmp_path / "commons.sqlite", _opener(pages), pause_s=0, progress=lines.append)

    client.warm(
        [
            {"name": "A", "wikidata": "Q1"},
            {"name": "B", "wikimedia_commons": "File:Direct.jpg"},
            {"name": "C", "wikimedia_commons": "Category:Ridge"},
        ]
    )

    assert "photos: entities 1/1" in lines
    assert "photos: files 2/2" in lines
    assert "photos: categories 1/1" in lines
    assert "photos: category files 1/1" in lines


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


def test_the_export_photos_command_succeeds_after_adding_photos(tmp_path: Path, monkeypatch, capsys) -> None:
    from dayhike.cli import main

    monkeypatch.setattr("dayhike.photos.export_photos", lambda catalog, out, cache, progress: 3)

    code = main(["export-photos", "--catalog", str(tmp_path / "c.sqlite"), "--out", str(tmp_path / "out")])

    assert code == 0
    assert "3 DayHikes with a photo" in capsys.readouterr().out


def _fixture_export(tmp_path: Path, extra_far_poi: bool = True) -> tuple[Path, Path, dict[str, dict]]:
    """A built fixture Catalog and its serving export, two on-hike POIs and one far from every hike tagged."""
    path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    build_similarity(path, tmp_path / "dem")
    out = export_serving(path, tmp_path / "web")
    geo = sqlite3.connect(geometry_store_path_for(path))
    for key, text in geo.execute("SELECT osm_key, tags FROM amenities").fetchall():
        tags = json.loads(text)
        if tags.get("name") == "Ridge Peak":
            tags["wikimedia_commons"] = "File:Ridge Peak.jpg"
        elif tags.get("name") == "Ridge View":
            tags["wikidata"] = "Q42"
        else:
            continue
        geo.execute("UPDATE amenities SET tags = ? WHERE osm_key = ?", (json.dumps(tags), key))
    pages = {
        "File:Ridge Peak.jpg": _page("File:Ridge Peak.jpg", "CC BY-SA 4.0"),
        "Q42": {"claims": {"P18": [{"mainsnak": {"datavalue": {"value": "Ridge View.jpg"}}}]}},
        "File:Ridge View.jpg": _page("File:Ridge View.jpg", "CC0"),
    }
    if extra_far_poi:
        geo.execute(
            "INSERT INTO amenities (osm_key, kind, latitude, longitude, tags) VALUES (?, ?, ?, ?, ?)",
            ("n999", "peak", 10.0, 10.0, json.dumps({"name": "Far Peak", "wikimedia_commons": "File:Far Peak.jpg"})),
        )
        pages["File:Far Peak.jpg"] = _page("File:Far Peak.jpg", "CC0")
    geo.commit()
    geo.close()
    return path, out, pages


def _asked(opener) -> str:
    return " ".join(unquote_plus(request.full_url) for request in opener.calls)


def _no_network(request) -> bytes:
    raise AssertionError(f"network used: {request.full_url}")


def _photos_column(out: Path) -> list[tuple]:
    db = sqlite3.connect(out / "hikes.sqlite")
    try:
        return db.execute("SELECT id, photos FROM hikes ORDER BY id").fetchall()
    finally:
        db.close()


def test_warm_only_asks_for_pois_on_a_hike_and_the_export_then_needs_no_network(tmp_path: Path) -> None:
    from dayhike.photos import warm_photos

    path, out, pages = _fixture_export(tmp_path)
    cache = tmp_path / "cache.sqlite"
    opener = _opener(pages)

    warmed = warm_photos(path, cache, opener=opener, pause_s=0)

    assert warmed == 2
    assert "Ridge Peak.jpg" in _asked(opener)
    assert "Far" not in _asked(opener)
    export_photos(path, out, cache, opener=_no_network, pause_s=0)
    assert any(photos for _id, photos in _photos_column(out))


def test_export_after_warm_only_equals_a_full_warm_on_the_same_cache(tmp_path: Path) -> None:
    from dayhike.photos import _poi_index, warm_photos

    path, out, pages = _fixture_export(tmp_path)
    full_cache = tmp_path / "full.sqlite"
    geo = sqlite3.connect(geometry_store_path_for(path))
    CommonsClient(full_cache, _opener(pages), pause_s=0).warm(_poi_index(geo).values())  # the old: warm every POI
    geo.close()
    export_photos(path, out, full_cache, opener=_no_network, pause_s=0)
    full = _photos_column(out)
    assert any(photos for _id, photos in full)

    warm_photos(path, tmp_path / "new.sqlite", opener=_opener(pages), pause_s=0)
    export_photos(path, out, tmp_path / "new.sqlite", opener=_no_network, pause_s=0)

    assert _photos_column(out) == full


def test_export_photos_warms_only_the_pois_its_hikes_show(tmp_path: Path) -> None:
    path, out, pages = _fixture_export(tmp_path)
    opener = _opener(pages)

    export_photos(path, out, tmp_path / "cache.sqlite", opener=opener, pause_s=0)

    asked = _asked(opener)
    assert "Ridge Peak.jpg" in asked
    assert "Far Peak" not in asked


def test_warm_only_reports_progress(tmp_path: Path) -> None:
    from dayhike.photos import warm_photos

    path, _out, pages = _fixture_export(tmp_path)
    lines: list[str] = []

    warm_photos(path, tmp_path / "cache.sqlite", opener=_opener(pages), pause_s=0, progress=lines.append)

    assert any(line.startswith("photos: warming 2 of 3 POIs") for line in lines)
    assert "photos: files 2/2" in lines


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_the_pause_counts_from_request_start_so_the_period_is_the_longer_of_pause_and_latency(
    tmp_path: Path, monkeypatch
) -> None:
    clock = _Clock()
    monkeypatch.setattr("dayhike.photos.time", clock)
    starts: list[float] = []
    pages = {f"File:{n}.jpg": _page(f"File:{n}.jpg", "CC0") for n in range(4)}
    inner = _opener(pages)

    def slow(request) -> bytes:
        starts.append(clock.now)
        clock.now += 1.5  # latency
        return inner(request)

    client = CommonsClient(tmp_path / "cache.sqlite", slow, pause_s=1.0)
    for n in range(4):
        client.files([f"File:{n}.jpg"])

    assert [round(b - a, 3) for a, b in zip(starts, starts[1:])] == [1.5, 1.5, 1.5]  # was 2.5: latency + pause
    fast_starts: list[float] = []

    def quick(request) -> bytes:
        fast_starts.append(clock.now)
        clock.now += 0.2
        return inner(request)

    client = CommonsClient(tmp_path / "cache2.sqlite", quick, pause_s=1.0)
    for n in range(3):
        client.files([f"File:{n}.jpg"])

    assert [round(b - a, 3) for a, b in zip(fast_starts, fast_starts[1:])] == [1.0, 1.0]


def test_one_cache_connection_serves_every_read_and_write(tmp_path: Path, monkeypatch) -> None:
    opened: list[object] = []
    real = sqlite3.connect

    def counting(*args, **kwargs):
        opened.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr("dayhike.photos.sqlite3.connect", counting)
    pages = {f"File:{n}.jpg": _page(f"File:{n}.jpg", "CC0") for n in range(3)}
    client = CommonsClient(tmp_path / "cache.sqlite", _opener(pages), pause_s=0)

    client.warm([{"wikimedia_commons": f"File:{n}.jpg"} for n in range(3)])
    client.one({"wikimedia_commons": "File:0.jpg"})
    client.close()

    assert len(opened) == 1
    again = CommonsClient(tmp_path / "cache.sqlite", _no_network, pause_s=0)  # the batch was committed
    assert again.one({"wikimedia_commons": "File:2.jpg"}) is not None


def test_the_warm_only_command_runs_without_a_serving_export(tmp_path: Path, monkeypatch, capsys) -> None:
    from dayhike.cli import main

    seen: dict = {}

    def fake(catalog, cache, progress):
        seen.update(catalog=catalog, cache=cache)
        return 5

    monkeypatch.setattr("dayhike.photos.warm_photos", fake)

    code = main(["export-photos", "--warm-only", "--catalog", str(tmp_path / "c.sqlite"), "--cache", str(tmp_path / "k.sqlite")])

    assert code == 0
    assert seen == {"catalog": tmp_path / "c.sqlite", "cache": tmp_path / "k.sqlite"}
    assert "5 POIs warmed" in capsys.readouterr().out


def test_warm_only_never_holds_the_geometry_store_open_while_it_computes_or_asks(tmp_path: Path, monkeypatch) -> None:
    """sample-dem and sample-landcover switch the store's journal mode beside warm-only, which needs it unlocked."""
    from dayhike import serving
    from dayhike.photos import warm_photos

    path, _out, pages = _fixture_export(tmp_path)
    geo_file = str(geometry_store_path_for(path))
    open_now: list[sqlite3.Connection] = []
    opened = 0

    class Tracked(sqlite3.Connection):
        def close(self) -> None:
            if self in open_now:
                open_now.remove(self)
            super().close()

    real = sqlite3.connect

    def tracking(database, *args, **kwargs):
        nonlocal opened
        connection = real(database, *args, factory=Tracked, **kwargs)
        if geo_file in str(database):
            opened += 1
            open_now.append(connection)
        return connection

    monkeypatch.setattr("dayhike.photos.sqlite3.connect", tracking)
    monkeypatch.setattr(serving, "_HIKE_CHUNK", 1)  # several chunks, so a connection kept between them shows
    held_while_computing: list[int] = []
    real_points = serving._line_points

    def watching(*args, **kwargs):
        held_while_computing.append(len(open_now))
        return real_points(*args, **kwargs)

    monkeypatch.setattr(serving, "_line_points", watching)
    inner = _opener(pages)
    held_while_asking: list[int] = []

    def opener(request) -> bytes:
        held_while_asking.append(len(open_now))
        return inner(request)

    warm_photos(path, tmp_path / "cache.sqlite", opener=opener, pause_s=0)

    assert held_while_computing and set(held_while_computing) == {0}
    assert held_while_asking and set(held_while_asking) == {0}
    assert opened > 2  # one short read per chunk, not one connection across the whole pass
