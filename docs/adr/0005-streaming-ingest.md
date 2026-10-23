---
status: accepted (access clipping superseded by ADR-0012 "Round 2")
---

# Ingest streams OSM snapshots through a work file

Continent-scale PBF extracts do not fit in RAM as an in-memory OSM graph. Ingest therefore filters (osmium-tool when present), then streams relations, ways, and nodes into a SQLite work file beside the Catalog, clips driveable highways to Trailhead access radius, and replaces `catalog.sqlite` only after the new Catalog is complete. Re-running the same inputs resumes the work file.

Access clipping uses the same haversine disk (`TRAILHEAD_ACCESS_RADIUS_M`) as Trailhead choice. When osmium-tool is on `PATH` and the source is a Type-then-ID PBF (Geofabrik), ingest writes lon/lat boxes that contain twice that radius and runs `osmium extract --polygon --strategy complete_ways` on the unfiltered source, then scans the extract. The doubled box keeps every node that could sit on a polygon edge outside the extract boundary, so the BallTree clip matches a full-source scan. Parking for Trailheads still comes from amenity tables, not this extract. XML, an unsorted PBF, or a missing osmium-tool, scans the source file.

**Considered Options**: load the whole snapshot into Python dicts (OOMs on Europe); append country files into a live Catalog (would require refitting the scaler mid-ingest and changes the replace-not-append rule). Rejected.

**Round 2 note (ADR-0012):** the driveable-highway clipping and the access
extract above are superseded. Access points are parking only, so ingest no
longer scans drivable roads, writes access boxes, runs `osmium extract`, or
leaves `*.access*` sidecars. The streaming design, the osmium tags-filter, the
resumable work file and replacing the Catalog only when complete are unchanged.
The text above is kept as the record of the first design.
