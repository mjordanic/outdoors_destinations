# Local Valhalla; matrix for DriveTime, isochrones only for maps

DriveTime comes from a local Valhalla instance built from the same regional PBF. The query primitive is `/sources_to_targets` from Origin to Trailheads. Isochrone polygons are not a Catalog filter and are not a Feature; they may shade a map in a later UI.

Public openrouteservice driving isochrones cap at one hour, so they cannot answer "within four hours." Self-hosted ORS/GraphHopper would work but use more RAM on a multi-country extract. OSRM has no first-class isochrones.

**Consequences**: Ranking works without Valhalla. `--origin` / max DriveTime requires the engine. Tests of the DriveTime filter mock the matrix HTTP API rather than building tiles in CI.

ADR-0013 adds an isochrone as a coarse first pass for the highways-only estimate; the matrix still gives the exact times.
