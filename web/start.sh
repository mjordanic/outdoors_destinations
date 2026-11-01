#!/bin/sh
# Start highways-only Valhalla in the background (if the export carries it), then the app.
set -eu
DATA="${DAYHIKE_DATA:-/data}"
if [ -f "$DATA/valhalla/valhalla.json" ] && [ -f "$DATA/snaps.npz" ]; then
  valhalla_service "$DATA/valhalla/valhalla.json" "${VALHALLA_THREADS:-1}" &
  set -- --valhalla-url http://127.0.0.1:8002
fi
if [ -n "${DAYHIKE_GOATCOUNTER:-}" ]; then set -- "$@" --goatcounter "$DAYHIKE_GOATCOUNTER"; fi
if [ -n "${DAYHIKE_KOFI:-}" ]; then set -- "$@" --kofi "$DAYHIKE_KOFI"; fi
for origin in ${DAYHIKE_CORS_ORIGINS:-}; do set -- "$@" --cors-origin "$origin"; done  # space-separated
exec /opt/venv/bin/dayhike serve --data "$DATA" --host 0.0.0.0 --port "${PORT:-8080}" \
  --forwarded-hops "${DAYHIKE_FORWARDED_HOPS:-0}" "$@"
