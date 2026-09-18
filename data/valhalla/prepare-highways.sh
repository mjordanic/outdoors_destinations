#!/usr/bin/env bash
# Build a long-distance PBF: motorway, trunk, primary, and ferries only.
# For continent-scale travel. Too sparse for Trailhead DriveTime — use
# prepare-roads.sh for that. Omits turn restrictions so osmium does not
# pull referenced residential/service members into the extract.
set -euo pipefail

usage() {
  echo "usage: $0 <input.osm.pbf>" >&2
  echo "writes data/valhalla/<stem>-highways.osm.pbf and removes other .osm.pbf files there" >&2
  exit 1
}

if [[ $# -ne 1 ]]; then
  usage
fi

if ! command -v osmium >/dev/null 2>&1; then
  echo "osmium-tool is required (brew install osmium-tool)" >&2
  exit 1
fi

input=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
if [[ ! -f "$input" ]]; then
  echo "not a file: $1" >&2
  exit 1
fi

valhalla_dir=$(cd "$(dirname "$0")" && pwd)
stem=$(basename "$input")
stem=${stem%.osm.pbf}
stem=${stem%.pbf}
stem=${stem%-roads}
stem=${stem%-highways}
output="$valhalla_dir/${stem}-highways.osm.pbf"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

osmium tags-filter "$input" \
  w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link,ferry \
  w/route=ferry \
  -o "$tmp/highways.osm.pbf" --overwrite

rm -f "$valhalla_dir"/*.osm.pbf
mv "$tmp/highways.osm.pbf" "$output"
echo "wrote $output"
ls -lh "$output"
