import itertools
import os

import pytest

from dayhike.parallel import WORKERS_ENV, parallel_iter, parallel_map, workers


def test_one_worker_by_default(monkeypatch):
    monkeypatch.delenv(WORKERS_ENV, raising=False)

    assert workers() == 1


def test_the_worker_count_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv(WORKERS_ENV, "3")

    assert workers() == 3


@pytest.mark.parametrize("value", ["0", "-2", "two", ""])
def test_a_worker_count_that_is_not_a_positive_whole_number_is_refused(monkeypatch, value):
    monkeypatch.setenv(WORKERS_ENV, value)

    with pytest.raises(ValueError, match=WORKERS_ENV):
        workers()


def test_parallel_map_returns_each_result_in_item_order(monkeypatch):
    monkeypatch.setenv(WORKERS_ENV, "3")
    table = {n: n * n for n in range(500)}  # read by the workers, never sent to them

    assert parallel_map(lambda n: table[n], range(500)) == [n * n for n in range(500)]


def test_parallel_map_runs_in_other_processes_with_more_than_one_worker(monkeypatch):
    monkeypatch.setenv(WORKERS_ENV, "2")

    pids = set(parallel_map(lambda _: os.getpid(), range(200)))

    assert os.getpid() not in pids
    assert len(pids) >= 1


def test_parallel_map_runs_here_with_one_worker(monkeypatch):
    monkeypatch.setenv(WORKERS_ENV, "1")

    assert set(parallel_map(lambda _: os.getpid(), range(20))) == {os.getpid()}


def test_an_error_in_a_worker_is_raised_here(monkeypatch):
    monkeypatch.setenv(WORKERS_ENV, "2")

    def fail_on_seven(n):
        if n == 7:
            raise ValueError("seven")
        return n

    with pytest.raises(ValueError, match="seven"):
        parallel_map(fail_on_seven, range(20))


def test_parallel_iter_yields_each_result_in_item_order_reading_items_as_it_goes(monkeypatch):
    monkeypatch.setenv(WORKERS_ENV, "2")

    results = parallel_iter(lambda n: n * n, itertools.count())  # never ends: read only as needed

    assert [next(results) for _ in range(300)] == [n * n for n in range(300)]
    results.close()


def test_parallel_iter_runs_here_with_one_worker(monkeypatch):
    monkeypatch.setenv(WORKERS_ENV, "1")

    assert set(parallel_iter(lambda _: os.getpid(), range(20))) == {os.getpid()}


def test_parallel_iter_runs_in_other_processes_with_more_than_one_worker(monkeypatch):
    monkeypatch.setenv(WORKERS_ENV, "2")

    assert os.getpid() not in set(parallel_iter(lambda _: os.getpid(), range(50)))
