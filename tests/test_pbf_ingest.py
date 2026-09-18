from pathlib import Path

import osmium

from dayhike import build_catalog

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"


def _write_pbf(xml_path: Path, pbf_path: Path) -> Path:
    writer = osmium.SimpleWriter(str(pbf_path))
    try:

        class Copy(osmium.SimpleHandler):
            def node(self, node) -> None:
                writer.add_node(node)

            def way(self, way) -> None:
                writer.add_way(way)

            def relation(self, relation) -> None:
                writer.add_relation(relation)

        Copy().apply_file(str(xml_path))
    finally:
        writer.close()
    return pbf_path


def test_pbf_ingest_keeps_the_same_dayhikes_as_xml(tmp_path: Path) -> None:
    xml_catalog = build_catalog(FIXTURE, tmp_path / "xml.sqlite")
    pbf = _write_pbf(FIXTURE, tmp_path / "catalog.osm.pbf")
    pbf_catalog = build_catalog(pbf, tmp_path / "pbf.sqlite")

    assert [hike.id for hike in pbf_catalog.dayhikes] == [
        hike.id for hike in xml_catalog.dayhikes
    ]
    assert [hike.name for hike in pbf_catalog.dayhikes] == [
        hike.name for hike in xml_catalog.dayhikes
    ]
    assert pbf_catalog.ingest_report == xml_catalog.ingest_report


def test_build_catalog_merges_multiple_osm_files(tmp_path: Path) -> None:
    fixtures = Path(__file__).parent / "fixtures"
    catalog = build_catalog(
        [
            fixtures / "reversed_ways.osm",
            fixtures / "trailhead_at_parking.osm",
        ],
        tmp_path / "catalog.sqlite",
    )

    names = {hike.name for hike in catalog.dayhikes}
    assert names == {"Zig Zag", "Parking End Path"}
    assert catalog.ingest_report.kept == 2
