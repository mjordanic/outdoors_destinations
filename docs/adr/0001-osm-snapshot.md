# OSM snapshot, not live Overpass

The Catalog is built from downloaded Geofabrik PBF extracts (filtered with osmium/pyosmium) written under `data/`. Runtime queries never hit Overpass. A live API cannot cover HR+SI+BA+ME plus Alpine extracts without timeouts, and a demo that depends on someone else's public instance is not a local tool.

**Considered Options**: live Overpass at query time; a nightly Overpass dump. Rejected because the bbox is too large and the Catalog must be reproducible from files in `data/`.
