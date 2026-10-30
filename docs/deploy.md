# Deploying the web demo to Google Cloud Run

The GoFindMeAHike demo is one container: the FastAPI app (`dayhike serve`) and a
highways-only Valhalla for the drive-time filter, with the serving export baked
into the image. Nothing is written at run time; Cloud Run scales it to zero
when idle and starts it again on the next visit (a cold start of about 10-12 s).

The demo runs on Cloud Run today (the README links it). Every `gcloud`
command below creates or changes cloud resources and costs money, so run them
only once the deployment is approved.

## 1. Build the serving export (on the machine with the data)

The export needs the Catalog, its GeometryStore, its similarity directory
(`dayhike build-similarity`), and the highways-only Valhalla tiles (README,
**Highway tiles**). Start Valhalla on those tiles first, so every Trailhead is
snapped once at export time instead of on a visitor's query:

```bash
docker run -d --name valhalla-highways -p 127.0.0.1:8002:8002 \
  -v "$PWD/data/valhalla/highways-eu:/custom_files:ro" \
  --entrypoint /bin/bash ghcr.io/gis-ops/docker-valhalla/valhalla:latest -c \
  'jq -s ".[0] * .[1]" /custom_files/valhalla.json /custom_files/mjolnir.drive-time.json > /tmp/valhalla.json && exec valhalla_service /tmp/valhalla.json 2'

uv run dayhike export-serving --catalog data/catalog.sqlite --out data/web-serving \
  --valhalla-tiles data/valhalla/highways-eu --snaps data/catalog.snaps.npz \
  --valhalla-url http://127.0.0.1:8002 --geonames data/geonames/cities1000.zip
```

`--snaps` is optional (it starts from an existing snap cache). `--geonames`
writes `places.sqlite` for the page's "Find place" (ADR-0029); the file is
downloaded there when missing, so delete it before a refresh to fetch the
current GeoNames list. An export written without it falls back to public
Nominatim. To add place search to an export already built, without exporting
again, run `uv run dayhike export-places --out data/web-serving` (it reads
only `trailheads.npy` and leaves `meta.json` as it is), then rebuild the image. Rerun the
command after any rebuild of the Catalog, the similarity facts or the tiles:
it rewrites the directory and writes `meta.json` last, so a half-written export
never opens.

## 2. Run it locally

Same image as Cloud Run:

```bash
DAYHIKE_SERVING="$PWD/data/web-serving" docker compose -f web/compose.yaml up --build
open http://127.0.0.1:8080
```

Or without Docker (drive-time needs the Valhalla container from step 1):

```bash
uv sync --extra web
uv run dayhike serve --data data/web-serving --valhalla-url http://127.0.0.1:8002
uv run python web/smoke.py --data data/web-serving --valhalla-url http://127.0.0.1:8002
```

## 3. Deploy

Region `europe-west1` (Belgium). Replace `PROJECT` and `BILLING_ACCOUNT`.

`gcloud config set project PROJECT` would change the machine-wide default
project, so the commands below use `CLOUDSDK_CORE_PROJECT` instead (or pass
`--project PROJECT` on each command).

A billing account caps the number of projects linked to it (3 on the
account used for the first deployment), so linking a new project can fail with "Cloud billing quota
exceeded". Unlink a project you no longer need, or request a quota increase.

```bash
export CLOUDSDK_CORE_PROJECT=PROJECT
gcloud services enable run.googleapis.com artifactregistry.googleapis.com billingbudgets.googleapis.com
gcloud artifacts repositories create dayhike --repository-format=docker --location=europe-west1
gcloud auth configure-docker europe-west1-docker.pkg.dev   # edits ~/.docker/config.json

# the image is about 0.9 GB compressed, so the push takes a couple of minutes
IMAGE=europe-west1-docker.pkg.dev/PROJECT/dayhike/web:$(git rev-parse --short HEAD)
docker buildx build --platform linux/amd64 -f web/Dockerfile \
  --build-context serving=data/web-serving -t "$IMAGE" --push .

gcloud run deploy dayhike --image "$IMAGE" --region europe-west1 \
  --allow-unauthenticated \
  --cpu 1 --memory 2Gi --execution-environment gen2 --cpu-boost \
  --min-instances 0 --max-instances 2 --concurrency 8 --timeout 60 \
  --set-env-vars DAYHIKE_FORWARDED_HOPS=1
```

What the flags do:

