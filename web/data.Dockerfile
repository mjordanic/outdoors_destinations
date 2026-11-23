# syntax=docker/dockerfile:1
# The data image: the pinned Valhalla base plus the serving export at /data, nothing else.
# Built once per data refresh and site, tagged with the export's data_tag (meta.json; facts_version and the DEM,
# and for a cut the site it is: -eu, -us, -demo); the uncut Europe and US export's tag takes -eu-us (docs/deploy.md §1):
#
#   docker build -f web/data.Dockerfile --build-context serving=data/web-serving-eu \
#     -t dayhike-data:"$(jq -r .data_tag data/web-serving-eu/meta.json)" .
#
# The web image (web/Dockerfile) starts FROM it, so its layers sit at the bottom and a
# code-only change pushes only the code layers above them. The base is the Valhalla
# that built the highways tiles (tiles are version-sensitive), so a Valhalla upgrade
# is a data refresh too.
FROM ghcr.io/gis-ops/docker-valhalla/valhalla:latest@sha256:060da5b92e6024a67f65135c236d918b021d63e73d1808a0d55b7e7cbd17240c
COPY --from=serving . /data
