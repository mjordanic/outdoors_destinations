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

# cleanup policy: keep the last 9 versions (3 pushes), delete anything older than 30 days
cat > /tmp/ar-cleanup.json <<'EOF'
[
  {"name": "keep-recent-releases", "action": {"type": "Keep"},
   "mostRecentVersions": {"keepCount": 9}},
  {"name": "delete-older-than-30d", "action": {"type": "Delete"},
   "condition": {"tagState": "ANY", "olderThan": "30d"}}
]
EOF
gcloud artifacts repositories set-cleanup-policies dayhike --location=europe-west1 \
  --policy=/tmp/ar-cleanup.json --dry-run      # then again with --no-dry-run

# the image is 1 to 2.4 GB compressed, so the push takes a couple of minutes
IMAGE=europe-west1-docker.pkg.dev/PROJECT/dayhike/web:$(git rev-parse --short HEAD)
docker buildx build --platform linux/amd64 -f web/Dockerfile \
  --build-context serving=data/web-serving -t "$IMAGE" --push .

gcloud run deploy dayhike --image "$IMAGE" --region europe-west1 \
  --allow-unauthenticated \
  --cpu 1 --memory 2Gi --execution-environment gen2 --cpu-boost \
  --min-instances 0 --max-instances 2 --concurrency 8 --timeout 60 \
  --set-env-vars DAYHIKE_FORWARDED_HOPS=1
```

Artifact Registry storage is not covered by the Cloud Run spend cap (section 5),
and each push adds 1 to 2.4 GB, hence the cleanup policy. `docker buildx` pushes
three versions per tag: an image index (the tagged one), the `linux/amd64`
image and an attestation. Cloud Run runs the untagged `linux/amd64` digest.
Cleanup never deletes an image that a kept index references, but it deletes the
children once their index goes. So `keepCount` counts versions, not tags: 9
keeps the last three pushes. Keep it a multiple of three.
Rolling back (section 7) only works to an image that is still kept. The
policy runs in a daily background job. `--dry-run` deletes nothing, but its
would-be deletions show only in the Data Access audit logs, which are off
on this project.

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
| `DAYHIKE_CORS_ORIGINS="https://HOST ..."` (optional) | The origins whose pages may call the API (CORS): the loading shell of section 8. Unset, no other origin may. Not set yet: no shell. |
| `DAYHIKE_KOFI=HANDLE` (optional) | The Support page links `https://ko-fi.com/HANDLE`. Unset, it says the tip link is not set up yet. Not set yet: no Ko-fi handle. While FABDEM is in use, email Fathom / the University of Bristol about the donation link before setting it, unless the Copernicus swap has landed. |

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
both instances gets up to 2x that), runs one Ranking at a time per instance (others
wait up to 30 s, then get `503`; a Valhalla call stalled past 50 s also ends
in `503` and frees the slot), and caps the drive filter at 4 hours. With
`--max-instances 2` the most the service can ever bill is two instances busy
around the clock: about 5.3 million vCPU-seconds and 10.5 million GiB-seconds a
month, roughly **$150 per month at list price** after the free tier (verify the
europe-west1 rates). Normal demo traffic stays inside the free tier
(180,000 vCPU-seconds and 360,000 GiB-seconds a month); the images in Artifact
Registry cost about $0.25 to $0.70 a month: the cleanup policy keeps three
pushes, 3 to 7 GB, at about $0.10 per GB-month past the 0.5 GB free tier.

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
- **Tile and place-search policies.** The map uses `tile.openstreetmap.org`,
  run by the OSM Foundation for low-volume use under its usage policy; if
  traffic grows, switch to a paid tile provider. Export with `--geonames` so
  the origin search reads `places.sqlite`; without it the page falls back to
  Nominatim, which allows at most one request a second over all visitors.
- **Privacy page.** `privacy.html` names the host (Google Cloud Run,
  `europe-west1`), says the request logs are kept by Google Cloud Logging in
  Google's default storage location (not pinned to the EU), the 30-day log
  retention, the map tile and place-search
  providers, and GoatCounter and Ko-fi once they are set. Change it with any of
  these: a new host or a CDN in front (the `host-recipient` item), a shorter
  log retention, or a different tile or place-search provider.
- **FABDEM is non-commercial (CC BY-NC-SA 4.0):** the demo must stay
  non-commercial (no ads, no paid tier).
  An export sampled from Copernicus GLO-30 instead (ADR-0030) lifts this
  limit for elevation only when every DEM step (`sample-dem`,
  `sample-trailgraph`, `build-similarity`) ran with the same Copernicus
  `--dem` over tiles covering every DayHike. Otherwise some values are still
  FABDEM: its credit and the non-commercial limit still apply, even though the
  page credits only Copernicus (it follows the last `sample-dem`).

## 7. Update or roll back

Rebuild the export and the image with a new tag, then `gcloud run deploy` again;
`gcloud run services update-traffic dayhike --region europe-west1 --to-revisions REVISION=100`
goes back to an earlier revision.

## 8. A loading shell on a static host (not done yet)

**Not set up.** No domain, static host or shell exists yet; the host is chosen
once there is a domain. Nothing below has been run.

With `--min-instances 0` the first visit after a quiet spell waits for the
container to start, and while Cloud Run serves the page too, the browser shows
nothing until then. A shell on a static host (for example Cloudflare Pages)
serves the page's files at once, so the loading screen (the logo, then "Waking
the server…") shows while Cloud Run starts, and the page then calls the API on
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

The steps, once there is a domain and a host account:

1. Deploy Cloud Run as in section 3, with
   `DAYHIKE_CORS_ORIGINS="https://SHELL_HOST"` added to `--set-env-vars`
   (every host the shell is served at, such as the custom domain and the
   host's own `*.pages.dev` name).
2. Copy the static files and point the copy at Cloud Run:

   ```bash
   SHELL_DIR=$(mktemp -d)/shell && cp -R src/dayhike/web/static "$SHELL_DIR"
   sed -i '' 's|<meta name="api-base" content="">|<meta name="api-base" content="https://SERVICE.run.app">|' "$SHELL_DIR/index.html"
   ```

   The server fills in the GoatCounter tag and the Ko-fi link (`<!--analytics-->`,
   `<!--ko-fi-->`); a shell copy keeps the placeholders, so fill them the same way
   if those settings are on.
3. Publish `$SHELL_DIR` on the static host after every `gcloud run deploy`, so
   the shell and the API stay the same version (on Cloudflare Pages:
   `wrangler pages deploy "$SHELL_DIR" --project-name gofindmeahike`).
4. Point the custom domain at the static host.

The server sends `Cache-Control: no-cache` on the page; a static host needs the
same, for example a Cloudflare Pages `_headers` file (an example only; no such
file is in the repository):

```text
/*
  Cache-Control: no-cache
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
```