| Flag | Why |
| --- | --- |
| `--memory 2Gi` | Measured peak of app plus Valhalla in the README table, **Web demo**; raise to 4Gi if the logs show out-of-memory restarts. |
| `--min-instances 0` | Scale to zero; the captain accepted the cold start. |
| `--max-instances 2` | The instance cap: at most two containers ever bill at once. |
| `--concurrency 8` | Searches and hike pages run side by side; Rankings queue one at a time per instance (in the app). |
| `--timeout 60` | No request may run longer; a 4 h drive query from the densest origin takes 23-31 s on 1 vCPU. |
| `DAYHIKE_FORWARDED_HOPS=1` | The rate limit keys on the last `X-Forwarded-For` entry, the one Google's front end adds. Earlier entries are client-supplied and ignored, so the limit cannot be dodged by sending a fake header. If that last entry turns out to be a Google proxy address rather than the visitor, the limit becomes one shared budget per instance: stricter, never looser. |
| `DAYHIKE_GOATCOUNTER=CODE` (optional) | Cookieless visit counts at `https://CODE.goatcounter.com`: every page loads the self-hosted `vendor/goatcounter/count.js`, and "Find" is counted as an event. Unset, no page loads analytics. Not set yet: no GoatCounter account. |
| `DAYHIKE_KOFI=HANDLE` (optional) | The Support page links `https://ko-fi.com/HANDLE`. Unset, it says the tip link is not set up yet. Not set yet: no Ko-fi handle. While FABDEM is in use, email Fathom / the University of Bristol about the donation link before setting it, unless the Copernicus swap has landed. |

## 4. Rate limit, instance cap and worst case

The app limits each client to **20 Rankings and 300 other API calls per
minute per instance** (`HTTP 429` past that; the counter lives in each
instance's memory, so with `--max-instances 2` a client whose requests reach
both instances gets up to 2x that), runs one Ranking at a time per instance (others
wait up to 30 s, then get `503`; a Valhalla call stalled past 50 s also ends
in `503` and frees the slot), and caps the drive filter at 4 hours. With
`--max-instances 2` the most the service can ever bill is two instances busy
around the clock: about 5.3 million vCPU-seconds and 10.5 million GiB-seconds a
month, roughly **$150 per month at list price** after the free tier (verify the
europe-west1 rates). Normal demo traffic stays inside the free tier
(180,000 vCPU-seconds and 360,000 GiB-seconds a month); the image in Artifact
Registry costs about $0.10 to $0.20 a month.

## 5. Budget alert

A budget does not stop spending; it emails the billing admins. Set it before
the first deploy. The project must already be linked to the billing account for
a project-scoped budget. The amount must be in the billing account's currency
(`gcloud billing budgets list` shows it); another currency fails with a bare
`INVALID_ARGUMENT`. This example uses `5USD`:

```bash
gcloud billing budgets create --billing-account=BILLING_ACCOUNT \
  --display-name="dayhike demo" --budget-amount=5USD \
  --filter-projects=projects/PROJECT \
  --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0
```

If an alert fires and the traffic is abuse, close the public access at once:
`gcloud run services remove-iam-policy-binding dayhike --region europe-west1 --member=allUsers --role=roles/run.invoker`.

## 6. Before the page goes public

- **ODbL offer.** The Data and credits page (`credits.html`) says the hike
  data is a derivative database of OpenStreetMap, offered under the ODbL, and
  links the code that builds it. The repository is public, with the code under
  MIT (`LICENSE`) and the data licences in `DATA-LICENSES.md`; keep it public,
  or change that page to offer the export another way (for example a download of `hikes.sqlite`).
- **Tile and place-search policies.** The map uses `tile.openstreetmap.org`,
  run by the OSM Foundation for low-volume use under its usage policy; if
  traffic grows, switch to a paid tile provider. Export with `--geonames` so
  the origin search reads `places.sqlite`; without it the page falls back to
  Nominatim, which allows at most one request a second over all visitors.
- **FABDEM is non-commercial (CC BY-NC-SA 4.0):** the demo must stay
  non-commercial (no ads, no paid tier).
  An export sampled from Copernicus GLO-30 instead (ADR-0027) lifts this
  limit for elevation only when every DEM step (`sample-dem`,
  `sample-trailgraph`, `build-similarity`) ran with the same Copernicus
  `--dem` over tiles covering every DayHike. Otherwise some values are still
  FABDEM: its credit and the non-commercial limit still apply, even though the
  page credits only Copernicus (it follows the last `sample-dem`).

## 7. Update or roll back

Rebuild the export and the image with a new tag, then `gcloud run deploy` again;
`gcloud run services update-traffic dayhike --region europe-west1 --to-revisions REVISION=100`
goes back to an earlier revision.
