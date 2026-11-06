from pathlib import Path

import pytest

from dayhike import build_catalog

FIXTURES = Path(__file__).parent / "fixtures"


def test_reversed_member_ways_assemble_into_one_linestring(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURES / "reversed_ways.osm", tmp_path / "catalog.sqlite")
    hike = catalog.dayhikes[0]

    assert hike.name == "Zig Zag"
    assert hike.length_m == pytest.approx(5_000.0, abs=1.0)
    assert (hike.trailhead.latitude, hike.trailhead.longitude) == (45.0, 15.0)


def test_trailhead_is_the_endpoint_nearer_parking(tmp_path: Path) -> None:
    catalog = build_catalog(
        FIXTURES / "trailhead_at_parking.osm", tmp_path / "catalog.sqlite"
    )
    hike = catalog.dayhikes[0]

    assert hike.name == "Parking End Path"
    assert hike.trailhead.latitude == pytest.approx(45.0449660803)
    assert hike.trailhead.longitude == pytest.approx(15.0)
    assert hike.trailhead.amenity_facts.parking.count == 1


def test_way_and_relation_amenities_count_once_each(tmp_path: Path) -> None:
    catalog = build_catalog(
        FIXTURES / "way_and_relation_amenities.osm", tmp_path / "catalog.sqlite"
    )
    facts = catalog.dayhikes[0].trailhead.amenity_facts

    assert facts.parking.count == 1
    assert facts.parking.nearest_m == pytest.approx(100.0, abs=50.0)
    assert facts.hut.count == 1
    assert facts.restaurant_cafe.count == 1
    assert facts.drinking_water.count == 0
    assert facts.drinking_water.nearest_m is None


def test_amenities_of_ways_and_relations_survive_a_run_stopped_while_resolving_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dayhike.ingest as ingest

    resolve = ingest._resolve_pending_amenities
    catalog_path = tmp_path / "catalog.sqlite"

    def killed_while_resolving(conn) -> None:
        conn.execute("INSERT INTO amenities (osm_key, kind, lat, lon) VALUES ('w0', 'hut', 0, 0)")
        raise RuntimeError("killed")

    monkeypatch.setattr(ingest, "_resolve_pending_amenities", killed_while_resolving)
    with pytest.raises(RuntimeError, match="killed"):
        build_catalog(FIXTURES / "way_and_relation_amenities.osm", catalog_path)
    monkeypatch.setattr(ingest, "_resolve_pending_amenities", resolve)
    messages: list[str] = []
    catalog = build_catalog(
        FIXTURES / "way_and_relation_amenities.osm", catalog_path, progress=messages.append
    )
    facts = catalog.dayhikes[0].trailhead.amenity_facts

    assert "nodes: resumed, already complete" in messages, messages
    assert (facts.parking.count, facts.hut.count, facts.restaurant_cafe.count) == (1, 1, 1)


def test_amenities_of_ways_and_relations_are_resolved_without_reading_every_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dayhike.ingest as ingest

    resolve = ingest._resolve_pending_amenities
    statements: list[str] = []

    def traced(conn) -> None:
        conn.set_trace_callback(statements.append)
        try:
            resolve(conn)
        finally:
            conn.set_trace_callback(None)

    monkeypatch.setattr(ingest, "_resolve_pending_amenities", traced)
    build_catalog(FIXTURES / "way_and_relation_amenities.osm", tmp_path / "catalog.sqlite")

    reads_of_nodes = [s for s in statements if "FROM nodes" in s]
    assert reads_of_nodes
    assert all("WHERE" in s or "JOIN" in s for s in reads_of_nodes), reads_of_nodes
