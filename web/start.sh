#!/bin/sh
# Start highways-only Valhalla in the background (if the export carries it), then the app.
set -eu
DATA="${DAYHIKE_DATA:-/data}"
if [ -f "$DATA/valhalla/valhalla.json" ] && [ -f "$DATA/snaps.npz" ]; then
  valhalla_service "$DATA/valhalla/valhalla.json" "${VALHALLA_THREADS:-1}" &
  set -- --valhalla-url http://127.0.0.1:8002
fi
if [ -n "${DAYHIKE_GOATCOUNTER:-}" ]; then set -- "$@" --goatcounter "$DAYHIKE_GOATCOUNTER"; fi
if [ -n "${DAYHIKE_SITE_URL:-}" ]; then set -- "$@" --site-url "$DAYHIKE_SITE_URL"; fi
if [ -n "${DAYHIKE_TILE_URL:-}" ]; then set -- "$@" --tile-url "$DAYHIKE_TILE_URL"; fi
if [ -n "${DAYHIKE_TILE_ATTRIBUTION:-}" ]; then set -- "$@" --tile-attribution "$DAYHIKE_TILE_ATTRIBUTION"; fi
if [ -n "${DAYHIKE_TILE_MAX_ZOOM:-}" ]; then set -- "$@" --tile-max-zoom "$DAYHIKE_TILE_MAX_ZOOM"; fi
if [ -n "${DAYHIKE_PMTILES_URL:-}" ]; then set -- "$@" --pmtiles-url "$DAYHIKE_PMTILES_URL"; fi
for origin in ${DAYHIKE_CORS_ORIGINS:-}; do set -- "$@" --cors-origin "$origin"; done  # space-separated
exec /opt/venv/bin/dayhike serve --data "$DATA" --host 0.0.0.0 --port "${PORT:-8080}" \
  --forwarded-hops "${DAYHIKE_FORWARDED_HOPS:-0}" "$@"
