"""Resuming a stopped rebuild (dayhike.steps): ingest keeps each finished phase beside its
work file and a rerun loads it; every step leaves a marker and ``--resume`` skips a finished
one. The files are the same as an uninterrupted run's, whatever stopped it and where."""

import functools
import shutil
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from dayhike import build_catalog, geometry_store_path_for, ingest
from dayhike.catalog import trailgraph_path_for
from dayhike.destination_hikes import destination_source
from dayhike.ingest import BUILT_HIKE_SOURCES, work_path_for
from dayhike.steps import PhaseStore, StepLedger, code_version, phases_dir_for

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


# -- phases inside ingest


def test_a_phase_result_comes_back_as_it_was_saved_and_only_under_its_own_key(tmp_path: Path) -> None:
    work = tmp_path / "catalog.sqlite.work"
    payload = {"hikes": [(i, np.arange(3.0) + i) for i in range(1200)], "walks": {i: (i, "x") for i in range(7)}, "n": 3, "none": []}
    PhaseStore(work, "base").save("one", payload)

    loaded = PhaseStore(work, "base").load("one")

    assert loaded is not None and loaded.keys() == payload.keys()
    assert [(i, a.tolist()) for i, a in loaded["hikes"]] == [(i, a.tolist()) for i, a in payload["hikes"]]
    assert (loaded["walks"], loaded["n"], loaded["none"]) == (payload["walks"], 3, [])
    assert PhaseStore(work, "other base").load("one") is None  # other inputs or code
    assert PhaseStore(work, "base").load("two") is None


def test_a_phase_is_loaded_only_after_the_phases_before_it_were_loaded_or_recomputed_alike(tmp_path: Path) -> None:
    work = tmp_path / "catalog.sqlite.work"
    first = PhaseStore(work, "base")
    first.save("a", {"x": 1})
    first.save("b", {"x": 2})

    chain = PhaseStore(work, "base")
    assert chain.load("a") == {"x": 1}
    assert chain.load("b") == {"x": 2}
    skipped = PhaseStore(work, "base")  # "a" never asked for: "b" is not what followed the base
    assert skipped.load("b") is None


