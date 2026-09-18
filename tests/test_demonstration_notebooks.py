from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NOTEBOOKS = REPO / "notebooks"
README = REPO / "README.md"
FIXTURE = REPO / "tests" / "fixtures" / "catalog.osm"


def _notebook_paths() -> list[Path]:
    return sorted(NOTEBOOKS.glob("*.ipynb"))


def _cell_source(cell: dict) -> str:
    source = cell.get("source", "")
    if isinstance(source, list):
        return "".join(source)
    return str(source)


def _notebook_text(path: Path) -> tuple[str, str, str]:
    nb = json.loads(path.read_text())
    sources: list[str] = []
    markdown: list[str] = []
    namespace: dict = {"__name__": "__main__"}
    stdout = io.StringIO()
    previous = Path.cwd()
    try:
        os.chdir(path.parent)
        with redirect_stdout(stdout):
            for cell in nb["cells"]:
                src = _cell_source(cell)
                sources.append(src)
                if cell.get("cell_type") == "markdown":
                    markdown.append(src)
                elif cell.get("cell_type") == "code" and src.strip():
                    exec(compile(src, str(path), "exec"), namespace)
    finally:
        os.chdir(previous)
    return stdout.getvalue(), "\n".join(markdown), "\n".join(sources)


def _run_notebooks() -> tuple[str, str, str]:
    outputs: list[str] = []
    markdown: list[str] = []
    sources: list[str] = []
    for path in _notebook_paths():
        out, md, src = _notebook_text(path)
        outputs.append(out)
        markdown.append(md)
        sources.append(src)
    return "\n".join(outputs), "\n".join(markdown), "\n".join(sources)


def test_at_least_one_notebook_exists() -> None:
    assert _notebook_paths(), "expected at least one notebook under notebooks/"


def test_notebooks_execute_against_committed_osm_fixture() -> None:
    output, _, source = _run_notebooks()

    assert "tests/fixtures/catalog.osm" in source
    assert FIXTURE.is_file()
    assert output


def test_notebook_shows_ingest_keep_drop_counts_and_kept_names() -> None:
    output, _, _ = _run_notebooks()

    assert "kept=2" in output
    assert "dropped_unnamed=1" in output
    assert "dropped_length=1" in output
    assert "dropped_network=1" in output
    assert "Ridge Walk" in output
    assert "Valley Path" in output


def test_notebook_shows_feature_vectors() -> None:
    output, _, _ = _run_notebooks()

    assert output.count("FeatureVector(") >= 2
    assert "length_m=" in output
    assert "difficulty_ordinal=" in output
    assert "AmenityFacts(" in output


def test_notebook_shows_ranking_for_one_seed_and_two_dissimilar_seeds() -> None:
    output, _, source = _run_notebooks()

    assert "rank_dayhikes" in source
    one_ridge = _tagged_names(output, "one_seed")
    one_valley = _tagged_names(output, "one_seed_valley")
    two_seeds = _tagged_names(output, "two_seeds")

    assert one_ridge == ["Valley Path"]
    assert one_valley == ["Ridge Walk"]
    assert two_seeds[0] == "Near Ridge"
    assert two_seeds.index("Near Ridge") < two_seeds.index("Mid Path")

    max_not_mean_lines = [
        line for line in output.splitlines() if line.startswith("max_not_mean\t")
    ]
    assert max_not_mean_lines
    for line in max_not_mean_lines:
        _, _name, score, expected_max, expected_mean = line.split("\t")
        assert float(score) == pytest.approx(float(expected_max))
        assert float(score) != pytest.approx(float(expected_mean))


def test_notebook_demonstrates_unknown_seed_failure() -> None:
    output, _, source = _run_notebooks()

    assert "UnknownSeedName" in source
    assert "UnknownSeedName" in output
    assert "Unknown Ridge" in output


def test_notebook_shows_stubbed_drive_time_and_local_valhalla_note() -> None:
    output, markdown, source = _run_notebooks()

    assert "filter_by_drive_time" in source
    assert _tagged_names(output, "drive_keep") == ["Valley Path"]
    assert "drive_drop_count\t0" in output
    assert "Valhalla" in markdown
    assert "tile" in markdown.casefold()


def test_notebook_states_catalog_limitations() -> None:
    _, markdown, _ = _run_notebooks()
    folded = markdown.casefold()

    assert "route=hiking" in markdown
    assert "length band" in folded
    assert "unnamed" in folded
    assert "scraped reviews" in folded
    assert "webpage ratings" in folded or "webpage rating" in folded
    assert "future work" in folded


def test_notebook_shows_ingest_stages_and_resume() -> None:
    output, markdown, source = _run_notebooks()

    assert "filter:" in output
    assert "relations:" in output
    assert "interrupted" in output
    assert "resumed, already complete" in output
    assert "route=hiking" in markdown


def test_readme_points_at_demonstration_notebooks() -> None:
    text = README.read_text()
    assert "notebooks/catalog-and-ranker.ipynb" in text
    assert "notebooks/working-with-the-catalog.ipynb" in text
    assert "notebooks/ingest-a-snapshot.ipynb" in text



def _tagged_names(output: str, tag: str) -> list[str]:
    prefix = f"{tag}\t"
    return [
        line.split("\t", 1)[1].split("\t", 1)[0]
        for line in output.splitlines()
        if line.startswith(prefix)
    ]
