"""Capture golden outputs from the reference data, once, before a clean rebuild.

    uv run python tests/rebuild/capture_golden.py REFERENCE_DIR \
        --index data/osm/geofabrik-index-v1.json --valhalla-url http://127.0.0.1:8002

REFERENCE_DIR holds frozen copies of the old outputs (README "Clean rebuild"):
catalog.sqlite, catalog.geometry.sqlite, catalog.similarity/ and
valhalla/{valhalla_tiles.tar,file_hashes.txt}. They are only read. The
reference Valhalla serves the old highway tiles. Writes REFERENCE_DIR/golden/:

- rankings.tsv: top 50 per golden Seed id and weight set (ids, similarities);
- pairs.tsv: similarity and rank of the golden pairs (route and its reverse);
- countries.tsv: hikes per Geofabrik country and how many have each sampled value;
- summits.tsv: elevation_max_m of the famous-summit DayHikes;
- routes.tsv, locate.tsv: city routes and city snaps on the reference Valhalla;
- nearby_<origin>_<h>h.tsv: ``nearby --highways`` sets from three Origins;
- unreachable.tsv: DayHikes with no highway within 100 km;
- tiles.tsv: tile counts of the reference tar.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rebuild_checks as rc  # noqa: E402

from dayhike import Catalog, SimilarityStore  # noqa: E402

# Ids of the famous-summit DayHikes (tests/test_golden.py holds their published heights).
SUMMIT_IDS = (4004229, 19446684, 3360396, 15661826, 11340787, 2235103)
# (Seed, other, weight set): a route and its reverse, and the Ben Nevis ordering.
GOLDEN_PAIRS = (
    (17085818, 17085817, "geometry"),
    (17085819, 17085816, "geometry"),
    (4004229, 16127475, "altitude"),
    (4004229, 17991231, "altitude"),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("reference", type=Path)
    parser.add_argument("--index", type=Path, required=True, help="Geofabrik index-v1.json")
    parser.add_argument("--valhalla-url", default=rc.VALHALLA_URL)
    args = parser.parse_args(argv)
    reference: Path = args.reference
    golden = reference / "golden"
    golden.mkdir(exist_ok=True)
    catalog_path = reference / "catalog.sqlite"

    def say(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    # The reference store predates the Catalog digest; its ids and versions are checked here.
    store = SimilarityStore.open(catalog_path)
    sets = rc.golden_weight_sets()
    rows = []
    for name, weights in sets.items():
        for seed in rc.GOLDEN_SEEDS:
            for rank, (dayhike_id, similarity, raw) in enumerate(
                rc.top_matches(store, seed, weights), start=1
            ):
                rows.append((name, seed, rank, dayhike_id, repr(similarity), repr(raw)))
    rc.write_tsv(golden / "rankings.tsv", ("set", "seed", "rank", "dayhike_id", "similarity", "raw"), rows)
    pairs = []
    for seed, other, name in GOLDEN_PAIRS:
        similarity, raw, rank = rc.similarity_between(store, seed, other, sets[name])
        pairs.append((seed, other, name, repr(similarity), repr(raw), rank))
    rc.write_tsv(golden / "pairs.tsv", ("seed", "other", "set", "similarity", "raw", "rank"), pairs)
    say("golden: rankings and pairs")

    connection = rc.connect_ro(catalog_path, immutable=True)
    try:
        coverage, unassigned = rc.coverage_by_country(
            rc.europe_countries(args.index), rc.trailhead_coverage(connection)
        )
        placeholders = ",".join("?" * len(SUMMIT_IDS))
        summits = connection.execute(
            "SELECT d.id, d.name, f.elevation_max_m FROM dayhikes d JOIN feature_vectors f "
            f"ON f.dayhike_id = d.id WHERE d.id IN ({placeholders}) ORDER BY d.id",
            SUMMIT_IDS,
        ).fetchall()
    finally:
        connection.close()
    rc.write_tsv(
        golden / "countries.tsv",
        ("country", "hikes", "landcover", "climate", "elevation"),
        [*((name, c.hikes, c.landcover, c.climate, c.elevation) for name, c in coverage.items()),
         ("unassigned", unassigned, "", "", "")],
    )  # fmt: skip
    rc.write_tsv(golden / "summits.tsv", ("dayhike_id", "name", "elevation_max_m"), summits)
    say("golden: countries and summits")

    url = args.valhalla_url
    rc.write_tsv(
        golden / "routes.tsv",
        ("from", "to", "hours", "km"),
        ((a, b, *map(repr, rc.route(url, rc.CITIES[a], rc.CITIES[b]))) for a, b in rc.CITY_ROUTES),
    )
    rc.write_tsv(
        golden / "locate.tsv", ("city", "edges"), ((city, rc.locates(url, place)) for city, place in rc.CITIES.items())
    )
    catalog = Catalog.open(catalog_path)
    with tempfile.TemporaryDirectory() as tmp:
        snaps = Path(tmp) / "catalog.snaps.npz"
        for origin, place in rc.NEARBY_ORIGINS.items():
            for hours in rc.NEARBY_HOURS:
                found = rc.nearby_hours(catalog, url, place, hours, snaps)
                rc.write_tsv(
                    golden / f"nearby_{origin}_{hours}h.tsv",
                    ("dayhike_id", "hours"),
                    ((dayhike_id, repr(found[dayhike_id])) for dayhike_id in sorted(found)),
                )
                say(f"golden: nearby {origin} {hours} h: {len(found)}")
        rc.write_tsv(
            golden / "unreachable.tsv", ("dayhike_id",), ((i,) for i in rc.unreachable_ids(catalog, url, snaps))
        )
    tiles = rc.tile_counts_in_tar(reference / "valhalla" / "valhalla_tiles.tar")
    rc.write_tsv(golden / "tiles.tsv", ("key", "count"), sorted(tiles.items()))
    say(f"golden: wrote {golden}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
