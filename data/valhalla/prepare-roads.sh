#!/usr/bin/env bash
# Build a DriveTime PBF: car highways, ferries, and turn restrictions only.
# Drops buildings, POIs, footways, cycleways, paths, steps, and driveways.
set -euo pipefail

usage() {
  echo "usage: $0 <input.osm.pbf>" >&2
  echo "writes data/valhalla/<stem>-roads.osm.pbf and removes other .osm.pbf files there" >&2
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
output="$valhalla_dir/${stem}-roads.osm.pbf"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# Keep ways a car can use, plus ferries and turn restrictions.
# highway=track stays: many Trailheads sit on forest/farm access roads.
osmium tags-filter "$input" \
  w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link,secondary,secondary_link,tertiary,tertiary_link,unclassified,residential,living_street,service,track,road,ferry \
  w/route=ferry \
  r/type=restriction \
  r/route=ferry \
  -o "$tmp/highways.osm.pbf" --overwrite

# Private stubs from a street to a house. Not through-roads; they bloat the graph.
osmium tags-filter --invert-match "$tmp/highways.osm.pbf" \
  w/service=driveway \
  -o "$tmp/roads.osm.pbf" --overwrite

rm -f "$valhalla_dir"/*.osm.pbf
mv "$tmp/roads.osm.pbf" "$output"
echo "wrote $output"
ls -lh "$output"
