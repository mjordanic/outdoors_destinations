"""Every step that reads the DEM reads the same source, and the export says so (ADR-0030, #178)."""

import json
import sqlite3
from pathlib import Path

import pytest
from test_dem import _write_constant_tif, _write_grassland

from dayhike import build_catalog, build_similarity, sample_dem
from dayhike.serving import ServingExportError, export_serving
from dayhike.trailgraph import sample_trailgraph

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
BOX = {"west": 14.99, "south": 44.99, "east": 16.01, "north": 46.01}
STEPS = ("ingest", "sample-dem", "sample-trailgraph", "build-similarity")
TILE_FILE = {"fabdem": "N45E015_FABDEM_V1-2.tif", "copernicus": "Copernicus_DSM_COG_10_N45_00_E015_00_DEM.tif"}


def _dem_dirs(root: Path) -> dict[str, Path]:
    """One directory per world source, each with a tile over the fixture's hikes."""
    dirs = {}
    for source, file in TILE_FILE.items():
        dirs[source] = root / f"dem-{source}"
        dirs[source].mkdir()
        _write_constant_tif(dirs[source] / file, z=900.0, **BOX)
    return dirs


def _build(root: Path, source_of: dict[str, str]) -> Path:
    """The fixture Catalog built step by step, each DEM step on the DEM directory of ``source_of[step]``
    (a step missing from it reads no DEM), exported; returns the Catalog."""
    dirs = _dem_dirs(root)
    landcover = _write_grassland(root)
    catalog_path = root / "catalog.sqlite"
    dem = {step: dirs[source] for step, source in source_of.items()}
    build_catalog(FIXTURE, catalog_path, dem_dir=dem.get("ingest"))
    sample_dem(catalog_path, dem["sample-dem"], landcover_dir=landcover)
    sample_trailgraph(
        catalog_path, dem_dir=dem["sample-trailgraph"], landcover_dir=landcover, climate_dir=root / "no-climate"
    )
    build_similarity(catalog_path, dem["build-similarity"])
    return catalog_path


def _meta(export: Path) -> dict:
    return json.loads((export / "meta.json").read_text())


@pytest.fixture(scope="module")
def one_source_exports(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict]:
    """The export meta of a whole build on FABDEM and of one on Copernicus GLO-30."""
    exports = {}
    for source in TILE_FILE:
        root = tmp_path_factory.mktemp(source)
        catalog_path = _build(root, {step: source for step in STEPS})
        exports[source] = _meta(export_serving(catalog_path, root / "web-serving"))
    return exports


def test_an_export_records_the_dem_every_step_read(one_source_exports: dict[str, dict]) -> None:
    assert one_source_exports["copernicus"]["dem_read"] == {step: "copernicus-glo30" for step in STEPS}
    assert one_source_exports["fabdem"]["dem_read"] == {step: "fabdem" for step in STEPS}


def test_the_data_image_tag_differs_between_dem_sources_built_on_the_same_code(
    one_source_exports: dict[str, dict],
) -> None:
    fabdem, copernicus = one_source_exports["fabdem"], one_source_exports["copernicus"]

    assert fabdem["facts_version"] == copernicus["facts_version"]
    assert fabdem["data_tag"] == f"{fabdem['facts_version']}-fabdem"
    assert copernicus["data_tag"] == f"{copernicus['facts_version']}-copernicus-glo30"


def test_a_step_that_read_no_dem_does_not_stop_the_export(tmp_path: Path) -> None:
    catalog_path = _build(
        tmp_path, {"sample-dem": "copernicus", "sample-trailgraph": "copernicus", "build-similarity": "copernicus"}
    )

    meta = _meta(export_serving(catalog_path, tmp_path / "web-serving"))

    assert meta["dem_read"] == {step: "copernicus-glo30" for step in STEPS[1:]}
    assert list(meta["attribution"])[:2] == ["osm", "copernicus"]


@pytest.mark.parametrize("odd_step", STEPS)
def test_the_export_refuses_when_one_step_read_another_dem_than_the_rest(tmp_path: Path, odd_step: str) -> None:
    catalog_path = _build(tmp_path, {step: "fabdem" if step == odd_step else "copernicus" for step in STEPS})

    with pytest.raises(ServingExportError) as raised:
        export_serving(catalog_path, tmp_path / "web-serving")

    message = str(raised.value)
    assert f"{odd_step}=fabdem" in message and "copernicus-glo30" in message
    assert not (tmp_path / "web-serving" / "meta.json").exists()


def test_a_catalog_sampled_before_the_steps_recorded_their_dem_still_exports(tmp_path: Path) -> None:
    catalog_path = _build(tmp_path, {step: "fabdem" for step in STEPS})
    with sqlite3.connect(tmp_path / "catalog.geometry.sqlite") as geometry:
        geometry.execute("DELETE FROM geometry_meta WHERE key LIKE 'dem_read%'")
    with sqlite3.connect(tmp_path / "catalog.trailgraph.sqlite") as trailgraph:
        trailgraph.execute("DELETE FROM trailgraph_meta WHERE key = 'dem_read'")

    meta = _meta(export_serving(catalog_path, tmp_path / "web-serving"))

    assert set(meta["dem_read"]) == {"build-similarity"}
