"""Finished work that survives a stopped rebuild, so a rerun with the same inputs
carries on where it stopped.

Two levels, both kept in the build's own data folder (beside the Catalog), never
in a temporary directory, each keyed on its inputs and on ``code_version()``:

- ``PhaseStore``: the phases of ``ingest`` after its passes (the relation
  DayHikes, then each built source: DayStages, NetworkHikes, named trails, the
  DestinationHikes). A phase's result is pickled beside the work file when it
  finishes; a resumed ingest loads it instead of computing it again. The work
  file already keeps the passes over the snapshot (relations, ways, nodes).
- ``StepLedger``: a marker for each finished step of the rebuild (``ingest``,
  ``sample-dem``, ...). ``--resume`` skips a step whose marker still matches its
  inputs and the markers of the steps it reads from.

A phase or step that was running when the machine stopped has no result or
marker, so it runs again from its start. Nothing here changes what a step
writes: the files are the same with or without a resume.
"""

from __future__ import annotations

import hashlib
import json
import pickle
import shutil
import uuid
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path

CHUNK = 500  # list items or dict entries per pickle record, so the pickler's memo stays small


@lru_cache(maxsize=1)
def code_version() -> str:
    """A hash of the source of every module of ``dayhike``: a change to any of it
    makes results kept by earlier code stale, with no version to remember to bump."""
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def files_key(directory: Path | str | None, pattern: str = "**/*") -> str:
    """The names, sizes and modification times of the files of ``directory`` that match ``pattern``
    (everything below it by default); empty when there is no such directory."""
    if directory is None or not Path(directory).is_dir():
        return ""
    root = Path(directory)
    rows = []
    for path in sorted(root.glob(pattern)):
        if path.is_file():
            stat = path.stat()
            rows.append((path.relative_to(root).as_posix(), stat.st_size, stat.st_mtime_ns))
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


def file_key(path: Path | str | None) -> list | None:
    """A file's size and modification time, ``None`` when it is not there."""
    if path is None or not Path(path).is_file():
        return None
    stat = Path(path).stat()
    return [stat.st_size, stat.st_mtime_ns]


def _digest(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:24]


def phases_dir_for(work_path: Path) -> Path:
    return work_path.with_name(work_path.name + ".phases")


def remove_phases(work_path: Path) -> None:
    shutil.rmtree(phases_dir_for(work_path), ignore_errors=True)


class PhaseStore:
    """The results of ingest's phases, one pickle each in ``{work file}.phases``.

    Phases are asked for in the order they run. A phase's key is made of the
    store's ``base_key`` (the inputs of the whole ingest and the code version)
    and the key of the phase before it, so a phase is loaded only when every
    phase before it was too, or was computed again to the same key."""

    def __init__(self, work_path: Path, base_key: str) -> None:
        self.directory = phases_dir_for(work_path)
        self._chain = base_key

    def _key(self, name: str) -> str:
        return _digest(self._chain, name)

    def load(self, name: str) -> dict[str, object] | None:
        """The result saved for ``name`` under the current key, else ``None``."""
        key = self._key(name)
        path = self.directory / f"{name}.pkl"
        try:
            payload: dict[str, object] = {}
            with path.open("rb") as stored:
                if pickle.load(stored) != key:
                    return None
                while (record := pickle.load(stored)) != ("end",):
                    kind, field, value = record
                    if kind == "list":
                        payload.setdefault(field, []).extend(value)  # type: ignore[union-attr]
                    elif kind == "dict":
                        payload.setdefault(field, {}).update(value)  # type: ignore[union-attr]
                    else:
                        payload[field] = value
        except (OSError, EOFError, ValueError, pickle.UnpicklingError, AttributeError, ImportError):
            return None
        self._chain = key
        return payload

    def save(self, name: str, payload: Mapping[str, object]) -> None:
        """Keep a phase's result: lists and dicts are written in chunks, the rest whole.
        A result that cannot be written (a full disk) is left out: it only saves time."""
        key = self._key(name)
        path = self.directory / f"{name}.pkl"
        tmp = path.with_name(path.name + ".writing")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with tmp.open("wb") as out:
                pickle.dump(key, out, pickle.HIGHEST_PROTOCOL)
                for field, value in payload.items():
                    if isinstance(value, list):
                        for start in range(0, len(value), CHUNK):
                            pickle.dump(("list", field, value[start : start + CHUNK]), out, pickle.HIGHEST_PROTOCOL)
                        if not value:
                            pickle.dump(("list", field, []), out, pickle.HIGHEST_PROTOCOL)
                    elif isinstance(value, dict):
                        items = list(value.items())
                        for start in range(0, len(items), CHUNK):
                            pickle.dump(("dict", field, dict(items[start : start + CHUNK])), out, pickle.HIGHEST_PROTOCOL)
                        if not items:
                            pickle.dump(("dict", field, {}), out, pickle.HIGHEST_PROTOCOL)
                    else:
                        pickle.dump(("value", field, value), out, pickle.HIGHEST_PROTOCOL)
                pickle.dump(("end",), out, pickle.HIGHEST_PROTOCOL)
            tmp.replace(path)
        except OSError:
            tmp.unlink(missing_ok=True)
        self._chain = key


