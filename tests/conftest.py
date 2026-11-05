import os
from pathlib import Path

import pytest

# Before any test module imports numpy: forked workers need Apple Accelerate on
# one thread (dayhike.parallel, ADR-0044).
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")


@pytest.fixture(scope="session", autouse=True)
def away_from_the_real_data(tmp_path_factory: pytest.TempPathFactory):
    """Every test runs from an empty directory. The library's default raster folders
    (``data/dem``, ``data/landcover``, ``data/climate``) are relative to it, so real data
    built in the repository's ``data/`` never reaches a test that leaves one out.
    Session-scoped, so it is in place before any module fixture builds a Catalog."""
    previous = Path.cwd()
    os.chdir(tmp_path_factory.mktemp("cwd"))
    yield
    os.chdir(previous)
