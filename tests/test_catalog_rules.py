"""Which relations become DayHikes (ADR-0033, ADR-0034).

tests/fixtures/catalog_rules.osm holds one relation per rule: route=foot,
no upper length cap, iwn like any network, and only pure containers dropped.
"""

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from dayhike import build_catalog
from dayhike.catalog import Catalog, trailgraph_path_for

FIXTURE = Path(__file__).parent / "fixtures" / "catalog_rules.osm"
LAC_WALK, TOUR_MIXTE, SHORT_SPUR, FOOT_LEG = 2001, 2002, 2003, 2004
LONG_TRAVERSE, IWN_STAGE, CONTAINER = 2005, 2006, 2007


@pytest.fixture(scope="module")
def catalog(tmp_path_factory: pytest.TempPathFactory) -> Catalog:
    return build_catalog(FIXTURE, tmp_path_factory.mktemp("rules") / "catalog.sqlite")


def test_a_route_foot_relation_is_a_dayhike_and_on_the_trailgraph(catalog: Catalog) -> None:
    names = {hike.id: hike.name for hike in catalog.dayhikes}
    assert names[LAC_WALK] == "Lac Walk"

    db = sqlite3.connect(trailgraph_path_for(catalog.path))
    try:
        relations = {
            relation
            for (ids,) in db.execute("SELECT relation_ids FROM segments")
            for relation in json.loads(ids)
        }
    finally:
        db.close()
    assert {LAC_WALK, FOOT_LEG} <= relations


@pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium-tool")
def test_the_catalog_extract_of_a_pbf_keeps_route_foot(tmp_path: Path) -> None:
    source = tmp_path / "rules.osm.pbf"
    subprocess.run(
        ["osmium", "sort", str(FIXTURE), "-o", str(source), "--no-progress"], check=True
    )

    catalog = build_catalog(source, tmp_path / "catalog.sqlite")

    assert LAC_WALK in {hike.id for hike in catalog.dayhikes}


def test_a_mixed_relation_is_kept_and_measured_on_its_own_ways(catalog: Catalog) -> None:
    lengths = {hike.id: hike.length_m for hike in catalog.dayhikes}

    assert round(lengths[TOUR_MIXTE]) == 25_000


def test_the_minimum_and_the_node_network_leg_rule_still_drop(catalog: Catalog) -> None:
    report = catalog.ingest_report

    kept = {hike.id for hike in catalog.dayhikes}
    assert SHORT_SPUR not in kept and FOOT_LEG not in kept
    assert (report.dropped_length, report.dropped_node_network) == (1, 1)


def test_a_long_relation_is_kept_with_no_upper_length_cap(catalog: Catalog) -> None:
    lengths = {hike.id: hike.length_m for hike in catalog.dayhikes}

    assert round(lengths[LONG_TRAVERSE]) == 30_000


def test_an_iwn_stage_is_kept_and_its_pure_container_dropped(catalog: Catalog) -> None:
    names = {hike.id: hike.name for hike in catalog.dayhikes}

    assert names[IWN_STAGE] == "Via Dinarica Red R1"
    assert CONTAINER not in names
    assert catalog.ingest_report.dropped_container == 1
    assert catalog.ingest_report.kept == 4
