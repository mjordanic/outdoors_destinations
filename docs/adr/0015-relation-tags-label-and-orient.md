---
status: accepted
---

# Relation tags label and orient a DayHike; node-network legs are not DayHikes

Web-demo testers found three things the geometry alone gets wrong. About 16 % of DayHikes showed `relation:{id}` and 20 % a bare `ref` ("14", "R32"). About 16 % were OSM node-network legs (`network:type=node_network`, refs like "26-78"): 1–3 km between two numbered junctions, crowding flat Seeds' lists. And a line could start at its top: "Dolič - Triglav" started on the summit and "Kungsleden Etapp 7: Kebnekaise - Nikkaluokta" at Nikkaluokta, because parking near an end decides, so Trailhead facts were read at the wrong end. "Preikestolen Roundtrip" (`roundtrip=yes`) became a 4.1 km line when its loop was under half of the route.

**Now** ingest reads the relation's tags first, and keeps the geometry rules (ADR-0012) wherever the tags say nothing:

- **Label.** OSM `name`; else `ref` followed by `from – to` (`ref: from – to`) or by the nearest place (`ref near Place`); else `from – to` (NameOrigin `osm_from_to`); else `Hike near Place` (NameOrigin `nearest_place`); else `relation:{id}`. The place is the nearest OSM `place=city|town|village|hamlet` node with a name within 5 km of the Trailhead; ingest now collects those nodes and the GeometryStore keeps them as amenity points of kind `place`.
- **Node-network legs.** A `network:type=node_network` relation that is not a RoundTrip is dropped (`dropped_node_network` in the ingest report). A node-network loop is kept.
- **Line direction.** A relation's `from` and `to` tags, else a name of exactly two parts joined by a spaced dash ("Dolič - Triglav"), name its ends. A name names an end when its rarest word (4+ letters, rarest among all named OSM points) is a word of a named point (place, Hut, Peak, parking, restaurant, Shelter, Viewpoint, WaterSource) within 1 km of that end. A line starts at the end `from` names and the other does not, or the end opposite the one `to` names. When the names name both ends, neither, or contradict, parking decides as before.
- **Round trips.** With `roundtrip=yes`, a piece that has any loop is walked as that loop plus at most one stem (ADR-0012's loop walk) when that walk is at least as long as the line walk; otherwise it stays the line. RoundTrip itself stays geometric (ends within 50 m), so a lollipop is still not a RoundTrip.

The member order of a relation is not used to orient a line: on the Europe Catalog it agreed with `from`/`to` no better than parking did.

**Counts** on the Europe Catalog (Geofabrik snapshot of 2026-09-18) before → after are in the od-web-polish report; the headline numbers are recorded in `tests/rebuild_checks.py` (`RECORDED_INGEST_REPORT`).

**Considered Options**: trust member order whenever `from`/`to` are present (it would have reversed 24,690 lines, 54 % of them to start at the higher end, so member order is not a direction); drop every node-network relation (it would also drop the few node-network round walks); count a `roundtrip=yes` line out and back, or mark it a RoundTrip (that changes `length` and the RoundTrip Measure for every out-and-back route, a separate decision: future work 11); reverse-geocode labels with an online service (ingest stays offline, ADR-0001). Rejected.
