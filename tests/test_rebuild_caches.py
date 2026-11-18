"""Caches that carry work across rebuilds of one snapshot: the ingest passes' work-file
tables (``pass_cache``) and the parsed Commons points (``cache_dir``). A cache is used
only when its key matches, and the Catalog is the same with it, without it, and after it."""

import gzip
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from dayhike import build_catalog, commons, geometry_store_path_for, ingest
from dayhike.catalog import trailgraph_path_for
from dayhike.commons import commons_points, count_commons
from dayhike.dem import Bounds
from test_commons import file_page, geotag, image, write_dumps

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"


def _dump(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def _files(catalog_path: Path) -> list[list[str]]:
    return [_dump(path) for path in (catalog_path, geometry_store_path_for(catalog_path), trailgraph_path_for(catalog_path))]


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "snapshot.osm"
    shutil.copyfile(FIXTURE, path)
    return path


@pytest.fixture
def passes_read(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """The files the ingest passes read from the snapshot, in order."""
    read: list[Path] = []
    real = ingest._iter_file

    def recorded(path, entities):
        read.append(path)
        return real(path, entities)

    monkeypatch.setattr(ingest, "_iter_file", recorded)
    return read


def test_a_second_build_of_one_snapshot_skips_the_passes_and_builds_the_same_catalog(
    tmp_path: Path, source: Path, passes_read: list[Path]
) -> None:
    cache = tmp_path / "pass-cache"
    build_catalog(source, tmp_path / "plain" / "catalog.sqlite")
    assert len(passes_read) == 3  # relations, ways, nodes
    passes_read.clear()
    build_catalog(source, tmp_path / "first" / "catalog.sqlite", pass_cache=cache)
    assert len(passes_read) == 3
    assert len(list(cache.iterdir())) == 1
    passes_read.clear()
    messages: list[str] = []

    build_catalog(source, tmp_path / "second" / "catalog.sqlite", pass_cache=cache, progress=messages.append)

    assert passes_read == []
    assert any(message.startswith("passes: cached") for message in messages), messages
    assert _files(tmp_path / "second" / "catalog.sqlite") == _files(tmp_path / "plain" / "catalog.sqlite")
    assert len(list(cache.iterdir())) == 1  # the cache is not rewritten under another name


def test_the_pass_cache_is_left_alone_by_the_work_file_the_build_deletes(tmp_path: Path, source: Path) -> None:
    cache = tmp_path / "pass-cache"
    build_catalog(source, tmp_path / "a" / "catalog.sqlite", pass_cache=cache)
    entry = next(cache.iterdir())
    before = entry.stat().st_mtime_ns

    build_catalog(source, tmp_path / "b" / "catalog.sqlite", pass_cache=cache)

    assert entry.stat().st_mtime_ns == before
    assert not (tmp_path / "b" / "catalog.sqlite.work").exists()


def test_a_resumed_build_does_not_overwrite_the_pass_cache(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "pass-cache"
    catalog = tmp_path / "a" / "catalog.sqlite"

    def stopped(*args, **kwargs):
        raise RuntimeError("stopped after the passes")

    with monkeypatch.context() as patch:
        patch.setattr(ingest, "_build_hikes", stopped)
        with pytest.raises(RuntimeError):
            build_catalog(source, catalog, pass_cache=cache)
    entry = next(cache.iterdir())
    before = entry.stat().st_mtime_ns

    build_catalog(source, catalog, pass_cache=cache)

    assert entry.stat().st_mtime_ns == before
    assert _files(catalog) == _files(_plain(tmp_path, source))


def test_a_build_resumed_during_the_passes_saves_the_pass_cache(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch, passes_read: list[Path]
) -> None:
    cache = tmp_path / "pass-cache"
    catalog = tmp_path / "a" / "catalog.sqlite"

    def stopped(*args, **kwargs):
        raise RuntimeError("stopped in the nodes pass")

    with monkeypatch.context() as patch:
        patch.setattr(ingest, "_pass_nodes", stopped)
        with pytest.raises(RuntimeError):
            build_catalog(source, catalog, pass_cache=cache)
    assert not cache.exists() or not any(cache.iterdir())

    build_catalog(source, catalog, pass_cache=cache)
    assert len(list(cache.iterdir())) == 1
    passes_read.clear()

    build_catalog(source, tmp_path / "b" / "catalog.sqlite", pass_cache=cache)

    assert passes_read == []
    assert _files(tmp_path / "b" / "catalog.sqlite") == _files(_plain(tmp_path, source))


def _plain(tmp_path: Path, source: Path) -> Path:
    catalog = tmp_path / "plain" / "catalog.sqlite"
    build_catalog(source, catalog)
    return catalog


@pytest.mark.parametrize("change", ["mtime", "size", "version", "schema", "paths", "fresh"])
def test_a_pass_cache_that_does_not_match_is_not_used(
    tmp_path: Path, source: Path, passes_read: list[Path], monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    cache = tmp_path / "pass-cache"
    build_catalog(source, tmp_path / "first" / "catalog.sqlite", pass_cache=cache)
    passes_read.clear()
    fresh, paths = False, "graded"
    if change == "mtime":
        stat = source.stat()
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    elif change == "size":
        source.write_text(source.read_text() + "\n")
    elif change == "version":
        monkeypatch.setattr(ingest, "PASS_CACHE_VERSION", ingest.PASS_CACHE_VERSION + 1)
    elif change == "schema":
        monkeypatch.setattr(ingest, "INGEST_WORK_SCHEMA", ingest.INGEST_WORK_SCHEMA + 1)
    elif change == "paths":
        paths = "trails"
    else:
        fresh = True

    build_catalog(source, tmp_path / "second" / "catalog.sqlite", pass_cache=cache, fresh=fresh, paths=paths)

    assert len(passes_read) == 3


def test_a_cache_the_build_cannot_open_is_rebuilt(tmp_path: Path, source: Path, passes_read: list[Path]) -> None:
    cache = tmp_path / "pass-cache"
    build_catalog(source, tmp_path / "first" / "catalog.sqlite", pass_cache=cache)
    next(cache.iterdir()).write_bytes(b"not a database")
    passes_read.clear()

    catalog = build_catalog(source, tmp_path / "second" / "catalog.sqlite", pass_cache=cache)

    assert len(passes_read) == 3
    assert catalog.ingest_report.kept == 4


def test_the_cli_takes_a_pass_cache(tmp_path: Path, source: Path, passes_read: list[Path]) -> None:
    from dayhike.cli import main

    cache = tmp_path / "pass-cache"
    for name in ("a", "b"):
        main(["ingest", str(source), "--catalog", str(tmp_path / name / "catalog.sqlite"), "--pass-cache", str(cache)])

    assert len(passes_read) == 3
    assert len(list(cache.iterdir())) == 1


# -- the parsed Commons points


BOUNDS = Bounds(south=45.5, west=13.5, north=46.5, east=14.5)


@pytest.fixture
def dumps(tmp_path: Path) -> Path:
    return write_dumps(
        tmp_path / "commons",
        geo_tags=[geotag(i, 100 + i, 46.01234567 + i / 1000, 14.01234567) for i in range(3)],
        pages=[file_page(100 + i, f"Photo_{i}.jpg") for i in range(3)],
        images=[image(f"Photo_{i}.jpg", 900 + i % 2) for i in range(3)],
    )


@pytest.fixture
def parsed(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """The dumps ``commons_points`` unzipped, in order."""
    read: list[Path] = []
    real = commons._rows

    def recorded(path):
        read.append(path)
        return real(path)

    monkeypatch.setattr(commons, "_rows", recorded)
    return read


def _same(a, b) -> bool:
    return all(x.dtype == y.dtype and x.tobytes() == y.tobytes() for x, y in ((a.lat, b.lat), (a.lon, b.lon), (a.uploader, b.uploader)))


def test_the_second_read_of_the_dumps_for_the_same_bounds_is_the_cached_points(
    tmp_path: Path, dumps: Path, parsed: list[Path]
) -> None:
    cache = tmp_path / "cache"
    plain = commons_points(dumps, BOUNDS)
    parsed.clear()
    first = commons_points(dumps, BOUNDS, cache_dir=cache)
    assert len(parsed) == 3
    parsed.clear()
    messages: list[str] = []

    second = commons_points(dumps, BOUNDS, progress=messages.append, cache_dir=cache)

    assert parsed == []
    assert any("cached" in message for message in messages), messages
    assert _same(first, plain) and _same(second, plain)
    assert len(plain.lat) == 3


@pytest.mark.parametrize("change", ["bounds", "mtime", "size", "version"])
def test_cached_commons_points_are_not_used_when_their_key_changes(
    tmp_path: Path, dumps: Path, parsed: list[Path], monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    cache = tmp_path / "cache"
    commons_points(dumps, BOUNDS, cache_dir=cache)
    parsed.clear()
    bounds = BOUNDS
    dump = commons.dump_path(dumps, "page")
    if change == "bounds":
        bounds = Bounds(south=45.5, west=13.5, north=46.5, east=14.6)
    elif change == "mtime":
        stat = dump.stat()
        os.utime(dump, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    elif change == "size":
        text = gzip.decompress(dump.read_bytes()).decode()
        dump.write_bytes(gzip.compress((text + file_page(999, "Other.jpg") + ",\n").encode()))  # more bytes, the same points
    else:
        monkeypatch.setattr(commons, "POINTS_CACHE_VERSION", commons.POINTS_CACHE_VERSION + 1)

    commons_points(dumps, bounds, cache_dir=cache)

    assert len(parsed) == 3


def test_count_commons_writes_the_same_counts_from_cached_points(tmp_path: Path, dumps: Path, source: Path) -> None:
    catalogs = {}
    for name in ("plain", "first", "second"):
        path = tmp_path / name / "catalog.sqlite"
        build_catalog(source, path)
        catalogs[name] = path
    cache = tmp_path / "cache"
    geo = sqlite3.connect(geometry_store_path_for(catalogs["plain"]))
    lat, lon = geo.execute("SELECT latitude, longitude FROM vertices ORDER BY dayhike_id, seq LIMIT 1").fetchone()
    geo.close()
    spots = write_dumps(
        tmp_path / "spots",
        geo_tags=[geotag(7, 700, lat + 0.0003, lon + 0.00011)],
        pages=[file_page(700, "Near.jpg")],
        images=[image("Near.jpg", 950)],
    )

    counted = count_commons(catalogs["plain"], spots)
    assert count_commons(catalogs["first"], spots, cache_dir=cache) == counted
    assert count_commons(catalogs["second"], spots, cache_dir=cache) == counted

    assert _dump(catalogs["first"]) == _dump(catalogs["plain"]) == _dump(catalogs["second"])
    with sqlite3.connect(catalogs["plain"]) as db:
        assert db.execute("SELECT max(commons_contributors) FROM dayhikes").fetchone() == (1,)
