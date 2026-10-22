import sqlite3
from pathlib import Path

import pytest

from dayhike import build_catalog, geometry_store_path_for

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
VALLEY_MEMBER = '<member type="way" ref="201" role=""/>'
VALLEY_RELATION = '<relation id="1002" version="1">'
VALLEY_LENGTH_M = 5_000.0


def _valley_with(tmp_path: Path, members: str, extra_ways: str = "") -> Path:
    text = FIXTURE.read_text()
    text = text.replace(VALLEY_MEMBER, members)
    text = text.replace(VALLEY_RELATION, extra_ways + VALLEY_RELATION)
    osm = tmp_path / "roles.osm"
    osm.write_text(text)
    return osm


def _ways(tmp_path: Path, osm: Path, dayhike_id: int = 1002):
    catalog_path = tmp_path / "catalog.sqlite"
    catalog = build_catalog(osm, catalog_path)
    connection = sqlite3.connect(geometry_store_path_for(catalog_path))
    try:
        rows = list(
            connection.execute(
                "SELECT way_id, in_chain, leftover FROM ways "
                "WHERE dayhike_id = ? ORDER BY seq",
                (dayhike_id,),
            )
        )
        vertices = connection.execute(
            "SELECT COUNT(*) FROM vertices WHERE dayhike_id = ?", (dayhike_id,)
        ).fetchone()[0]
    finally:
        connection.close()
    return catalog, rows, vertices


def _side_ways() -> str:
    # Ways touching the valley path's far end (node 11) at 1 km each.
    return """
  <node id="12" lat="45.2449660803" lon="15.1130000000" version="1"/>
  <node id="13" lat="45.2449660803" lon="15.1260000000" version="1"/>
  <node id="14" lat="45.2449660803" lon="15.1390000000" version="1"/>
  <way id="211" version="1"><nd ref="11"/><nd ref="12"/><tag k="highway" v="path"/></way>
  <way id="212" version="1"><nd ref="12"/><nd ref="13"/><tag k="highway" v="path"/></way>
  <way id="213" version="1"><nd ref="13"/><nd ref="14"/><tag k="highway" v="path"/></way>
"""


@pytest.mark.parametrize("role", ["alternative", "excursion", "connection"])
def test_a_member_with_a_variant_role_is_a_role_leftover(tmp_path: Path, role: str) -> None:
    members = (
        VALLEY_MEMBER + f'<member type="way" ref="211" role="{role}"/>'
    )
    osm = _valley_with(tmp_path, members, _side_ways())
    catalog, rows, vertices = _ways(tmp_path, osm)

    assert rows == [(201, 1, None), (211, 0, "role")]
    assert vertices == 2
    valley = next(hike for hike in catalog.dayhikes if hike.id == 1002)
    assert valley.length_m == pytest.approx(VALLEY_LENGTH_M, abs=1.0)


@pytest.mark.parametrize("role", ["approach", "forward", "backward", "", "main"])
def test_approach_forward_backward_and_other_roles_are_walked(
    tmp_path: Path, role: str
) -> None:
    members = VALLEY_MEMBER + f'<member type="way" ref="211" role="{role}"/>'
    osm = _valley_with(tmp_path, members, _side_ways())
    catalog, rows, _ = _ways(tmp_path, osm)

    assert rows == [(201, 1, None), (211, 1, None)]
    valley = next(hike for hike in catalog.dayhikes if hike.id == 1002)
    assert valley.length_m == pytest.approx(VALLEY_LENGTH_M + 1_000.0, abs=50.0)


def test_a_way_listed_four_times_counts_once(tmp_path: Path) -> None:
    osm = _valley_with(tmp_path, VALLEY_MEMBER * 4)
    catalog, rows, vertices = _ways(tmp_path, osm)

    assert rows == [
        (201, 1, None),
        (201, 0, "repeat"),
        (201, 0, "repeat"),
        (201, 0, "repeat"),
    ]
    assert vertices == 2
    valley = next(hike for hike in catalog.dayhikes if hike.id == 1002)
    assert valley.length_m == pytest.approx(VALLEY_LENGTH_M, abs=1.0)


def test_a_loop_then_a_side_trip_does_not_count_the_first_way_twice(tmp_path: Path) -> None:
    nodes = {"a": (45.0, 15.0), "b": (45.0, 15.02), "c": (45.02, 15.02),
             "d": (45.02, 15.0), "e": (45.01, 14.99)}
    ids = {name: 900 + i for i, name in enumerate(nodes)}
    way_ids = {}
    xml = ""
    for name, (lat, lon) in nodes.items():
        xml += f'<node id="{ids[name]}" lat="{lat}" lon="{lon}" version="1"/>\n'
    for pair in ["ab", "bc", "cd", "de", "ea", "be"]:
        way_ids[pair] = 950 + len(way_ids)
        xml += (
            f'<way id="{way_ids[pair]}" version="1"><nd ref="{ids[pair[0]]}"/>'
            f'<nd ref="{ids[pair[1]]}"/><tag k="highway" v="path"/></way>\n'
        )
    listing = ["ab", "bc", "cd", "de", "ea", "ab", "be"]
    members = "".join(
        f'<member type="way" ref="{way_ids[p]}" role=""/>' for p in listing
    )
    xml += (
        f'<relation id="1100" version="1">{members}<tag k="type" v="route"/>'
        '<tag k="route" v="hiking"/><tag k="name" v="Loop Plus"/></relation>\n'
    )
    osm = tmp_path / "loop.osm"
    osm.write_text(FIXTURE.read_text().replace("</osm>", xml + "</osm>"))
    catalog, rows, _ = _ways(tmp_path, osm, 1100)

    ab = way_ids["ab"]
    assert rows[0] == (ab, 1, None)
    assert rows[5] == (ab, 0, "repeat")
    # A piece without dead ends is walked whole until loops shed shortcuts
    # (ticket 04): b-e is walked once, ab still once.
    assert rows[6] == (way_ids["be"], 1, None)
    hike = next(hike for hike in catalog.dayhikes if hike.id == 1100)
    assert hike.length_m == pytest.approx(8_092.0 + 2_608.0, abs=5.0)
