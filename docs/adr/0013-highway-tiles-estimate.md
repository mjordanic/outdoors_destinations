# DriveTime on highways-only tiles is an estimate, found with an isochrone

Detailed car tiles for Europe do not build in a 7.6 GB Docker VM: the build of the whole roads PBF never finished, and a 1.1 GB regional chunk was killed for lack of memory. Highways-only tiles (motorway, trunk, primary) build in 11 minutes and serve from a 754 MB extract, but most Trailheads are not on them.

With `--highways`, DriveTime is therefore an estimate: each Trailhead is snapped onto the highway tiles, the straight-line distance from it (and from the Origin) to its highway point is added at 35 km/h, and one isochrone from the Origin places every snapped point in a 20-minute band. Only points near the time limit go to the matrix for an exact time. Matrix-only was exact but took 32 s for a four-hour query from Munich (106,000 candidates, mostly spent snapping them); this takes about 10 s warm.

**Considered Options**: a table of per-Trailhead entry points and local seconds, routed offline on detailed regional tiles (it overestimated by 13 to 18 minutes and kept 85 to 92 % of true hikes, against 94 to 98 % for the straight-line approach, and needs chunked tile builds); highways-only with no approach (about 8 minutes short on average). Rejected.

**Consequences**: times are good to about ten minutes, and a limit to about half an hour; the README has the measured error. Snaps are cached beside the Catalog. The isochrone is a coarse pass, not a map polygon, so ADR-0004's "isochrones only for maps" now allows it where the matrix is too slow. Trailheads with no highway within 100 km are named, not dropped silently.
