"""The regional harness (tests/rebuild/regional.py) measures a serving export around an Origin.

The export is the SuggestedLoops one (test_loops.py): tests/fixtures/trailgraph.osm
with a Middle Path from W to G across the Lake Loop. The Origin is A, the Lake
Loop's corner by the parking; distances below are from A, in the fixture's metres.
"""

import sys
from pathlib import Path

from test_loops import data  # noqa: F401  (the export fixture)

sys.path.insert(0, str(Path(__file__).parent / "rebuild"))
import regional  # noqa: E402

A = (46.05, 14.05)
# 0.25 km holds A only; 0.6 km also W (500 m) and Z2 (500 m); 2 km every DayHike.
AROUND_A = regional.Measurement(origin=A, radii_km=(0.25, 0.6, 2.0))


def test_counts_dayhikes_passing_and_starting_within_each_radius(data) -> None:  # noqa: F811
    measured = regional.measure(data.directory, AROUND_A)

    # Passing: the Lake Loop and the Summit Line at A; the Middle Path at W and the Meadow Path at Z2,
    # 500 m; the Cross Trail (856 m), the Connector (1.1 km) and the Bridge Path (1.9 km) within 2 km.
    assert measured["relations"] == {0.25: 2, 0.6: 4, 2.0: 7}
    # Starting: the Meadow Path starts at Z1 (728 m), its end nearer the parking, so not within 0.6 km.
    assert measured["trailheads"] == {0.25: 2, 0.6: 3, 2.0: 7}


def test_counts_the_loops_the_trailgraph_holds_near_the_starts(data) -> None:  # noqa: F811
    plan = regional.Measurement(origin=A, radii_km=(0.25, 0.6, 2.0), loop_lengths_km=(3.0, 6.0), starts=(A,))

    measured = regional.measure(data.directory, plan)

    # The Middle Path cuts the 4 km Lake Loop into two loops of 3.1 km; no cycle is 4.5-7.5 km long.
    assert {km: loops["found"] for km, loops in measured["loops"].items()} == {3.0: 2, 6.0: 0}


def test_counts_the_rows_and_loops_a_ranking_from_the_origin_lists(data, monkeypatch) -> None:  # noqa: F811
    # Every hike its own row, as if no two started near each other.
    monkeypatch.setattr("dayhike.serving.GROUP_MIN_M", 0.0)
    monkeypatch.setattr("dayhike.serving.GROUP_SHARE", 0.0)
    plan = regional.Measurement(
        origin=A,
        radii_km=(0.25, 0.6, 2.0),
        loop_lengths_km=(3.0,),
        loop_max_km=2.0,
        loop_within_km=2.0,
        page_length_km=3.0,
        page_max_km=2.0,
    )

    measured = regional.measure(data.directory, plan)

    # Both 3.1 km loops start on the Lake Loop, within 2 km of A.
    assert measured["loops"][3.0]["listed"] == 2
    # The 7 DayHikes; within 0.25 km the two starting at A, within 0.6 km also the Middle Path (W).
    off, on = measured["first_page"]["loops_off"], measured["first_page"]["loops_on"]
    assert (off["rows"], off["hikes"], off["rows_within"]) == (7, 7, {0.25: 2, 0.6: 3})
    assert (on["rows"], on["hikes"]) == (9, 9)


def test_reports_the_size_of_each_export_file_and_the_total(data) -> None:  # noqa: F811
    export = data.directory
    files = [path for path in export.rglob("*") if path.is_file()]

    measured = regional.measure(export, AROUND_A)

    assert measured["sizes"] == {
        "trailgraph.sqlite": (export / "trailgraph.sqlite").stat().st_size,
        "hikes.sqlite": (export / "hikes.sqlite").stat().st_size,
        "similarity": sum(path.stat().st_size for path in (export / "similarity").iterdir()),
        "total": sum(path.stat().st_size for path in files),
    }
    assert measured["sizes"]["trailgraph.sqlite"] > 0
