"""Worker processes for the rebuild's stages (ADR-0044).

``workers()`` is how many processes a stage may use, from ``DAYHIKE_WORKERS``
(the CLI's ``--workers`` sets it), 1 when unset: one process, as before.
``parallel_map`` maps a function over items in that many forked processes and
returns the results in item order, so a stage builds the same thing with any
worker count; ``parallel_iter`` streams them. The workers are forked, so they
read what the stage holds (the snapshot, a graph, a closure's variables)
without it being sent to them; only the items and the results are.
"""

from __future__ import annotations

import gc
import multiprocessing
import os
import sys
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from typing import TypeVar

WORKERS_ENV = "DAYHIKE_WORKERS"
ACCELERATE_THREADS_ENV = "VECLIB_MAXIMUM_THREADS"

# numpy's BLAS on macOS, Apple Accelerate, runs on GCD threads a forked worker
# cannot use: its first BLAS call there can hang for good. With one thread every
# call stays on its caller's thread. Accelerate reads this when numpy loads it,
# so dayhike imports this module before anything imports numpy.
if "numpy" not in sys.modules:
    os.environ.setdefault(ACCELERATE_THREADS_ENV, "1")
_FORK_SAFE_BLAS = sys.platform != "darwin" or os.environ.get(ACCELERATE_THREADS_ENV) == "1"
IN_FLIGHT_PER_WORKER = 4  # parallel_iter's items sent ahead per worker
CHUNKS_PER_WORKER = 16  # items go out in chunks this many times the workers, so one slow chunk waits little

T = TypeVar("T")
R = TypeVar("R")

_task: Callable | None = None  # the function the forked workers run


def workers() -> int:
    """The worker count from ``DAYHIKE_WORKERS``: a whole number of at least 1, 1 when unset."""
    value = os.environ.get(WORKERS_ENV)
    if value is None:
        return 1
    try:
        count = int(value)
    except ValueError:
        count = 0
    if count < 1:
        raise ValueError(f"{WORKERS_ENV} must be a whole number of at least 1, got {value!r}")
    return count


def parallel_map(task: Callable[[T], R], items: Iterable[T]) -> list[R]:
    """``[task(item) for item in items]``, run in ``workers()`` forked processes
    when that is more than 1, in item order either way."""
    items = list(items)
    count = min(workers(), len(items))
    if count <= 1:
        return [task(item) for item in items]
    _check_fork_safe_blas()
    global _task
    _task = task
    # Frozen objects are left out of the workers' garbage collection, so it does
    # not write to (and copy) the pages they share with this process.
    gc.freeze()
    try:
        with multiprocessing.get_context("fork").Pool(count) as pool:
            return pool.map(_run, items, chunksize=max(1, len(items) // (count * CHUNKS_PER_WORKER)))
    finally:
        gc.unfreeze()
        _task = None


def parallel_iter(task: Callable[[T], R], items: Iterable[T]) -> Iterator[R]:
    """``task`` of each item, yielded in item order, run in ``workers()`` forked
    processes when that is more than 1. Items are read as results are taken,
    at most ``IN_FLIGHT_PER_WORKER`` per worker ahead, so a stream of large
    items or results is never held whole. The workers are forked once, when
    the first result is asked for: they read the state of that moment."""
    count = workers()
    if count <= 1:
        yield from map(task, items)
        return
    _check_fork_safe_blas()
    global _task
    _task = task
    gc.freeze()
    try:
        with multiprocessing.get_context("fork").Pool(count) as pool:
            pending: deque = deque()
            for item in items:
                pending.append(pool.apply_async(_run, (item,)))
                if len(pending) >= count * IN_FLIGHT_PER_WORKER:
                    yield pending.popleft().get()
            while pending:
                yield pending.popleft().get()
    finally:
        gc.unfreeze()


def _check_fork_safe_blas() -> None:
    if not _FORK_SAFE_BLAS:
        raise RuntimeError(
            f"more than one worker needs {ACCELERATE_THREADS_ENV}=1 before numpy is imported "
            "(import dayhike first, or set it in the environment); else use one worker"
        )


def _run(item):
    return _task(item)
