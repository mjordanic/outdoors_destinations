import sqlite3
import subprocess
import sys
from pathlib import Path

from dayhike import Catalog, build_catalog

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"

RIDGE_WALK_ID = 1001
VALLEY_PATH_ID = 1002
KEPT_IDS = {RIDGE_WALK_ID, VALLEY_PATH_ID}


def test_build_catalog_keeps_named_in_band_dayhikes(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")

    kept = catalog.dayhikes
    assert {hike.id for hike in kept} == KEPT_IDS
    names = {hike.id: hike.name for hike in kept}
    assert names[RIDGE_WALK_ID] == "Ridge Walk"
    assert names[VALLEY_PATH_ID] == "Valley Path"


def _by_id(catalog: Catalog, hike_id: int):
    return next(hike for hike in catalog.dayhikes if hike.id == hike_id)


def test_kept_dayhikes_have_trailhead_and_length_in_band(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    ridge = _by_id(catalog, RIDGE_WALK_ID)
    valley = _by_id(catalog, VALLEY_PATH_ID)

    assert (ridge.trailhead.latitude, ridge.trailhead.longitude) == (45.0, 15.0)
    assert (valley.trailhead.latitude, valley.trailhead.longitude) == (45.2, 15.1)
    assert 2_000 <= ridge.length_m <= 25_000
    assert 2_000 <= valley.length_m <= 25_000


def test_difficulty_is_max_sac_scale_or_missing_not_t1(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    ridge = _by_id(catalog, RIDGE_WALK_ID)
    valley = _by_id(catalog, VALLEY_PATH_ID)

    assert ridge.difficulty.sac_scale == "mountain_hiking"
    assert valley.difficulty.sac_scale is None
    assert valley.difficulty.sac_scale != "hiking"


def test_ingest_report_counts_kept_versus_dropped_by_reason(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    report = catalog.ingest_report

    assert report.kept == 2
    assert report.dropped_unnamed == 1
    assert report.dropped_length == 1
    assert report.dropped_network == 1


def test_second_build_catalog_yields_same_dayhike_identities(tmp_path: Path) -> None:
    first = build_catalog(FIXTURE, tmp_path / "a.sqlite")
    second = build_catalog(FIXTURE, tmp_path / "b.sqlite")

    assert [hike.id for hike in first.dayhikes] == [hike.id for hike in second.dayhikes]
    assert [hike.name for hike in first.dayhikes] == [hike.name for hike in second.dayhikes]


def test_osm_attribution_is_stored_with_the_catalog(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    catalog = Catalog.open(path)

    assert catalog.attribution == "© OpenStreetMap contributors"


def test_catalog_has_vacant_labels_table(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, path)

    connection = sqlite3.connect(path)
    try:
        columns = [
            row[1] for row in connection.execute("PRAGMA table_info(labels)")
        ]
        assert columns == ["hiker_id", "dayhike_id", "score", "created_at"]
        assert connection.execute("SELECT COUNT(*) FROM labels").fetchone() == (0,)
    finally:
        connection.close()


def test_data_catalog_sqlite_remains_gitignored() -> None:
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "check-ignore", "-q", "data/catalog.sqlite"],
        cwd=repo,
        check=False,
    )
    assert result.returncode == 0


def test_valhalla_compose_is_tracked_and_tiles_are_ignored() -> None:
    repo = Path(__file__).resolve().parents[1]
    compose = subprocess.run(
        ["git", "check-ignore", "-q", "data/valhalla/compose.yaml"],
        cwd=repo,
        check=False,
    )
    tiles = subprocess.run(
        ["git", "check-ignore", "-q", "data/valhalla/valhalla_tiles.tar"],
        cwd=repo,
        check=False,
    )
    assert compose.returncode == 1
    assert tiles.returncode == 0


def test_build_catalog_writes_only_to_the_given_path(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "catalog.sqlite"
    catalog = build_catalog(FIXTURE, path)

    assert catalog.path == path
    assert path.is_file()


def test_cli_ingest_writes_catalog_and_prints_report(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dayhike",
            "ingest",
            str(FIXTURE),
            "--catalog",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert path.is_file()
    assert "kept=2" in result.stdout
    assert "Ridge Walk" in result.stdout
    assert "Valley Path" in result.stdout
    catalog = Catalog.open(path)
    assert {hike.id for hike in catalog.dayhikes} == KEPT_IDS