def ingest_key(
    fingerprint: str,
    levels: Iterable[str],
    schema: int,
    source_names: Iterable[str],
    heights_dir: Path | str | None,
    previous: Mapping | None,
) -> str:
    """The inputs of an ingest that decide what its phases compute, with the code version."""
    return _digest(
        code_version(),
        schema,
        fingerprint,
        list(levels),
        list(source_names),
        files_key(heights_dir, "*.tif"),
        None if previous is None else hashlib.sha256(repr(sorted((sorted(k), v) for k, v in previous.items())).encode()).hexdigest(),
    )


# Each step reads the Catalog and GeometryStore as the steps before it left them; the markers
# of those steps that exist are part of its key, so a step that ran again invalidates what came after.
STEP_READS: dict[str, tuple[str, ...]] = {
    "ingest": (),
    "sample-dem": ("ingest",),
    "sample-landcover": ("ingest", "sample-dem"),
    "sample-climate": ("ingest", "sample-dem", "sample-landcover"),
    "sample-trailgraph": ("ingest",),
    "count-commons": ("ingest",),
    "build-similarity": ("ingest", "sample-dem", "sample-landcover", "sample-climate", "sample-trailgraph"),
    "export-serving": (
        "ingest", "sample-dem", "sample-landcover", "sample-climate", "sample-trailgraph", "count-commons", "build-similarity",
    ),
}


class StepLedger:
    """A marker per finished rebuild step, in ``{catalog}.steps``, beside the Catalog.

    ``done(step, inputs)`` is true when the step's marker holds the same inputs
    (its arguments and the raster or dump files it reads), the same code version
    and the same markers of the steps it reads from (``STEP_READS``). The marker
    is written by ``mark`` after the step succeeds; ``ingest`` clears every other
    marker, since it replaces the files they describe. Files changed by anything
    but these steps are not noticed."""

    def __init__(self, catalog_path: Path | str) -> None:
        catalog_path = Path(catalog_path)
        self.directory = catalog_path.with_name(catalog_path.name + ".steps")

    def _marker(self, step: str) -> Path:
        return self.directory / f"{step}.json"

    def _read(self, step: str) -> dict | None:
        try:
            return json.loads(self._marker(step).read_text())
        except (OSError, ValueError):
            return None

    def _key(self, step: str, inputs: Mapping) -> str:
        upstream = {name: (marker or {}).get("nonce") for name in STEP_READS[step] if (marker := self._read(name))}
        return _digest(step, code_version(), dict(inputs), upstream)

    def done(self, step: str, inputs: Mapping) -> bool:
        marker = self._read(step)
        return marker is not None and marker.get("key") == self._key(step, inputs)

    def mark(self, step: str, inputs: Mapping) -> None:
        if step == "ingest":
            shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True, exist_ok=True)
        marker = {"step": step, "key": self._key(step, inputs), "nonce": uuid.uuid4().hex}
        tmp = self._marker(step).with_name(f"{step}.json.writing")
        tmp.write_text(json.dumps(marker))
        tmp.replace(self._marker(step))
