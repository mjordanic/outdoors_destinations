# Local Valhalla for DriveTime

DriveTime is a query-time filter on an already-built Ranking. It is **not** a
FeatureVector field and it is **not** an isochrone polygon. The package calls
Valhalla's **`/sources_to_targets`** matrix: one Origin as the source, Trailheads
as targets, costing `auto`. Unreachable targets come back without a time and are
dropped.

Isochrones (`/isochrone`) may shade a later map. They are not how Ranking is
filtered.

Tiles and extracts belong under gitignored `data/valhalla/` (rebuild locally;
do not commit them). Ranking without `--origin` never talks to Valhalla.

## Docker

From the repo root, with a PBF in `data/valhalla/` as the image expects:

```bash
mkdir -p data/valhalla
docker compose -f docs/valhalla/compose.yaml up
```

The service listens on `http://127.0.0.1:8002`. The CLI uses that URL by
default:

```bash
uv run dayhike rank --catalog data/catalog.sqlite \
  --origin 45.8,15.97 --max-hours 2 \
  "Ridge Walk"
```

`--valhalla-url` overrides the base URL. The path posted is always
`/sources_to_targets`.
