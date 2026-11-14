---
status: accepted
---

# The vector base map is drawn by MapLibre GL JS under Leaflet's overlays

Both maps drew raster tiles from `tile.openstreetmap.org`, whose usage policy allows no heavy use and no
bulk download. The base map is now a setting (#120): a raster tile URL template, its credit and max zoom,
OpenStreetMap's tiles by default. A vector base map in one PMTiles file, which any static host can serve
with range requests, keeps the source under the project's control (#119). Leaflet draws raster tiles only,
so a vector map needs another renderer. The maintainer chose MapLibre GL JS (option b of #119) over
`protomaps-leaflet`, which is in maintenance mode.

**The decision.** With a PMTiles URL set (`dayhike serve --pmtiles-url`, `DAYHIKE_PMTILES_URL`), MapLibre GL
JS draws the vector base map as one Leaflet layer, through the MapLibre project's `maplibre-gl-leaflet`
binding, in the Protomaps light style with dashed paths and peaks named with their height. Every overlay
stays a Leaflet layer: the hike lines, map picking, the shared-trail popup, hover highlight, the loops and
the route designer. They behave the same over both base maps by construction, and the same page tests run
over both. MapLibre, the binding, the PMTiles reader, the style, its glyphs and sprites are served from
`static/vendor/` with their licences, and the page loads them only when a PMTiles URL is set. Without one,
the page is the raster page it was. A browser without WebGL 2 gets the raster tiles.

**Considered Options**

- **Native MapLibre maps in vector mode, Leaflet in raster mode.** Every overlay and click handler (about
  700 lines) would exist twice, on two APIs, and the page tests would run per mode. Rejected: twice the
  upkeep for the same behaviour.
- **Native MapLibre always, the raster tiles as a MapLibre raster source, Leaflet dropped.** One code path,
  map rotation and GL-drawn overlays, but WebGL becomes required, every page loads about 1.6 MB more script,
  and every map page test is rewritten. Not now; a later move if the overlays outgrow Leaflet.

**The cost.** Two map libraries in vector mode. The GL canvas follows Leaflet's zoom animation by a CSS
transform, so it can blur for a frame mid-zoom. The map cannot rotate or tilt.