def test_a_phase_file_cut_short_is_computed_again(tmp_path: Path) -> None:
    work = tmp_path / "catalog.sqlite.work"
    PhaseStore(work, "base").save("a", {"x": list(range(5000))})
    path = phases_dir_for(work) / "a.pkl"
    path.write_bytes(path.read_bytes()[: path.stat().st_size // 2])

    assert PhaseStore(work, "base").load("a") is None


def _counting(monkeypatch: pytest.MonkeyPatch, sources, killed: str | None = None):
    """The built-hike sources, each counting its calls; the one named ``killed`` stops the build.
    Ingest runs the destination search when its sources hold ``destination_source``, so that
    name points at its counting stand-in."""
    calls: dict[str, int] = {}
    wrapped = []
    for source in sources:
        name = source.__name__

        @functools.wraps(source)
        def run(context, source=source, name=name):
            calls[name] = calls.get(name, 0) + 1
            if name == killed:
                raise RuntimeError("killed")
            return source(context)

        wrapped.append(run)
        if source is destination_source:
            monkeypatch.setattr(ingest, "destination_source", run)
    return wrapped, calls


@pytest.mark.parametrize("killed", ["day_stages", "network_source", "named_trails", "destination_source"])
def test_ingest_stopped_in_a_phase_resumes_after_the_phases_it_finished_to_the_same_files(
    tmp_path: Path, source: Path, killed: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_catalog(source, tmp_path / "clean" / "catalog.sqlite")
    interrupted = tmp_path / "resumed" / "catalog.sqlite"
    names = [s.__name__ for s in BUILT_HIKE_SOURCES]
    sources, first = _counting(monkeypatch, BUILT_HIKE_SOURCES, killed)
    with pytest.raises(RuntimeError, match="killed"):
        build_catalog(source, interrupted, sources=sources)
    assert phases_dir_for(work_path_for(interrupted)).is_dir()

    sources, second = _counting(monkeypatch, BUILT_HIKE_SOURCES)
    messages: list[str] = []
    build_catalog(source, interrupted, sources=sources, progress=messages.append)

    finished = names[: names.index(killed)]
    assert {name: second.get(name, 0) for name in names} == {name: 0 if name in finished else 1 for name in names}
    assert any("resumed" in message and "phase" in message for message in messages) or not finished
    assert _files(interrupted) == _files(tmp_path / "clean" / "catalog.sqlite")
    assert not phases_dir_for(work_path_for(interrupted)).exists()  # the work file and its phases go with the build


def test_ingest_stopped_before_its_first_built_source_still_keeps_the_relation_phase(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = tmp_path / "catalog.sqlite"
    sources, _ = _counting(monkeypatch, BUILT_HIKE_SOURCES, "day_stages")
    with pytest.raises(RuntimeError, match="killed"):
        build_catalog(source, catalog, sources=sources)
    calls = []
    real = ingest._keep_dayhikes
    monkeypatch.setattr(ingest, "_keep_dayhikes", lambda *a, **k: calls.append(1) or real(*a, **k))

    build_catalog(source, catalog)

    assert calls == []


@pytest.mark.parametrize("change", ["source", "code", "fresh"])
def test_phases_kept_for_other_inputs_or_code_are_not_used(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    catalog = tmp_path / "catalog.sqlite"
    sources, _ = _counting(monkeypatch, BUILT_HIKE_SOURCES, "network_source")
    with pytest.raises(RuntimeError, match="killed"):
        build_catalog(source, catalog, sources=sources)
    fresh = False
    if change == "source":
        source.write_text(source.read_text() + "\n")
    elif change == "code":
        monkeypatch.setattr("dayhike.steps.code_version", lambda: "another version of the code")
    else:
        fresh = True
    sources, calls = _counting(monkeypatch, BUILT_HIKE_SOURCES)

    build_catalog(source, catalog, sources=sources, fresh=fresh)

    assert calls["day_stages"] == 1


# -- steps


def test_a_step_is_done_for_the_inputs_and_code_it_ran_with_and_the_steps_it_read_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = StepLedger(tmp_path / "catalog.sqlite")
    assert not ledger.done("sample-dem", {"dem": "a"})
    ledger.mark("ingest", {"osm": "x"})
    ledger.mark("sample-dem", {"dem": "a"})
    assert ledger.done("ingest", {"osm": "x"}) and ledger.done("sample-dem", {"dem": "a"})
    assert not ledger.done("sample-dem", {"dem": "b"})

    ledger.mark("ingest", {"osm": "x"})  # ingest again: the Catalog is a new one
    assert not ledger.done("sample-dem", {"dem": "a"})

    ledger.mark("sample-dem", {"dem": "a"})
    ledger.mark("sample-landcover", {"landcover": "l"})
    ledger.mark("sample-dem", {"dem": "a"})  # a step it read from ran again
    assert not ledger.done("sample-landcover", {"landcover": "l"})
    ledger.mark("sample-landcover", {"landcover": "l"})
    assert ledger.done("sample-landcover", {"landcover": "l"})
    monkeypatch.setattr("dayhike.steps.code_version", lambda: "other code")
    assert not ledger.done("sample-landcover", {"landcover": "l"})


def test_code_version_changes_with_the_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = tmp_path / "dayhike"
    package.mkdir()
    (package / "steps.py").write_text("A = 1\n")
    monkeypatch.setattr("dayhike.steps.__file__", str(package / "steps.py"))
    code_version.cache_clear()
    before = code_version()
    (package / "steps.py").write_text("A = 2\n")
    code_version.cache_clear()
    after = code_version()
    code_version.cache_clear()

    assert len(before) == 16 and before != after


@pytest.mark.parametrize(
    "command, extra, target",
    [
        ("sample-dem", ["--dem", "DEM", "--landcover", "LC"], "dayhike.cli._sample_dem"),
        ("sample-landcover", ["--landcover", "LC"], "dayhike.cli._sample_landcover"),
        ("sample-climate", ["--climate", "CL"], "dayhike.cli._sample_climate"),
        ("sample-trailgraph", ["--dem", "DEM", "--landcover", "LC", "--climate", "CL"], "dayhike.cli._sample_trailgraph"),
        ("build-similarity", ["--dem", "DEM", "--landcover", "LC"], "dayhike.cli._build_similarity"),
        ("count-commons", ["--commons", "CM"], "dayhike.cli.count_commons"),
        ("export-serving", ["--out", "OUT"], "dayhike.cli._export_serving"),
    ],
)
def test_resume_skips_a_finished_step_and_runs_one_whose_inputs_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, extra: list[str], target: str
) -> None:
    from dayhike.cli import main

    catalog = tmp_path / "catalog.sqlite"
    catalog.write_bytes(b"")
    for name in ("DEM", "LC", "CL", "CM"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "tile.tif").write_bytes(b"1")
    (tmp_path / "OUT").mkdir()
    (tmp_path / "OUT" / "meta.json").write_text("{}")
    extra = [str(tmp_path / part) if part in {"DEM", "LC", "CL", "CM", "OUT"} else part for part in extra]
    ran: list[int] = []
    monkeypatch.setattr(target, lambda *a, **k: ran.append(1) or 0)
    argv = [command, "--catalog", str(catalog), *extra]

    assert main(argv) == 0 and ran == [1]  # runs, and leaves its marker
    assert main([*argv, "--resume"]) == 0 and ran == [1]  # done: skipped
    assert main(argv) == 0 and ran == [1, 1]  # without --resume it always runs
    (tmp_path / "DEM" / "tile.tif").write_bytes(b"22")
    (tmp_path / "LC" / "tile.tif").write_bytes(b"22")
    (tmp_path / "CL" / "tile.tif").write_bytes(b"22")
    (tmp_path / "CM" / "tile.tif").write_bytes(b"22")
    (tmp_path / "OUT" / "meta.json").unlink()  # export-serving: its output is gone
    assert main([*argv, "--resume"]) == 0 and ran == [1, 1, 1]  # an input changed: runs again


def test_export_serving_resume_ignores_later_steps_writing_into_its_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dayhike.cli import main

    catalog = tmp_path / "catalog.sqlite"
    catalog.write_bytes(b"")
    out = tmp_path / "OUT"
    out.mkdir()
    (out / "meta.json").write_text("{}")
    ran: list[int] = []
    monkeypatch.setattr("dayhike.cli._export_serving", lambda *a, **k: ran.append(1) or 0)
    argv = ["export-serving", "--catalog", str(catalog), "--out", str(out), "--resume"]

    assert main(argv) == 0 and ran == [1]
    (out / "hikes.sqlite").write_bytes(b"photos added")  # export-photos writes into the same folder
    assert main(argv) == 0 and ran == [1]
    other = tmp_path / "OTHER"
    other.mkdir()
    (other / "meta.json").write_text("{}")
    assert main([*argv[:-2], str(other), "--resume"]) == 0 and ran == [1, 1]  # another output folder


def test_a_step_that_failed_leaves_no_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dayhike.cli import main

    catalog = tmp_path / "catalog.sqlite"
    catalog.write_bytes(b"")
    (tmp_path / "CL").mkdir()
    attempts: list[int] = []

    def fails_once(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("killed")
        return 0

    monkeypatch.setattr("dayhike.cli._sample_climate", fails_once)
    argv = ["sample-climate", "--catalog", str(catalog), "--climate", str(tmp_path / "CL"), "--resume"]
    with pytest.raises(RuntimeError):
        main(argv)

    assert main(argv) == 0
    assert attempts == [1, 1]


def test_resume_skips_a_finished_ingest_and_a_new_ingest_clears_the_markers_after_it(
    tmp_path: Path, source: Path
) -> None:
    from dayhike.cli import main

    catalog = tmp_path / "catalog.sqlite"
    ingest_argv = ["ingest", str(source), "--catalog", str(catalog)]
    assert main(ingest_argv) == 0
    ledger = StepLedger(catalog)
    ledger.mark("sample-dem", {"dem": "d"})
    before = catalog.stat().st_mtime_ns

    assert main([*ingest_argv, "--resume"]) == 0
    assert catalog.stat().st_mtime_ns == before  # skipped
    assert ledger.done("sample-dem", {"dem": "d"})

    assert main(ingest_argv) == 0  # a new ingest
    assert catalog.stat().st_mtime_ns != before
    assert not ledger.done("sample-dem", {"dem": "d"})
