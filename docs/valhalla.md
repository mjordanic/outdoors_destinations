# Local Valhalla for DriveTime

DriveTime is a query-time filter on an already-built Ranking. It is **not** a
FeatureVector field and it is **not** an isochrone polygon. The package calls
Valhalla's **`/sources_to_targets`** matrix: one Origin as the source,
Trailheads as targets, costing `auto`. Unreachable targets come back without a
time and are dropped.

Isochrones (`/isochrone`) may shade a later map. They are not how Ranking is
filtered.

Tiles and extracts belong under gitignored `data/valhalla/` (rebuild locally;
do not commit them). Ranking without `--origin` never talks to Valhalla.

## Is it installed? Is it running?

| Question | Answer |
| --- | --- |
| Does `uv sync` install Valhalla? | **No.** The Python package has no Valhalla dependency. |
| Is Valhalla a Python library in this repo? | **No.** `ValhallaMatrixClient` is an HTTP client (`urllib`) that POSTs JSON. |
| How do you get the engine? | Docker image `ghcr.io/gis-ops/docker-valhalla/valhalla:latest`. |
| Where does it run? | Container, port **8002** on localhost. Compose file: `data/valhalla/compose.yaml`. |
| When does `dayhike` use it? | Only `rank` with `--origin` and `--max-hours`, `nearby`, or `filter_by_drive_time` with `ValhallaMatrixClient`. |
| Do tests start it? | **No.** Tests stub the matrix client or stand up a tiny fake HTTP server. |

If nothing is listening on `127.0.0.1:8002`, ranking with `--origin` fails with
a URL error. Ranking without Origin still works.

## Car-only tiles

The graph is for **car DriveTime**, not walking, cycling, or public transit.

**`prepare-roads.sh`** (osmium-tool, host) drops buildings, POIs, landuse,
footways, paths, steps, pedestrian streets, cycleways, and driveways
(`highway=service` + `service=driveway`) from the PBF Valhalla parses. It
keeps motorway–residential, other `service` ways (parking aisles, alleys),
`highway=track` (Trailhead access), ferries, and turn restrictions.

**`prepare-highways.sh`** is the same idea, stricter: only `motorway`,
`trunk`, `primary` (and `_link` ramps) plus ferries. Output is
`*-highways.osm.pbf`. Use it for long-distance European travel, not for
DriveTime to Trailheads on its own; with `--highways` it gives an estimate for all
of Europe (README, **Highway tiles**). Turn restrictions
are omitted so osmium does not pull small member ways into the extract.

**`mjolnir.drive-time.json`** is merged into `valhalla.json` when the
container starts. `include_pedestrian`, `include_bicycle`, and
`include_driveways` are `false`, so leftover walk-only / bike-only / driveway
ways do not become edges.

To restore a fuller graph: skip both prepare scripts, copy an unfiltered
Geofabrik PBF into `data/valhalla/`, set those three flags to `true`, delete
`valhalla_tiles/`, `valhalla_tiles.tar`, and `file_hashes.txt`, then compose
up. README section **How to restore a fuller graph** is the canonical
checklist.

Changing flags alone does not rebuild existing tiles.

## What you put on disk

The compose file mounts **`data/valhalla`** (repo-relative) into the
container as `/custom_files`. On first start, the image looks in that
directory for `.osm.pbf` files, builds a road graph (tiles), and then serves
HTTP.

**Put exactly one `.osm.pbf` in `data/valhalla/`.** Valhalla warns (and can
build a bad graph) if it sees more than one extract
([valhalla#3925](https://github.com/valhalla/valhalla/issues/3925)). Leave
Croatia/Slovenia/Austria files in `data/osm/` for ingest; do not copy the
whole folder. Prefer a prepare script over copying the Geofabrik file
verbatim.

```
data/valhalla/          # gitignored except compose.yaml, prepare-*.sh,
                        # mjolnir.drive-time.json
  croatia-latest-roads.osm.pbf
  compose.yaml
  prepare-roads.sh
  prepare-highways.sh
  mjolnir.drive-time.json
  valhalla_tiles/          # the image writes these
  valhalla.json
```

Hiking-only filtered PBFs are a poor routing graph: DriveTime needs highways.

Croatia only:

```bash
./data/valhalla/prepare-roads.sh data/osm/croatia-latest.osm.pbf
```

Long-distance (motorway/trunk/primary only):

```bash
./data/valhalla/prepare-highways.sh data/osm/croatia-latest.osm.pbf
```

Several countries — merge first (`osmium-tool` CLI, not Python), then filter
**the merged file only**:

```bash
osmium merge \
  data/osm/croatia-latest.osm.pbf \
  data/osm/slovenia-latest.osm.pbf \
  data/osm/austria-latest.osm.pbf \
  -o data/osm/hr-si-at.osm.pbf

docker compose -f data/valhalla/compose.yaml down
./data/valhalla/prepare-roads.sh data/osm/hr-si-at.osm.pbf
rm -rf data/valhalla/valhalla_tiles data/valhalla/valhalla_tiles.tar \
  data/valhalla/file_hashes.txt
```

Then start (or recreate) the container so tiles rebuild from that one file.

## Docker

From the repo root:

```bash
mkdir -p data/valhalla
./data/valhalla/prepare-roads.sh data/osm/croatia-latest.osm.pbf

docker compose -f data/valhalla/compose.yaml up
```

The first start compiles tiles. That can take a long time and a few GB of RAM
for Alpine-scale extracts. Leave the container running. Later starts reuse
tiles in `data/valhalla/` unless you change the PBF set.

The service listens on `http://127.0.0.1:8002`. Confirm:

```bash
curl -s http://127.0.0.1:8002/status
```

Probe the same matrix API `dayhike` uses (seconds in `sources_to_targets[0][i].time`;
`null` means unreachable):

```bash
curl -s -H 'Content-Type: application/json' \
  -d '{"sources":[{"lat":45.8,"lon":15.97}],"targets":[{"lat":45.81,"lon":15.98}],"costing":"auto"}' \
  http://127.0.0.1:8002/sources_to_targets
```

Then rank:

```bash
uv run dayhike rank --catalog data/catalog.sqlite \
  --origin 45.8,15.97 --max-hours 2 \
  "Ridge Walk"
```

`--valhalla-url` overrides the base URL. The path posted is always
`/sources_to_targets`. Trailheads are sent in batches of 50.

To stop: `Ctrl+C` in the compose terminal, or
`docker compose -f data/valhalla/compose.yaml down`. Tiles stay in
`data/valhalla/`.

## How the package talks to it

`src/dayhike/valhalla.py` (`ValhallaMatrixClient`):

1. Takes today's Origin and the Trailheads still in the Ranking.
2. POSTs JSON `{sources, targets, costing: "auto"}` to
   `{base}/sources_to_targets`.
3. Reads `time` in **seconds** (or missing → `None`).
4. `filter_by_drive_time` converts seconds to hours, keeps `hours <= max`,
   drops `None` / non-finite / negative.

The Catalog SQLite file is not a routing database. Valhalla's tiles are a
separate graph built from a PBF. You can rank similarity with no tiles at all.

## Compose file

[`data/valhalla/compose.yaml`](../data/valhalla/compose.yaml) publishes port 8002,
mounts the same directory as `/custom_files`, and merges
`mjolnir.drive-time.json` into `valhalla.json` before the image entrypoint.
Extra image environment variables (`tile_urls`, `force_rebuild`, …) are
documented upstream: <https://github.com/gis-ops/docker-valhalla>.
