import inspect
from pathlib import Path

from dayhike import build_catalog, filter_by_drive_time, rank_dayhikes, resolve_seed

README = Path(__file__).resolve().parents[1] / "README.md"


def test_readme_covers_install_ingest_rank_origin_odbl_and_gitignore() -> None:
    text = README.read_text()

    assert "uv sync" in text
    assert "tests/fixtures/catalog.osm" in text
    assert "build_catalog" in text
    assert "dayhike rank" in text
    assert "--origin" in text
    assert "--max-hours" in text
    assert "ODbL" in text
    assert "data/catalog.sqlite" in text
    assert "gitignor" in text.lower()
    assert "data/osm/" in text
    assert "data/valhalla/" in text


def test_readme_states_seed_must_exist_and_drivetime_is_not_a_feature() -> None:
    text = README.read_text()

    assert "Seed names must exist in the Catalog" in text
    assert "DriveTime is not a FeatureVector column" in text


def test_readme_links_valhalla_compose_and_docs() -> None:
    text = README.read_text()

    assert "docs/valhalla.md" in text
    assert "docs/valhalla/compose.yaml" in text


def test_public_seams_have_glossary_docstrings() -> None:
    docs = {
        "build_catalog": inspect.getdoc(build_catalog),
        "rank_dayhikes": inspect.getdoc(rank_dayhikes),
        "filter_by_drive_time": inspect.getdoc(filter_by_drive_time),
        "resolve_seed": inspect.getdoc(resolve_seed),
    }
    for name, doc in docs.items():
        assert doc, f"{name} is missing a docstring"

    assert "DayHike" in docs["build_catalog"]
    assert "Catalog" in docs["build_catalog"]
    assert "Seed" in docs["rank_dayhikes"]
    assert "Ranking" in docs["rank_dayhikes"]
    assert "DayHike" in docs["rank_dayhikes"]
    assert "Origin" in docs["filter_by_drive_time"]
    assert "DriveTime" in docs["filter_by_drive_time"]
    assert "Ranking" in docs["filter_by_drive_time"]
    assert "Seed" in docs["resolve_seed"]
    assert "DayHike" in docs["resolve_seed"]
    assert "Catalog" in docs["resolve_seed"]
