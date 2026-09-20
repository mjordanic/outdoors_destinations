import inspect
import json
import re
from pathlib import Path

from dayhike import build_catalog, filter_by_drive_time, rank_dayhikes, resolve_seed

README = Path(__file__).resolve().parents[1] / "README.md"
REPO = README.parent
VALHALLA_DIR = REPO / "data" / "valhalla"


def test_readme_covers_install_ingest_rank_origin_odbl_and_gitignore() -> None:
    text = README.read_text()

    assert "uv sync" in text
    assert "tests/fixtures/catalog.osm" in text
    assert "build_catalog" in text
    assert "dayhike ingest" in text
    assert "dayhike rank" in text
    assert "--origin" in text
    assert "--max-hours" in text
    assert "ODbL" in text
    assert "data/catalog.sqlite" in text
    assert "gitignor" in text.lower()
    assert "data/osm/" in text
    assert "data/dem/" in text
    assert "data/valhalla/" in text


def test_readme_states_ingest_replaces_catalog_and_does_not_append() -> None:
    text = README.read_text()

    assert "does not append" in text
    assert "Ingest replaces the Catalog" in text
    assert "Slovenia is gone" in text
    text = README.read_text()

    assert "Seed names must exist in the Catalog" in text
    assert "DriveTime is not a FeatureVector column" in text


def test_readme_documents_ingest_progress_resume_and_work_file() -> None:
    text = README.read_text()

    assert "--list" in text
    assert "--fresh" in text
    assert "catalog.sqlite.work" in text
    assert "stderr" in text.casefold()
    assert "europe-latest.osm.pbf" in text
    assert "osmium extract" in text


def test_readme_links_valhalla_compose_and_docs() -> None:
    text = README.read_text()

    assert "docs/valhalla.md" in text
    assert "data/valhalla/compose.yaml" in text


def test_valhalla_mjolnir_omits_walk_bike_and_driveways() -> None:
    flags = json.loads((VALHALLA_DIR / "mjolnir.drive-time.json").read_text())
    mjolnir = flags["mjolnir"]

    assert mjolnir["include_pedestrian"] is False
    assert mjolnir["include_bicycle"] is False
    assert mjolnir["include_driveways"] is False


def test_valhalla_compose_applies_drive_time_mjolnir_flags() -> None:
    compose = (VALHALLA_DIR / "compose.yaml").read_text()

    assert "mjolnir.drive-time.json" in compose
    assert "/valhalla/scripts/run.sh" in compose


def test_readme_documents_car_only_valhalla_graph_and_how_to_restore() -> None:
    text = README.read_text()

    assert "prepare-roads.sh" in text
    assert "prepare-highways.sh" in text
    assert "mjolnir.drive-time.json" in text
    assert "include_driveways" in text
    assert "include_pedestrian" in text
    assert "include_bicycle" in text
    assert "buildings" in text.casefold()
    assert "driveway" in text.casefold()
    assert "valhalla_tiles" in text
    assert "unfiltered" in text.casefold()


def test_prepare_highways_keeps_only_motorway_trunk_primary_and_ferry() -> None:
    text = (VALHALLA_DIR / "prepare-highways.sh").read_text()
    match = re.search(r"w/highway=(\S+)", text)
    assert match is not None
    kept = set(match.group(1).split(","))

    assert kept == {
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "ferry",
    }
    assert "type=restriction" not in text


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
