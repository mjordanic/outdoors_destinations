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
        if path.name in REAL_DATA_ONLY:
            continue
        out, md, src = _notebook_text(path)
        outputs.append(out)
        markdown.append(md)
        sources.append(src)
    return "\n".join(outputs), "\n".join(markdown), "\n".join(sources)


WALKTHROUGH = (
    "01_raw_data_and_ingestion.ipynb",
    "02_catalog.ipynb",
    "03_ranking.ipynb",
    "04_rank_feature_lab.ipynb",
    "04a_geometry.ipynb",
    "05_similarity.ipynb",
)
# Needs the local Catalog (data/); tests/test_real_data.py runs it.
REAL_DATA_ONLY = ("05_similarity.ipynb",)


def test_at_least_one_notebook_exists() -> None:
    assert _notebook_paths(), "expected at least one notebook under notebooks/"


def test_notebooks_are_numbered_walkthrough() -> None:
    assert [path.name for path in _notebook_paths()] == list(WALKTHROUGH)


def test_notebooks_execute_against_committed_osm_fixture() -> None:
    output, _, source = _run_notebooks()

    assert "tests/fixtures/catalog.osm" in source
    assert FIXTURE.is_file()
    assert output


def test_notebook_shows_ingest_keep_drop_counts_and_kept_names() -> None:
    output, _, _ = _run_notebooks()

    assert "kept=4" in output
    assert "dropped_unnamed=0" in output
    assert "dropped_length=2" in output
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

    assert "Valley Path" in one_ridge
    assert "Ridge Walk" not in one_ridge
    assert "Ridge Walk" in one_valley
    assert "Valley Path" not in one_valley
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
    assert "Valley Path" in _tagged_names(output, "drive_keep")
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
    for name in WALKTHROUGH:
        assert f"notebooks/{name}" in text
    assert "Notebook walkthrough" in text


def test_readme_documents_landcover_and_climate() -> None:
    text = README.read_text()
    for command in (
        "fetch-landcover",
        "sample-landcover",
        "fetch-climate",
        "sample-climate",
    ):
        assert command in text
    assert text.index("sample-dem") < text.index("fetch-landcover")
    assert "data/landcover/" in text
    assert "data/climate/" in text
    assert "vertex_climate" in text
    assert "--month" in text
    assert "month=7" in text.replace(" ", "")
    assert "fetch_worldcover" in text
    assert "sample_landcover" in text
    assert "fetch_worldclim" in text
    assert "sample_climate" in text
    assert "month=" in text
    assert "### LandCover and ClimateNormal" in text
    assert "**When:**" in text.split("### LandCover and ClimateNormal", 1)[1]
    assert "WorldCover" in text
    assert "Fick" in text
    assert "Hijmans" in text
    assert "QueryMonth" in text


def test_notebook_sanity_checks_catalog_counts_length_band_and_map() -> None:
    output, markdown, source = _run_notebooks()

    assert "The Catalog" in markdown
    assert "data/catalog.sqlite" in source
    assert "sanity kept_matches_rows\tok" in output
    assert "sanity length_band\tok" in output
    assert "sanity trailhead_per_dayhike\tok" in output
    assert "geopandas" in source.casefold()
    assert "contextily" in source.casefold()
    assert "catalog_meta" in markdown
    assert "feature_vectors" in markdown
    assert "DriveTime" in markdown
    assert "table\tdayhikes" in output
    assert "table\ttrailheads" in output
    assert "import pandas" in source
    assert "pandas dayhikes\t" in output
    assert "pandas play\t" in output
    assert "rank_scaled_rows" in source
    assert "geometry_store" in source
    assert "name_origin" in source
    assert "prod_rank\t" in output
    assert "dem skipped" in output or "dem sampled" in output


def test_notebook_catalog_schema_names_landcover_and_climate() -> None:
    _, markdown, _ = _run_notebooks()
    folded = markdown.casefold()

    assert "tree_fraction" in markdown
    assert "shrub_fraction" in markdown
    assert "grass_fraction" in markdown
    assert "bare_fraction" in markdown
    assert "snow_fraction" in markdown
    assert "temperature_c" in markdown
    assert "precipitation_mm" in markdown
    assert "solar_radiation" in markdown
    assert "climate_months" in markdown
    assert "regime" in folded or ("june" in folded and "august" in folded)
    assert "monthly" in folded


def test_notebook_geometry_samples_landcover_and_climate_without_download() -> None:
    output, _, source = _run_notebooks()

    assert "sample_landcover" in source
    assert "sample_climate" in source
    assert "fetch_worldcover" not in source
    assert "fetch_worldclim" not in source
    assert _tagged_int(output, "landcover_classes") > 0
    assert _tagged_int(output, "vertex_climate_rows") > 0


def test_notebook_ranking_shows_query_month_and_landcover() -> None:
    output, _, source = _run_notebooks()

    assert "rank_dayhikes" in source
    assert "month=7" in source.replace(" ", "")
    assert _tagged_names(output, "no_month")
    assert _tagged_names(output, "july")
    assert _tagged_float(output, "july_temperature") != _tagged_float(
        output, "regime_temperature"
    )
    landcover = _tagged_names(output, "landcover_rank")
    assert landcover[0] == "Forested Twin"
    assert landcover.index("Forested Twin") < landcover.index("Bare Twin")


def _tagged_names(output: str, tag: str) -> list[str]:
    prefix = f"{tag}\t"
    return [
        line.split("\t", 1)[1].split("\t", 1)[0]
        for line in output.splitlines()
        if line.startswith(prefix)
    ]


def _tagged_int(output: str, tag: str) -> int:
    values = _tagged_names(output, tag)
    assert values, f"missing {tag} line"
    return int(values[0])


def _tagged_float(output: str, tag: str) -> float:
    values = _tagged_names(output, tag)
    assert values, f"missing {tag} line"
    return float(values[0])


def test_geometry_notebook_shows_leftovers_by_reason() -> None:
    output, markdown, _ = _run_notebooks()
    lines = [line.split("\t") for line in output.splitlines()]

    assert "ADR-0012" in markdown
    reasons = [fields[0] for fields in lines if len(fields) == 4 and fields[0] in (
        "role", "repeat", "disconnected", "dead_end", "shortcut"
    )]
    assert reasons == ["role", "repeat", "disconnected", "dead_end", "shortcut"]
    assert any(f[:2] == ["example", "disconnected"] and len(f) == 5 for f in lines)
    assert "leftover_reasons_missing" in output
    assert any(f[0] == "main_pieces" for f in lines)
