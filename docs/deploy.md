# Deploying the web demo to Google Cloud Run

The HikesAlike demo is one container: the FastAPI app (`dayhike serve`) and a
highways-only Valhalla for the drive-time filter, with the serving export baked
into the image. The image is built in two parts: a data image (the pinned
Valhalla base plus the serving export at `/data`, `web/data.Dockerfile`), built
once per data refresh, and the web image (`web/Dockerfile`), which starts `FROM`
the data image and adds uv, the venv and the code. Nothing is written at run time; Cloud Run scales it to zero
when idle and starts it again on the next visit (a cold start of up to about 20 s).

The demo runs on Cloud Run today (the README links it; setup and measurements are in
[technical.md](technical.md)). Every `gcloud`
command below creates or changes cloud resources and costs money, so run them
only once the deployment is approved.

## 1. Build the serving export (on the machine with the data)

### Europe only, or Europe and the US

The site serves one export, so it shows the hikes of one Catalog. Two builds
are supported (ADR-0069); pick one before the steps below:

| Build | Ingest | `export-serving` | Cloud Run memory |
| --- | --- | --- | --- |
| **Europe only** | `dayhike ingest data/osm/europe-latest.osm.pbf` (paths level `graded`) | no `--region`, or `--region europe` | **4Gi** (section 3) |
| **Europe and the US** (the last rebuild) | `dayhike ingest data/osm/europe data/osm/us --paths data/osm/us=trails` | `--region europe --region us-48` | **4Gi** |

A US-only build is the same with only `data/osm/us` and `--paths trails`, and
`--region us-48`; it fits 2Gi. The deploy workflow sets the memory from the data
image's tag (section 9).

The export names the regions its hikes start in, in `meta.json`: without
`--region`, "Europe" for hikes east of 40° W and "the United States" for hikes
west of it (ADR-0074), and the pages, the region filter and the smoke test
follow. `--region` only labels them otherwise ("the contiguous United States",
"Colorado"); `export-serving` refuses, before writing anything, a `--region`
list that leaves out a region the hikes start in, or names one where none does.

