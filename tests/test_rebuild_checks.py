"""The clean-rebuild comparison helpers detect what they must, on tiny inputs."""

import json
import os
import sqlite3
from pathlib import Path

import numpy as np
from rebuild_checks import (
    CountryCoverage,
    compare_tables,
    coverage_by_country,
    europe_countries,
    manifest_diff,
    raw_manifest,
)


def _table(path: Path, rows: list[tuple]) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, length_m REAL)")
    connection.executemany("INSERT INTO t VALUES (?, ?, ?)", rows)
    connection.commit()
    return connection


def test_compare_tables_reports_a_changed_value(tmp_path: Path) -> None:
    old = _table(tmp_path / "old.sqlite", [(1, "a", 1000.0), (2, "b", 2000.0), (3, "c", None)])
    new = _table(tmp_path / "new.sqlite", [(1, "a", 1000.0), (2, "b", 2000.5), (3, "c", 0.0)])

    diff = compare_tables(old, new, "t", ["id"], ["name", "length_m"])

    assert not diff.equal
    assert diff.changed == [((2,), ("b", 2000.0), ("b", 2000.5)), ((3,), ("c", None), ("c", 0.0))]
    assert compare_tables(old, new, "t", ["id"], ["name", "length_m"], abs_tol=1.0).changed == [
        ((3,), ("c", None), ("c", 0.0))
    ]
    assert "changed (2,)" in diff.report()


def test_compare_tables_reports_left_and_joined_ids(tmp_path: Path) -> None:
    old = _table(tmp_path / "old.sqlite", [(1, "a", 1.0), (2, "b", 2.0), (4, "d", 4.0)])
    new = _table(tmp_path / "new.sqlite", [(2, "b", 2.0), (3, "c", 3.0), (4, "d", 4.0), (5, "e", 5.0)])

    diff = compare_tables(old, new, "t", ["id"], ["name", "length_m"])

    assert diff.left == [(1,)]
    assert diff.joined == [(3,), (5,)]
    assert diff.changed == []
    assert compare_tables(old, old, "t", ["id"], ["name", "length_m"]).equal


def _square(west: float, south: float, size: float) -> dict:
    ring = [[west, south], [west + size, south], [west + size, south + size], [west, south + size], [west, south]]
    return {"type": "Polygon", "coordinates": [ring]}


def test_coverage_by_country_assigns_points_to_polygons(tmp_path: Path) -> None:
    index = tmp_path / "index-v1.json"
    index.write_text(
        json.dumps(
            {
                "features": [
                    {"properties": {"id": "aland", "parent": "europe"}, "geometry": _square(0, 0, 2)},
                    {"properties": {"id": "bland", "parent": "europe"}, "geometry": _square(2, 0, 2)},
                    {"properties": {"id": "alps", "parent": "europe"}, "geometry": _square(0, 0, 4)},
                    {"properties": {"id": "bland-north", "parent": "bland"}, "geometry": _square(2, 1, 1)},
                ]
            }
        )
    )
    # latitude, longitude, has LandCover, has climate, has elevation
    rows = np.array(
        [
            [1.0, 1.0, 1, 1, 1],
            [1.5, 0.5, 0, 1, 1],
            [1.0, 3.0, 1, 0, 1],
            [9.0, 9.0, 1, 1, 1],
        ]
    )

    countries = europe_countries(index)
    coverage, unassigned = coverage_by_country(countries, rows)

    assert [name for name, _ in countries] == ["aland", "bland"]
    assert coverage == {
        "aland": CountryCoverage(hikes=2, landcover=1, climate=2, elevation=2),
        "bland": CountryCoverage(hikes=1, landcover=1, climate=0, elevation=1),
    }
    assert unassigned == 1


def test_raw_manifest_diff_names_a_missing_and_a_changed_file(tmp_path: Path) -> None:
    for name in ("a.tif", "b.tif", "c.pbf"):
        (tmp_path / name).write_bytes(name.encode())
    expected = raw_manifest(tmp_path, ["a.tif", "b.tif", "c.pbf"], hashed=["c.pbf"])
    stat = (tmp_path / "c.pbf").stat()

    (tmp_path / "a.tif").unlink()
    (tmp_path / "b.tif").write_bytes(b"longer b.tif")
    (tmp_path / "c.pbf").write_bytes(b"C.PBF")
    os.utime(tmp_path / "c.pbf", ns=(stat.st_atime_ns, stat.st_mtime_ns))
    actual = raw_manifest(tmp_path, ["b.tif", "c.pbf"], hashed=["c.pbf"])

    problems = manifest_diff(expected, actual)

    assert problems[0] == "missing a.tif"
    assert problems[1].startswith("changed b.tif: size")
    assert problems[2].startswith("changed c.pbf: sha256")
    assert manifest_diff(expected, expected) == []
