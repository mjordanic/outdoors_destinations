import json
import shutil
import sqlite3
import urllib.error
from pathlib import Path

import numpy as np
import pytest

from dayhike.bases import export_bases
from dayhike.catalog import similarity_dir_for
from dayhike.cli import main
from test_bases import KINDS, PER_KIND, _columns, _store
from test_highways import _locate, _serve, _stop

# The fixture export: three kinds of hiking, ten hikes each and alike within a kind, so
# each kind is one HikeType. Hikes 0-24 start around Keswick at (0, 0) on a 2.2 km grid,
# each in its own 1 km cell; hikes 25-29 start around Remote, 333 km east.
# Hike 1 is a Twin of hike 0 and starts nearer Keswick; hike 2 is hike 0's kind and starts in its cell.
KESWICK, BRAITHWAITE, HAMLET, REMOTE = 1, 2, 3, 4
PLACES = (
    (KESWICK, "Keswick", 4_658, 0.0, 0.0),
    (BRAITHWAITE, "Braithwaite", 500, -0.05, 0.0),
    (HAMLET, "Hamlet", 120, 0.01, 0.01),  # under 300 people
    (REMOTE, "Remote", 5_000, 0.0, 3.0),  # only five HikeGroups in Reach
)


def _trailheads() -> np.ndarray:
    points = [(0.02 * (i % 5) - 0.04, 0.02 * (i // 5) + 0.02) for i in range(25)]
    points[0] = (0.0, 0.11)  # 12.2 km east: 26.2 minutes at 28 km/h
    points[1] = (0.0, 0.03)  # 3.3 km east: 7.1 minutes
    points[2] = (0.001, 0.111)  # in hike 0's 1 km cell
    points += [(0.01 * k, 3.0 + 0.02 * k) for k in range(5)]
    return np.array(points, dtype=np.float64)


def _export(tmp_path: Path) -> Path:
    out = tmp_path / "web-serving"
    out.mkdir(parents=True)
    columns = {name: np.repeat(values[::PER_KIND], PER_KIND) for name, values in _columns().items()}
    _store(tmp_path / "store", columns)
    shutil.copytree(similarity_dir_for(tmp_path / "store" / "catalog.sqlite"), out / "similarity")
    twins = np.arange(len(KINDS) * PER_KIND)
    twins[1] = 0
    np.save(out / "similarity" / "twins.npy", twins)
    np.save(out / "trailheads.npy", _trailheads())
    db = sqlite3.connect(out / "places.sqlite")
    db.execute("CREATE TABLE places (id INTEGER PRIMARY KEY, name TEXT, country TEXT, population INTEGER, lat REAL, lon REAL)")
    db.executemany("INSERT INTO places VALUES (?, ?, 'GB', ?, ?, ?)", PLACES)
    db.commit()
    db.close()
    return out


def _valhalla(fail_after: int | None = None):
    """Fake Valhalla: every place snaps 11 km west, so the highway estimate is over the hour
    and every minute is the straight line's; with ``fail_after``, isochrones fail after that many."""
    calls: list[tuple[float, float]] = []

    def isochrone(body):
        location = body["locations"][0]
        calls.append((location["lat"], location["lon"]))
        if fail_after is not None and len(calls) > fail_after:
            return 500, {"error": "stopped"}
        ring = [[location["lon"] - 0.01, location["lat"] - 0.01], [location["lon"] + 0.01, location["lat"] - 0.01],
                [location["lon"] + 0.01, location["lat"] + 0.01], [location["lon"] - 0.01, location["lat"] - 0.01]]  # fmt: skip
        features = [
            {"properties": {"contour": c["time"]}, "geometry": {"type": "Polygon", "coordinates": [ring]}}
            for c in body["contours"]
        ]
        return 200, {"features": features}

    server, url = _serve({"/locate": _locate(lambda lat, lon, cutoff: (lat, lon - 0.1)), "/isochrone": isochrone})
    return server, url, calls


def _build(out: Path, **options) -> None:
    server, url, _ = _valhalla()
    try:
        export_bases(out, url, **options)
    finally:
        _stop(server)


def _load(out: Path) -> dict[str, np.ndarray]:
    return {path.stem: np.load(path) for path in (out / "bases").glob("*.npy")}


def _in_reach(bases: dict[str, np.ndarray], base: int, reach: int) -> dict[int, int]:
    """HikeGroup -> minutes of one Base within ``reach`` minutes, read through the run index."""
    found = {}
    count = len(bases["place_ids"])
    for band, upper in enumerate((15, 30, 60)):
        if upper > reach:
            break
        slot = band * count + base
        for run in range(bases["base_runs"][slot], bases["base_runs"][slot + 1]):
            at = slice(bases["run_offsets"][run], bases["run_offsets"][run + 1])
            groups = bases["pair_groups"][at]
            assert (bases["group_types"][groups] == bases["run_types"][run]).all()
            found.update(zip(groups.tolist(), bases["pair_minutes"][at].tolist()))
    return found


def test_towns_with_enough_hike_groups_are_kept_and_others_dropped(tmp_path: Path) -> None:
    out = _export(tmp_path)

    _build(out)

    bases = _load(out)
    assert bases["place_ids"].tolist() == [KESWICK, BRAITHWAITE]
    np.testing.assert_allclose(bases["points"], [(0.0, 0.0), (-0.05, 0.0)])
    assert json.loads((out / "bases" / "meta.json").read_text())["bases"] == 2


def test_hikes_of_one_type_in_one_cell_and_their_twins_are_one_group(tmp_path: Path) -> None:
    out = _export(tmp_path)

    _build(out)

    bases = _load(out)
    starts, hikes = bases["group_starts"], bases["group_hikes"]
    groups = [sorted(hikes[starts[g] : starts[g + 1]].tolist()) for g in range(len(starts) - 1)]
    assert [0, 1, 2] in groups
    assert len(groups) == len(KINDS) * PER_KIND - 2
    assert sorted(hikes.tolist()) == list(range(len(KINDS) * PER_KIND))
    assert (np.diff(bases["group_types"]) >= 0).all()


def test_a_groups_minutes_are_its_nearest_trailheads(tmp_path: Path) -> None:
    out = _export(tmp_path)

    _build(out)

    bases = _load(out)
    starts, hikes = bases["group_starts"], bases["group_hikes"]
    (twin_group,) = [g for g in range(len(starts) - 1) if 0 in hikes[starts[g] : starts[g + 1]]]
    within_15 = _in_reach(bases, 0, 15)
    assert within_15[twin_group] == 8  # hike 1's 7.1 minutes, rounded up, not hike 0's 26.2
    # Hike 24, 12.0 km out (25.7 minutes), is in the 30 minute band only.
    (far_group,) = [g for g in range(len(starts) - 1) if 24 in hikes[starts[g] : starts[g + 1]]]
    assert far_group not in within_15
    assert _in_reach(bases, 0, 30)[far_group] == 26
    assert len(_in_reach(bases, 0, 60)) == len(starts) - 1 - 5  # every group but Remote's five


def test_a_stopped_run_resumes_where_it_stopped(tmp_path: Path) -> None:
    whole = _export(tmp_path / "whole")
    _build(whole)
    out = _export(tmp_path / "stopped")
    server, url, calls = _valhalla(fail_after=1)
    try:
        with pytest.raises(urllib.error.HTTPError):
            export_bases(out, url, chunk=1)
    finally:
        _stop(server)
    assert not (out / "bases" / "meta.json").exists()

    server, url, calls = _valhalla()
    try:
        export_bases(out, url, chunk=1)
    finally:
        _stop(server)

    assert len(calls) == 2  # Keswick's reach was kept; Braithwaite and Remote remained
    for name, values in _load(whole).items():
        np.testing.assert_array_equal(_load(out)[name], values)


def test_any_number_of_workers_gives_the_same_bases(tmp_path: Path) -> None:
    one, three = _export(tmp_path / "one"), _export(tmp_path / "three")

    _build(one)
    _build(three, workers=3, chunk=2)

    for name, values in _load(one).items():
        np.testing.assert_array_equal(_load(three)[name], values)


def test_export_bases_command_writes_the_bases_folder(tmp_path: Path) -> None:
    out = _export(tmp_path)
    server, url, _ = _valhalla()
    try:
        code = main(["export-bases", "--out", str(out), "--valhalla-url", url])
    finally:
        _stop(server)

    assert code == 0
    assert (out / "bases" / "meta.json").is_file()
