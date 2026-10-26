"""Every notebook runs top to bottom in a Jupyter kernel on the real Catalog.

Slow, opt-in (DAYHIKE_REAL_DATA=1). The notebooks read data/ of this checkout,
so the check skips when DAYHIKE_DATA_DIR points elsewhere. ``02``, ``04`` and
``04a`` open the local Catalog only outside pytest, so the kernel runs without
PYTEST_CURRENT_TEST; ``01`` and ``03`` always use the committed fixture.
tests/test_demonstration_notebooks.py runs ``01``-``04a`` on the fixture in the
normal suite, and tests/test_real_data.py checks what ``05`` verifies.
"""

from __future__ import annotations

import nbformat
import pytest
import rebuild_checks as rc
from nbclient import NotebookClient
from test_demonstration_notebooks import NOTEBOOKS, WALKTHROUGH

pytestmark = rc.real_data


@pytest.mark.parametrize("name", WALKTHROUGH)
def test_notebook_runs_on_the_real_catalog(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if rc.DATA_DIR.resolve() != (rc.REPO / "data").resolve():
        pytest.skip("the notebooks read data/ of this checkout, not DAYHIKE_DATA_DIR")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("MPLBACKEND", "Agg")
    notebook = nbformat.read(NOTEBOOKS / name, as_version=4)
    # Raises CellExecutionError, with the cell and its traceback, on the first error.
    NotebookClient(
        notebook,
        timeout=1800,
        kernel_name="python3",
        resources={"metadata": {"path": str(NOTEBOOKS)}},
    ).execute()