- **Europe only** is every command in this file and in
  [technical.md](technical.md#clean-rebuild) as written. Nothing of the US is read or needed.
- **Europe and the US** is one Catalog of both. Keep each region's extracts in a
  directory of its own, so `--paths data/osm/us=trails` reads the US at the
  `trails` level, which builds its named-trail hikes, while Europe stays at
  `graded` and gets the same hikes as a Europe build; only names they share
  gain a place (ADR-0068). The raster steps take both regions, each covering
  its own box (CHELSA is clipped to the box around both):

  ```bash
  uv run dayhike fetch-extracts --region europe --dest data/osm/europe
  uv run dayhike fetch-extracts --region us-48 --dest data/osm/us      # 4 Geofabrik groups, the 48 states and DC
  uv run dayhike fetch-dem --region europe --region us-48 --source copernicus
  uv run dayhike fetch-landcover --region europe --region us-48
  uv run dayhike fetch-climate --region europe --region us-48
  uv run dayhike ingest data/osm/europe data/osm/us --paths data/osm/us=trails --dem data/dem --fresh
  # then sample-dem, sample-landcover, sample-climate, sample-trailgraph and build-similarity as for Europe
  ```

  Both regions must be read from one elevation map (export-serving refuses a
  mix), so a combined build is a Copernicus GLO-30 build. The highway tiles
  cover both: cut each region's highways, merge them, and build the tiles from the
  one file, as in [Highway tiles](technical.md#highway-tiles-all-of-europe-small-server):

  ```bash
  ./data/valhalla/prepare-highways.sh data/osm/europe/europe-latest.osm.pbf /tmp/highways/europe
  for f in data/osm/us/*-latest.osm.pbf; do
    ./data/valhalla/prepare-highways.sh "$f" "/tmp/highways/$(basename "$f" -latest.osm.pbf)"
  done
  mkdir -p data/valhalla/highways-eu-us
  osmium merge /tmp/highways/*/*.osm.pbf -o data/valhalla/highways-eu-us/europe-us-highways.osm.pbf
  # build the tiles in data/valhalla/highways-eu-us, then export with --region europe --region us-48
  ```

  The pages then say "across Europe and the contiguous United States". The
  preview's estimate for this build (2026-11-17, not yet built in one piece):
  an export of about 7.4 GB, a data image of about 4.2 GB compressed, and
  2.3–2.8 GB of app plus Valhalla at run time, hence 4Gi. Go back to Europe
  alone by deploying a Europe data image.

The export needs the Catalog, its GeometryStore, its similarity directory
(`dayhike build-similarity`), and the highways-only Valhalla tiles ([technical docs](technical.md#highway-tiles-all-of-europe-small-server),
**Highway tiles**). Start Valhalla on those tiles first, so every Trailhead is
snapped once at export time instead of on a visitor's query:

```bash
docker run -d --name valhalla-highways -p 127.0.0.1:8002:8002 \
  -v "$PWD/data/valhalla/highways-eu:/custom_files:ro" \
  --entrypoint /bin/bash ghcr.io/gis-ops/docker-valhalla/valhalla:latest@sha256:060da5b92e6024a67f65135c236d918b021d63e73d1808a0d55b7e7cbd17240c -c \
  'jq -s ".[0] * .[1]" /custom_files/valhalla.json /custom_files/mjolnir.drive-time.json > /tmp/valhalla.json && exec valhalla_service /tmp/valhalla.json 2'

uv run dayhike export-serving --catalog data/catalog.sqlite --out data/web-serving \
  --valhalla-tiles data/valhalla/highways-eu --snaps data/catalog.snaps.npz \
  --valhalla-url http://127.0.0.1:8002 --geonames data/geonames/allCountries.zip
```

`--snaps` is optional (it starts from an existing snap cache). `--geonames`
writes `places.sqlite` for the page's "Find place" (ADR-0029, ADR-0042); the file (about 420 MB) is
downloaded there when missing, so delete it before a refresh to fetch the
current GeoNames list. An export written without it falls back to public
Nominatim. To add place search to an export already built, without exporting
again, run `uv run dayhike export-places --out data/web-serving` (it reads
only `trailheads.npy` and `hikes.sqlite` and leaves `meta.json` as it is; it also writes
`landmarks.sqlite`, the peaks, huts, lakes and towns the hike search finds hikes by, ADR-0053), then rebuild the image.

The export also writes `names.npz`, the name search's index, so a server starts
without reading every hike name from the 2.2 GB `hikes.sqlite` (about 15 s of
one CPU for 890,000 hikes, the most of a cold start). An export written before
it still opens: the server builds the same index at start. Add it to such an
export, without exporting again, with `uv run dayhike export-name-index --out data/web-serving`
(about 13 s; it reads only `hikes.sqlite` and `similarity/ids.npy`), then
rebuild the data image. The index records the hikes it was written for: a
server ignores one of other hikes and builds its own, so rerun the command on an
export whose hikes changed. Rerun the
command after any rebuild of the Catalog, the similarity facts or the tiles:
it rewrites the directory and writes `meta.json` last, so a half-written export
never opens.

## 2. Run it locally

Same images as Cloud Run (compose builds the data image first, then the web
image on it):

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

# cleanup policy: keep the last 9 versions (3 pushes), delete the rest once older than 30 days;
# never delete a data image (every web image is built on one)
cat > /tmp/ar-cleanup.json <<'EOF'
[
  {"name": "keep-recent-releases", "action": {"type": "Keep"},
   "mostRecentVersions": {"keepCount": 9}},
  {"name": "keep-data-images", "action": {"type": "Keep"},
   "condition": {"packageNamePrefixes": ["data"]}},
  {"name": "delete-older-than-30d", "action": {"type": "Delete"},
   "condition": {"tagState": "ANY", "olderThan": "30d"}}
]
EOF
gcloud artifacts repositories set-cleanup-policies dayhike --location=europe-west1 \
  --policy=/tmp/ar-cleanup.json --dry-run      # then again with --no-dry-run

# the data image, once per data refresh: 1 to 2.4 GB compressed, a push of a couple of minutes
REPO=europe-west1-docker.pkg.dev/PROJECT/dayhike
# data_tag is the export's facts_version and its elevation map(s), e.g. 3fa9…-fabdem or 3fa9…-copernicus-glo30, so the
# same code on another DEM is another tag and the live image is kept for a rollback
DATA_IMAGE=$REPO/data:$(jq -r .data_tag data/web-serving/meta.json)
docker buildx build --platform linux/amd64 -f web/data.Dockerfile \
  --build-context serving=data/web-serving -t "$DATA_IMAGE" --push .

# the web image, on every code change: only the layers above the data image are pushed
IMAGE=$REPO/web:$(git rev-parse --short HEAD)
docker buildx build --platform linux/amd64 -f web/Dockerfile \
  --build-arg DATA_IMAGE="$DATA_IMAGE" -t "$IMAGE" --push .

gcloud run deploy dayhike --image "$IMAGE" --region europe-west1 \
  --allow-unauthenticated \
  --cpu 1 --memory 4Gi --execution-environment gen2 --cpu-boost \
  --min-instances 0 --max-instances 2 --concurrency 8 --timeout 60 \
  --set-env-vars DAYHIKE_FORWARDED_HOPS=1,DAYHIKE_SITE_URL=https://SERVICE.run.app
```

For the live site, `DAYHIKE_SITE_URL` is `https://hikesalike.com`.

Artifact Registry storage is not covered by the Cloud Run spend cap (section 5),
and each push of a data image adds 1 to 2.4 GB, hence the cleanup policy. A web
image shares the data image's layers, so pushing one adds only its code layers
(section 9 has the sizes). The policy keeps every data image; delete one by hand
once no kept web image is built on it. `docker buildx` pushes
three versions per tag: an image index (the tagged one), the `linux/amd64`
image and an attestation. Cloud Run runs the untagged `linux/amd64` digest.
Cleanup never deletes an image that a kept index references, but it deletes the
children once their index goes. So `keepCount` counts versions, not tags: 9
keeps at least the last three pushes, plus every push from the last 30 days
(the Delete rule only matches versions older than that). Keep it a multiple of three.
Rolling back (section 7) only works to an image that is still kept. The
policy runs in a daily background job. `--dry-run` deletes nothing, but its
would-be deletions show only in the Data Access audit logs, which are off
on this project.

What the flags do:

| Flag | Why |
| --- | --- |
| `--memory 4Gi` | For a Europe or a Europe and US export (section 1), the most Cloud Run gives 1 vCPU; a US-only export fits `2Gi`. The deploy workflow sets it on every deploy from the data image's tag: `2Gi` for a tag ending in `-us` (but not `-eu-us`) or `-demo`, else `4Gi`, or the `DAYHIKE_MEMORY` repository variable when set (section 9). Measured peak of app plus Valhalla in the technical docs table, **Web demo measurements** ([technical.md](technical.md#web-demo-measurements)). The trail network for the way back is memory-mapped from the export's `trailgraph.network/` (an export without it builds it once per instance, a request arriving during the build waiting for it), and the image sets `MALLOC_ARENA_MAX=2`; with both, 8 visitors on 1 vCPU peak near 1.1 GB. Raise to 4Gi if the logs show out-of-memory restarts. |
| `--min-instances 0` | Scale to zero; the captain accepted the cold start. |
| `--max-instances 2` | The instance cap: at most two containers ever bill at once. |
| `--concurrency 8` | Searches and hike pages run side by side; Rankings queue one at a time per instance (in the app). |
| `--timeout 60` | No request may run longer; a 4 h drive query from the densest origin takes 23-31 s on 1 vCPU. |
| `DAYHIKE_FORWARDED_HOPS=1` | The rate limit keys on the last `X-Forwarded-For` entry, the one Google's front end adds. Earlier entries are client-supplied and ignored, so the limit cannot be dodged by sending a fake header. If that last entry turns out to be a Google proxy address rather than the visitor, the limit becomes one shared budget per instance: stricter, never looser. |
| `DAYHIKE_SITE_URL=https://HOST` (optional) | The site's public address (the live site's is `https://hikesalike.com`; before a custom domain, the Cloud Run URL). Each page then names itself in `og:url` and its preview image by full address, so a shared link shows the preview, and `/robots.txt` and `/sitemap.xml` name pages at it; unset, the image address stays relative and most link previews show none. |
| `DAYHIKE_GOATCOUNTER=CODE` (optional) | Cookieless visit counts at `https://CODE.goatcounter.com`: every page loads the self-hosted `vendor/goatcounter/count.js`, and "Find" is counted as an event. Unset, no page loads analytics. Not set yet: no GoatCounter account. |
| `DAYHIKE_CORS_ORIGINS="https://HOST ..."` (optional) | The origins whose pages may call the API (CORS): the loading shell of section 8. Unset, no other origin may. The deploy workflow sets it from the repository variable of that name (section 9). |
| `DAYHIKE_TILE_URL=TEMPLATE` (optional) | The raster tile URL template both maps load their base map from, with `{z}`, `{x}` and `{y}` (for example `https://tiles.example.com/{z}/{x}/{y}.png`). Unset, the maps use `tile.openstreetmap.org` (section 6). `/api/info` names it as `basemap`. |
| `DAYHIKE_TILE_ATTRIBUTION=HTML` (optional) | The tile provider's credit, as its terms ask for it: the map corner shows it beside "© OpenStreetMap contributors", which always stays, and the Data and credits page says the tiles are served by it. |
| `DAYHIKE_TILE_MAX_ZOOM=N` (optional) | The deepest zoom the provider serves (default 19). |
| `DAYHIKE_PMTILES_URL=URL` (optional) | A vector base map: a PMTiles file in the [Protomaps basemap](https://docs.protomaps.com/basemaps/downloads) schema, by full address or relative to the page. Both maps then draw it with MapLibre GL JS under their overlays (ADR-0061), credited "© OpenStreetMap contributors · Protomaps", and ask no raster tiles; where the vector map cannot be shown (no WebGL 2, or the file or its tiles cannot be read, for example blocked by CORS), each map falls back to the raster tiles above for the rest of the page's life. A map whose view lies wholly outside the file's bounds (a US hike on a Europe file) draws the raster tiles over the vector map until it is moved back over the file, so one Europe file serves a Europe and US site. The host must answer range requests and, on another origin, allow the site's origin by CORS with the `range` request header and the `etag` response header exposed ([Protomaps cloud storage guide](https://docs.protomaps.com/pmtiles/cloud-storage)). The live site's file is `https://maps.hikesalike.com/europe.pmtiles` on Cloudflare R2 (section 10); the deploy workflow sets it from the `DAYHIKE_PMTILES_URL` repository variable (section 9). |
| `DAYHIKE_DOWNLOAD_URL=URL` (optional) | Where the ODbL download of the OpenStreetMap part of the hikes is hosted (section 11). The Data and credits page then shows a "Download the data" link to it, saying what the file holds and leaves out; unset or empty, no link; a localhost or loopback address (for example `http://127.0.0.1:8081/...` from a local test) is ignored with a warning in the log, since only that machine could open it. The live site's file is `https://maps.hikesalike.com/hikes-osm.sqlite`; the deploy workflow sets it from the `DAYHIKE_DOWNLOAD_URL` repository variable (section 9). |

The service also carries a service-level `scaling.maxInstanceCount` of 20 in
the Cloud Run v2 API, next to the revision-level `template.scaling.maxInstanceCount`
of 2 that `--max-instances 2` sets. Nobody set the 20 on purpose. The revision
cap of 2 is the one that limits instances, so the 20 is harmless today; check
both after any change to scaling:

```bash
curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  https://run.googleapis.com/v2/projects/PROJECT/locations/europe-west1/services/dayhike \
  | jq '{service: .scaling, revision: .template.scaling}'
```

## 4. Rate limit, instance cap and worst case

The app limits each client to **20 Rankings and 300 other API calls per
minute per instance** (`HTTP 429` past that; the counter lives in each
instance's memory, so with `--max-instances 2` a client whose requests reach
both instances gets up to 2x that), runs one Ranking at a time per instance, in
the order they came (others wait up to 25 s, then get `503`; with the slowest
4 h drive Ranking of about 31 s that still ends inside the 60 s request timeout,
so a visitor gets the app's `503` rather than Cloud Run's `504`; a Valhalla call
stalled past 50 s also ends in `503` and frees the slot), lets a client (a browser
tab at an address) have one Ranking queued or running at a time, and an address
at most 4 (the next gets `409` until one ends, so one client cannot hold the
Ranking for everyone, and people behind one address do not block each other), refuses a request body over 256 KB unread (`413`), and caps the drive filter at 4 hours. With
`--max-instances 2` the most the service can ever bill is two instances busy
around the clock: about 5.3 million vCPU-seconds and 10.5 million GiB-seconds a
month, roughly **$150 per month at list price** after the free tier (verify the
europe-west1 rates). Normal demo traffic stays inside the free tier
(180,000 vCPU-seconds and 360,000 GiB-seconds a month); the images in Artifact
Registry cost about $0.10 per GB-month past the 0.5 GB free tier. The cleanup
policy keeps at least the last three pushes plus every push from the last 30
days, 1 to 2.4 GB each: about $0.25 to $0.70 a month for three pushes, plus
$0.10 to $0.24 for each further push in the last 30 days.

## 5. Budget, spend cap and capacity alert

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

### Spend cap

A spend cap is a budget that does stop spending: past 100 % Google pauses new
Cloud Run usage in the project, so **the site goes down** until the 1st of the
next month or until someone lifts the cap (Billing → Budgets & alerts → "Lift
spend cap"; Cloud Run can take up to 1 h to recover). Requests already running
finish and are billed, so a small overage is possible. It covers Cloud Run only:
Artifact Registry, Logging and Monitoring are not paused. The budget cannot be
edited or deleted while the cap is enforced. Docs:
https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps.

`gcloud` has no spend-cap flag, so create it with the Budget API. Google
requires exactly one project and one service, `EXCLUDE_ALL_CREDITS`, a calendar
month, thresholds of exactly 0.5, 0.8 and 1.0 and project-level recipients only.
`services/152E-C115-5142` is Cloud Run. `PROJECT_NUMBER` is the number, not the
ID (`gcloud projects describe PROJECT --format='value(projectNumber)'`). The
`x-goog-user-project` header bills the call to PROJECT, which must have the
Budget API enabled (above). The deployed project has a $10 cap, "dayhike cloud
run spend cap", next to the $5 alert budget:

```bash
curl -s -X POST \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "x-goog-user-project: PROJECT" -H "Content-Type: application/json" \
  https://billingbudgets.googleapis.com/v1/billingAccounts/BILLING_ACCOUNT/budgets \
  -d '{
    "displayName": "dayhike cloud run spend cap",
    "budgetFilter": {
      "projects": ["projects/PROJECT_NUMBER"],
      "services": ["services/152E-C115-5142"],
      "creditTypesTreatment": "EXCLUDE_ALL_CREDITS",
      "calendarPeriod": "MONTH"
    },
    "amount": {"specifiedAmount": {"currencyCode": "USD", "units": "10"}},
    "thresholdRules": [
      {"thresholdPercent": 0.5, "spendBasis": "CURRENT_SPEND"},
      {"thresholdPercent": 0.8, "spendBasis": "CURRENT_SPEND"},
      {"thresholdPercent": 1.0, "spendBasis": "CURRENT_SPEND"}
    ],
    "notificationsRule": {"enableProjectLevelRecipients": true},
    "spendCap": {"inputState": "CONFIGURED"}
  }'
gcloud billing budgets list --billing-account=BILLING_ACCOUNT --billing-project=PROJECT
```

The list shows `CONFIGURED` under `OUTPUT_STATE` for the cap. The cap counts
gross cost and ignores credits, so it may trip before the invoice reaches $10.

### Capacity alert

An email to the captain when the service runs at its instance cap or turns
visitors away. It is free today; Cloud Monitoring starts charging for alerting
no sooner than 2027-09-01, about $0.35 a month per condition. First the email
channel (the deployed one is "Captain email"; `EMAIL` is the captain's
address), then the policy:

```bash
gcloud services enable monitoring.googleapis.com
CHANNEL=$(curl -s -X POST \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "x-goog-user-project: PROJECT" -H "Content-Type: application/json" \
  https://monitoring.googleapis.com/v3/projects/PROJECT/notificationChannels \
  -d '{"type": "email", "displayName": "Captain email", "labels": {"email_address": "EMAIL"}}' \
  | jq -r .name)

cat > /tmp/dayhike-alert.json <<'EOF'
{
  "displayName": "dayhike traffic near capacity",
  "combiner": "OR",
  "alertStrategy": {"autoClose": "86400s"},
  "conditions": [
    {
      "displayName": "dayhike at max instances (2) for 5 min",
      "conditionThreshold": {
        "filter": "resource.type = \"cloud_run_revision\" AND resource.labels.service_name = \"dayhike\" AND metric.type = \"run.googleapis.com/container/instance_count\"",
        "aggregations": [{"alignmentPeriod": "60s", "perSeriesAligner": "ALIGN_MAX",
          "crossSeriesReducer": "REDUCE_SUM", "groupByFields": ["resource.label.service_name"]}],
        "comparison": "COMPARISON_GT", "thresholdValue": 1.5,
        "duration": "300s", "trigger": {"count": 1}
      }
    },
    {
      "displayName": "dayhike >10 HTTP 429/503 in 5 min",
      "conditionThreshold": {
        "filter": "resource.type = \"cloud_run_revision\" AND resource.labels.service_name = \"dayhike\" AND metric.type = \"run.googleapis.com/request_count\" AND metric.labels.response_code = one_of(\"429\", \"503\")",
        "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_SUM",
          "crossSeriesReducer": "REDUCE_SUM", "groupByFields": ["resource.label.service_name"]}],
        "comparison": "COMPARISON_GT", "thresholdValue": 10,
        "duration": "0s", "trigger": {"count": 1}
      }
    }
  ],
  "documentation": {"mimeType": "text/markdown", "content": "Cloud Run service dayhike (europe-west1) is near its capacity: either it ran at its max of 2 instances for 5 minutes, or it returned more than 10 HTTP 429/503 responses in 5 minutes (429 = app rate limit or Cloud Run had no free instance; 503 = Ranking queue full). If this is abuse, close public access: gcloud run services remove-iam-policy-binding dayhike --region europe-west1 --project PROJECT --member=allUsers --role=roles/run.invoker"}
}
EOF
gcloud monitoring policies create --policy-from-file=/tmp/dayhike-alert.json \
  --notification-channels="$CHANNEL"
```

The first condition fires when both instances run for 5 minutes. The second
fires on more than 10 `429` or `503` answers in 5 minutes (section 4: the rate
limit, no free instance, a full Ranking queue or a stalled Valhalla call). Your
own rate-limit tests and the overlap of two revisions during a deploy can
trigger it; the incident closes itself after 24 h.

If an alert fires and the traffic is abuse, close the public access at once:
`gcloud run services remove-iam-policy-binding dayhike --region europe-west1 --member=allUsers --role=roles/run.invoker`.

## 6. Before the page goes public

- **ODbL offer.** The Data and credits page (`credits.html`) says the hike
  data is a derivative database of OpenStreetMap, offered under the ODbL, and
  links the code that builds it. The repository is public, with the code under
  MIT (`LICENSE`) and the data licences in `DATA-LICENSES.md`; keep it public,
  or change that page to offer the export another way (for example a download of `hikes.sqlite`).
- **Tile and place-search policies.** The map uses `tile.openstreetmap.org`
  (only as the fallback where the vector map cannot be shown once it is set, section 10), run by the OSM Foundation for low-volume use under its usage policy; if
  traffic grows, switch to another tile provider with `DAYHIKE_TILE_URL` and
  `DAYHIKE_TILE_ATTRIBUTION` (section 3). Export with `--geonames` so
  the origin search reads `places.sqlite`; without it the page falls back to
  Nominatim, which allows at most one request a second over all visitors.
- **Privacy page.** `privacy.html` names the host (Google Cloud Run,
  `europe-west1`), says the request logs are kept by Google Cloud Logging in
  Google's default storage location (not pinned to the EU), the 30-day log
  retention, the map tile and place-search
  providers (Cloudflare for the vector map once `DAYHIKE_PMTILES_URL` is set,
  section 10), and GoatCounter once it is set. Change it with any of
  these: a new host or a CDN in front (the `host-recipient` item), a shorter
  log retention, or a different tile or place-search provider.
- **FABDEM is non-commercial (CC BY-NC-SA 4.0):** the demo must stay
  non-commercial (no ads, no paid tier).
  An export sampled from Copernicus GLO-30 instead (ADR-0030) lifts this
  limit for elevation only when every DEM step (`ingest`, `sample-dem`,
  `sample-trailgraph`, `build-similarity`) ran with the same Copernicus
  `--dem` over tiles covering every DayHike. `export-serving` refuses a build
  whose steps recorded different sources, and its `meta.json` lists the one
  each read (`dem_read`). The credit follows each hike's `dem_source`, which
  only `sample-dem` records: a Catalog built before the steps recorded their
  source is not checked, so rebuild it before relying on the credit.

## 7. Update or roll back

A code change needs only a new web image: build it on the current data image
(section 3) and `gcloud run deploy` again, or let the deploy workflow do it
(section 9). A data refresh rebuilds the export, then the data image with the
new `data_tag` (`facts_version` and the DEM: a Copernicus data image never takes
the tag of the FABDEM one), then the web image on it. Keep the previous data
image when the DEM changes: a rollback is one traffic change. Every data refresh
also rebuilds and uploads the ODbL download (section 11), so it matches the data
the site serves.
`gcloud run services update-traffic dayhike --region europe-west1 --to-revisions REVISION=100`
goes back to an earlier revision (`gcloud run revisions list --service dayhike --region europe-west1`
lists them); the image it runs must still be kept (section 3). That pins the
traffic to the revision: a later `gcloud run deploy` by hand gets none until
`gcloud run services update-traffic dayhike --region europe-west1 --to-latest`
(the deploy workflow does this itself).

## 8. A loading shell on a static host

With `--min-instances 0` the first visit after a quiet spell waits for the
container to start, and while Cloud Run serves the page too, the browser shows
nothing until then. A shell on a static host (Cloudflare Pages) serves the
page's files at once, so the loading screen (the logo, then "Waking the
server…") shows while Cloud Run starts, and the page then calls the API on
Cloud Run. What is built for it:

- `index.html` holds `<meta name="api-base" content="">`; empty, the page asks
  its own origin. In the shell's copy it names the Cloud Run URL.
- `dayhike serve --cors-origin` (`DAYHIKE_CORS_ORIGINS`) lets the shell's
  origins call the API. CORS sits outside the rate limit: a preflight uses none
  of a client's budget, and a `429` is readable by the shell.
- The loading screen says the site is paused when the server answers `401` or
  `403` (Cloud Run with public access closed, section 5). Across origins the
  shell cannot read an answer without CORS headers (that `403`, or a `429` or
  `503` from Cloud Run's front end), so the page asks `/api/health` once in
  `no-cors` mode: any answer says the site is paused or busy, no answer that
  the server is down.
- `web/shell.py --url https://SERVICE.run.app --out SHELL_DIR` writes the
  shell: the pages and `robots.txt` and `sitemap.xml` fetched from the running
  server, so they hold what it fills in (the regions and hike count of its data,
  the GoatCounter tag, the base map, `og:url` with `DAYHIKE_SITE_URL`), the
  other static files, a `404.html`, and a `_headers` file with the headers the
  server sends with its pages (`Cache-Control: no-cache`, `nosniff`, the
  referrer policy), with `index.html` pointed at the server.
- The deploy workflow (section 9) publishes it after every deploy, once the
  `CLOUDFLARE_PAGES_PROJECT` variable is set, so the shell and the API stay
  the same version; it sets `DAYHIKE_CORS_ORIGINS` from the variable of that
  name.

The one-time setup, by the maintainer, once there is a host account:

1. Create the Cloudflare Pages project (direct upload, production branch
   `main`), and an API token that may edit Cloudflare Pages on that account.
2. Set the repository variables `CLOUDFLARE_PAGES_PROJECT` and
   `CLOUDFLARE_ACCOUNT_ID`, the secret `CLOUDFLARE_API_TOKEN`, and
   `DAYHIKE_CORS_ORIGINS` to every host the shell is served at, such as
   `https://hikesalike.com https://PROJECT.pages.dev` (space-separated).
3. Run the workflow; check the shell at its `*.pages.dev` address.
4. Point the custom domain at the Pages project, and keep `DAYHIKE_SITE_URL`
   on the custom domain.

By hand, without the workflow: deploy with `DAYHIKE_CORS_ORIGINS` set, then

```bash
python3 web/shell.py --url https://SERVICE.run.app --out "$(mktemp -d)/shell"
npx wrangler pages deploy SHELL_DIR --project-name PROJECT --branch main
```

## 9. Deploy from GitHub Actions (not set up yet)

**Not set up.** `.github/workflows/deploy.yml` is written, but the cloud identity
and the repository variables below do not exist yet, so its deploy job is
skipped. Nothing below has been run.

On a push to `main`, or by hand (`workflow_dispatch` on `main`), the workflow:

1. runs the tests (`ci.yml`, called as a reusable workflow);
2. authenticates with Workload Identity Federation
   (`google-github-actions/auth`): the run's GitHub OIDC token is traded for a
   short-lived Google token, so no key or secret is stored anywhere;
3. builds the web image in Cloud Build (`web/cloudbuild.yaml`) on top of the
   current data image, tagged with the commit SHA. Cloud Build pushes one version per image, not the three of
   `docker buildx` (section 3), so the cleanup policy keeps the last 9 deploys;
4. deploys it as a new revision with `--no-traffic` and a `c-<sha>` tag, which
   gives the candidate its own URL while visitors stay on the current revision;
5. runs `web/smoke.py --url` against that URL. A failure stops the job here:
   the candidate keeps its tag and no traffic, and the live revision is untouched;
6. checks that the candidate is still the latest revision, sends all traffic to
   the latest revision (so a later `gcloud run deploy` by hand, section 7, takes
   the traffic as usual), then removes the candidate's tag;
7. with `CLOUDFLARE_PAGES_PROJECT` set, publishes the loading shell of the
   service (section 8) to Cloudflare Pages.

The smoke test follows the data: it reads the regions the candidate has hikes
in (`/api/info`), checks that the page names them (only a site of Europe alone
says "Europe"), and searches, opens and ranks the known hikes of each: in
Slovenia and Switzerland for Europe, in California, Colorado and Utah for the
United States, with drives from Ljubljana, Munich and Denver when the candidate
has drive time.

The deploy changes only the image, the memory, `DAYHIKE_PMTILES_URL`,
`DAYHIKE_DOWNLOAD_URL` and `DAYHIKE_CORS_ORIGINS`, which it sets from the
repository variables of those names (empty, the variable is set empty: the maps
keep the raster tiles, the credits page shows no download link, and no other
origin may call the API). The memory is `DAYHIKE_MEMORY`, or, unset, `2Gi` for
a data image whose tag ends in `-us` (but not `-eu-us`) or `-demo` and `4Gi`
for any other, `-eu-us` included. CPU,
scaling and the other environment variables stay as section 3 set them. A data refresh is still built and pushed by
hand (section 3), followed by a change of `DAYHIKE_DATA_IMAGE`; the next run
builds on it. To roll back, move traffic to an earlier revision as in section 7,
or re-run the workflow on an earlier commit. A failed candidate can be removed
with `gcloud run services update-traffic dayhike --region europe-west1 --remove-tags c-SHA`.

The workflow never runs on pull requests, and must never be given a
`pull_request` or `pull_request_target` trigger: the repository is public, and a
fork's pull request must never reach the deploy credentials. The identity
provider below accepts only tokens for this repository's `main`.

### Repository variables

Set as repository variables, not environment variables: the job's `if` reads
`GCP_WORKLOAD_IDENTITY_PROVIDER` before any environment applies. None is a
secret; the one secret is `CLOUDFLARE_API_TOKEN`, for the loading shell. The deploy job runs in the `production` environment, which can require a
reviewer before each deploy.

| Variable | Example form |
| --- | --- |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github` |
| `GCP_SERVICE_ACCOUNT` | `DEPLOY_SA@PROJECT.iam.gserviceaccount.com` |
| `GCP_PROJECT` | `PROJECT` |
| `GCP_REGION` | `europe-west1` |
| `CLOUD_RUN_SERVICE` | `dayhike` |
| `DAYHIKE_IMAGE_REPO` | `europe-west1-docker.pkg.dev/PROJECT/dayhike` |
| `DAYHIKE_DATA_IMAGE` | `europe-west1-docker.pkg.dev/PROJECT/dayhike/data:DATA_TAG` |
| `DAYHIKE_PMTILES_URL` (optional) | `https://maps.hikesalike.com/europe.pmtiles` (section 10); unset or empty, the raster tiles |
| `DAYHIKE_DOWNLOAD_URL` (optional) | `https://maps.hikesalike.com/hikes-osm.sqlite` (section 11); unset or empty, no download link |
| `DAYHIKE_MEMORY` (optional) | `4Gi`; unset or empty, from the data image's tag (`2Gi` for `-us`, not `-eu-us`, or `-demo`; else `4Gi`) |
| `DAYHIKE_CORS_ORIGINS` (optional) | `https://hikesalike.com https://PROJECT.pages.dev` (section 8); unset or empty, none |
| `CLOUDFLARE_PAGES_PROJECT` (optional) | the Pages project of the loading shell (section 8); unset or empty, no shell is published |
| `CLOUDFLARE_ACCOUNT_ID` (with the project) | the Cloudflare account's id |

### One-time cloud setup (by the maintainer)

`OWNER/REPO` is this repository, `DEPLOY_SA` a new service account for the
workflow, `RUNTIME_SA` the account the Cloud Run service runs as. Cloud Build
bills build minutes past its free tier.

```bash
export CLOUDSDK_CORE_PROJECT=PROJECT
gcloud services enable cloudbuild.googleapis.com iamcredentials.googleapis.com sts.googleapis.com

gcloud iam workload-identity-pools create github --location=global
gcloud iam workload-identity-pools providers create-oidc github --location=global \
  --workload-identity-pool=github --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping=google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref \
  --attribute-condition="assertion.repository == 'OWNER/REPO' && assertion.ref == 'refs/heads/main'"

gcloud iam service-accounts create DEPLOY_SA
gcloud iam service-accounts add-iam-policy-binding DEPLOY_SA@PROJECT.iam.gserviceaccount.com \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/OWNER/REPO"
```

Then grant `DEPLOY_SA` the least it needs, and check each role against the
current Google documentation before granting it: submit builds
(`roles/cloudbuild.builds.editor`), upload the build source to the Cloud Build
bucket (`roles/storage.objectAdmin` on `gs://PROJECT_cloudbuild`), read the
build log it streams (`roles/logging.viewer`), deploy revisions and move traffic
(`roles/run.developer`), and act as the runtime account
(`roles/iam.serviceAccountUser` on `RUNTIME_SA`). The account Cloud Build runs
as needs `roles/artifactregistry.writer` on the `dayhike` repository, to pull
the data image and push the web image.

### What a code-only deploy pushes

The data image is the Valhalla base plus the serving export; the web image adds
the uv binary, the dependency layer, the code and `start.sh` on top of it. The
split adds no layer and no file: the image is the same size as before, and the
container runs the same files, so its memory is unchanged. What changes is the
order. Before, the export was copied after the code, so every code change made a
new export layer (6.0 GB unpacked in the image deployed before the split, 1 to
2.4 GB compressed) that was pushed again. Now a code-only change rebuilds only
the code layers. Measured on the test-fixture export with a local registry:

| Push | New layers | Compressed |
| --- | --- | --- |
| Data image (fixture export, Valhalla base) | 9 | 221 MB |
| First web image on it (uv, dependencies, code) | 7 | 184 MB |
| Web image after a code-only change | 3 | 1.4 MB |

With the full export, only the data image's push grows; the two web pushes stay
the same size. A change to `uv.lock` also pushes the dependency layer again
(about 180 MB).

## 10. The hosted vector map

One PMTiles file covers Europe for both maps' vector base map (ADR-0061). It
lives in a Cloudflare R2 bucket and is served from the custom domain
`maps.hikesalike.com` (the `hikesalike.com` zone is on Cloudflare DNS, and
connecting the domain to the bucket adds its DNS record), at
`https://maps.hikesalike.com/europe.pmtiles`. The privacy page names Cloudflare
as the map host whenever `DAYHIKE_PMTILES_URL` is set; change that text first if
the file moves to another host.

### Making the file

The file is a `pmtiles extract` of a Protomaps daily build of the whole planet
([builds](https://maps.protomaps.com/builds/), basemap schema v4), cut to the
bbox `-25,34,45,72` (longitude -25 to 45, latitude 34 to 72) at zoom 0 to 14.
The extract reads only the ranges it needs over HTTP, about 25 GB, so neither
the planet file (about 140 GB) nor any account is needed. Use the
[go-pmtiles](https://github.com/protomaps/go-pmtiles/releases) release binary:

```bash
BUILD=20261115   # a build's date, from https://build-metadata.protomaps.dev/builds.json
pmtiles extract "https://build.protomaps.com/$BUILD.pmtiles" europe.pmtiles \
  --bbox=-25,34,45,72 --maxzoom=14 --download-threads=8
pmtiles show europe.pmtiles   # bounds, zooms 0-14, the build's metadata
```

The first file is from the build of 2026-11-15 (basemap 4.15.2, OpenStreetMap
data as of 2026-11-15 04:00 UTC), extracted with go-pmtiles 1.31.2: 25.0 GB
(23.3 GiB), 13.4 million tiles. Zoom 15 would double it (49 GB) for detail the maps
barely show; MapLibre overzooms zoom 14 tiles past it.

### The bucket and its CORS rule

The bucket is public only through the custom domain (its `r2.dev` address
stays off). Upload `europe.pmtiles` as one object with an S3 client against the bucket's
S3 endpoint, which splits it into a multipart upload (`wrangler r2 object put`
takes only small objects), for example `rclone copyto europe.pmtiles r2:BUCKET/europe.pmtiles`.

The maps read the file by HTTP range requests from the page's origin, so the
bucket allows `GET` and `HEAD` with the `range` header from the site and from
Cloud Run's own addresses (the service URL and each deploy's tagged candidate,
section 9; `gcloud run services describe dayhike --region europe-west1` prints
the form), and exposes `etag`, which the PMTiles reader checks between
requests. In the S3 form of the policy:

```json
[
  {
    "AllowedOrigins": [
      "https://hikesalike.com",
      "https://www.hikesalike.com",
      "https://dayhike-PROJECT_NUMBER.europe-west1.run.app",
      "https://*---dayhike-PROJECT_NUMBER.europe-west1.run.app"
    ],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["range"],
    "ExposeHeaders": ["etag"],
    "MaxAgeSeconds": 86400
  }
]
```

R2 allows one `*` per origin, which here matches every candidate tag. Check it:
`curl -sI -H "Range: bytes=0-16383" -H "Origin: https://hikesalike.com" https://maps.hikesalike.com/europe.pmtiles`
answers `206` with `access-control-allow-origin: https://hikesalike.com`.

`www.hikesalike.com` should redirect (`301`) to `https://hikesalike.com`, by a
Cloudflare redirect rule with the `www` DNS record proxied; its origin stays in
the rule above in case a page is still served there. A page from an origin the
rule leaves out still has a map: both maps fall back to the raster tiles (ADR-0061).

### Refreshing it

The map changes slowly, so refresh it when it looks out of date (a few times a
year at most), never on a schedule. Extract a newer build as above, check it
with `pmtiles show`, and upload it under a new name (`europe-BUILD.pmtiles`),
then point `DAYHIKE_PMTILES_URL` at it and deploy. Pages opened before the
switch keep reading the old file, so delete the old object a day later.

### Cost

R2 bills storage and requests, never egress ([R2 pricing](https://developers.cloudflare.com/r2/pricing/)).
The free tier covers 10 GB-month of storage, 1 million Class A (writes) and
10 million Class B (reads) a month. The 25 GB file bills about 15 GB past it
at $0.015 per GB-month: about $0.23 a month. Opening a hike's map reads about
ten ranges (measured locally: 15 for the liked and one similar hike's map),
more as a visitor pans and zooms, so the free reads cover several hundred
thousand map views a month; past
them, reads cost $0.36 a million. A refresh keeps two files for a day and
uploads a few thousand parts, both within the free tier.

## 11. The ODbL download

The hike database is a derivative database of OpenStreetMap, offered free under
the ODbL ([ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/) §4.6). Only the
part made from OpenStreetMap is offered (the OSMF
[Collective Database guideline](https://osmfoundation.org/wiki/Licence/Community_Guidelines/Collective_Database_Guideline_Guideline):
heights, land cover, climate and places come from other sources with their own licences,
`DATA-LICENSES.md`). It is two files, served beside the map file from the same R2 bucket
(section 10), so the elevation source (FABDEM or Copernicus GLO-30) does not touch it:

- `hikes-osm.sqlite`: the hikes whose source is OpenStreetMap (never an external source's), with
  their names, kind, route, line (WKT) and length, `sac_scale`, Trailhead, and in `hike_points` the OpenStreetMap
  points along them with their names; a `licence` table holds the ODbL notice, "© OpenStreetMap contributors",
  the data date and what is left out. No heights (a peak's `ele` tag too), land cover, climate, GeoNames
  places or Landmarks.
- `README.txt`: the same notice, and the tables explained.

### Building it

From the serving export of section 1 (read-only), once per data refresh, after `export-serving`:

```bash
uv run dayhike export-osm-download --data data/web-serving --out data/osm-download --data-date YYYY-MM-DD
```

`--data-date` is the date of the OpenStreetMap extracts the export was built from (the Geofabrik
file date); without it the day the export was written is used, which is later than the data. Built
from the Europe export of 2026-11-15 it is 1.13 GB (258,211 hikes, 4,660,146 points). Check the row counts with
`sqlite3 data/osm-download/hikes-osm.sqlite 'select count(*) from hikes; select count(*) from hike_points;'`
against `meta.json`'s `dayhikes` (the difference is the external hikes, which stay out).

### Publishing it

Upload both files to the bucket of section 10 beside `europe.pmtiles`, replacing the last ones.
`hikes-osm.sqlite` (about 1.1 GB) is far past what `wrangler r2 object put` takes (about 300 MiB), so
upload it with an S3 client, as section 10 does for the map; the README is tiny, so `wrangler` takes it:

```bash
rclone copyto data/osm-download/hikes-osm.sqlite r2:BUCKET/hikes-osm.sqlite --header-upload "Content-Type: application/vnd.sqlite3"
wrangler r2 object put BUCKET/hikes-osm-README.txt --file data/osm-download/README.txt --content-type "text/plain; charset=utf-8" --remote
```

Then set the repository variable `DAYHIKE_DOWNLOAD_URL` to `https://maps.hikesalike.com/hikes-osm.sqlite` once
(section 9) and deploy; the credits page then shows the "Download the data" link. The same address is
used on every refresh (the file is replaced in place), so later refreshes need no deploy for the link. A file
that is replaced keeps its address, but a browser may serve a cached one for a short while; that is acceptable
for a download. Check: `curl -sI https://maps.hikesalike.com/hikes-osm.sqlite` answers `200` with a `content-length`.
