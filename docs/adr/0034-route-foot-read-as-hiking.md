---
status: accepted (amends ADR-0007 and ADR-0018)
---

# route=foot is read as route=hiking

OSM tags some walking routes `route=foot` rather than `route=hiking`; around Briançon these include Le lac de l'Eychauda and Sentier Découverte, and Europe has about 65,000 such relations. Ingest read only `route=hiking`, so none of them could be a DayHike or a TrailGraph Segment. Ingest now reads `route=foot` relations exactly as it reads `route=hiking`: the same catalog extract filter (`r/route=hiking,foot`), the same Catalog rules (ADR-0033) and the same TrailGraph input. On the 2026-09-17 snapshot about 10,500 of them pass those rules, before Twins are removed.

**Consequences.** Some `route=foot` relations are town walks and city tours; they are DayHikes like any other relation that passes the rules. The changed filter list makes the next ingest cut the catalog extract again from the full PBF.

**Considered Options.** Also read `route=walking` (not an approved OSM value; rare). Keep `route=foot` out and accept the gap (rejected: the captain wants all hikes).
