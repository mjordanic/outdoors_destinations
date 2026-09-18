# Ingest streams OSM snapshots through a work file

Continent-scale PBF extracts do not fit in RAM as an in-memory OSM graph. Ingest therefore filters (osmium-tool when present), then streams relations, ways, and nodes into a SQLite work file beside the Catalog, clips driveable highways to Trailhead access radius, and replaces `catalog.sqlite` only after the new Catalog is complete. Re-running the same inputs resumes the work file.

**Considered Options**: load the whole snapshot into Python dicts (OOMs on Europe); append country files into a live Catalog (would require refitting the scaler mid-ingest and changes the replace-not-append rule). Rejected.
