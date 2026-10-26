#!/usr/bin/env bash
# Build a long-distance PBF: motorway, trunk, primary, and ferries only.
# For continent-scale travel. Too sparse to reach Trailheads on its own: pair
# it with `dayhike ... --highways` (an estimate), or use prepare-roads.sh. Omits turn
# restrictions so osmium does not pull referenced residential/service members
# into the extract.
set -euo pipefail

usage() {
  echo "usage: $0 <input.osm.pbf> [dest-dir]" >&2
  echo "writes <dest-dir>/<stem>-highways.osm.pbf and removes other .osm.pbf files there" >&2
  echo "dest-dir defaults to data/valhalla; give a separate one to keep a roads PBF" >&2
  exit 1
}

if [[ $# -lt 1 || $# -gt 2 ]]; then
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
dest_dir=${2:-$valhalla_dir}
mkdir -p "$dest_dir"
dest_dir=$(cd "$dest_dir" && pwd)
stem=$(basename "$input")
stem=${stem%.osm.pbf}
stem=${stem%.pbf}
stem=${stem%-roads}
stem=${stem%-highways}
output="$dest_dir/${stem}-highways.osm.pbf"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

osmium tags-filter "$input" \
  w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link,ferry \
  w/route=ferry \
  -o "$tmp/highways.osm.pbf" --overwrite

rm -f "$dest_dir"/*.osm.pbf
mv "$tmp/highways.osm.pbf" "$output"
echo "wrote $output"
ls -lh "$output"
