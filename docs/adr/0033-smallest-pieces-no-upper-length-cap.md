---
status: accepted (supersedes ADR-0007's length band and superroute rule; amends ADR-0018)
---

# Smallest pieces: no upper length cap, only pure containers dropped

Around Briançon the Catalog held almost none of the hiking routes OpenStreetMap maps there. Two of the causes were Catalog rules: a 20 km upper cap, and a superroute rule that dropped every `network=iwn` relation and every `network=nwn` relation with child relations or over 20 km. They dropped the GR 5 sections through Briançon (117 and 163 km), the 66 km Tour du Mont Thabor and eight Via Alpina day stages of 10–22 km. The site is no longer only about day hikes: a Hiker may want a multi-day tour too.

**A Catalog DayHike is the smallest piece that holds trail.** Every `route=hiking` relation with at least one way member is kept when its main walk is at least 1 km, whatever its `network` and however long it is. There is no upper length cap. A relation with no way of its own (a superroute or container whose members are only relations, or an empty one) holds no trail and is dropped as a **container** (`dropped_container` in the ingest report, in place of `dropped_network`). Its child relations are judged on their own. A mixed relation, with ways and child relations, is kept and measured on its own ways, as lwn and rwn mixed relations already were.

**Kept as they were:** the 1 km minimum, and the node-network rule (a `network:type=node_network` leg that is not a RoundTrip is not an outing). The TrailGraph still holds the ways of every relation ingest read, so the 1 km minimum is the only band left from ADR-0018's "the 1–20 km band holds".

**Consequences.** Europe gains up to about 13,600 relations over 20 km (522 over 200 km; the longest near 2,000 km) and about 800 iwn relations of 1–20 km. Downstream code that assumed a 20 km ceiling (the way-back search, loop bounds, the Wish length input) must bound itself on its own. Day-sized pieces of long tours are a separate change.

**Considered Options.** Only treat iwn like nwn and keep the 20 km cap (keeps the short Via Alpina stages, loses GR sections and every multi-day tour). List whole tours, containers included, as one joined hike (the same trail again at every level of a tour; the smallest pieces were chosen, and day-sized stages come from splitting long ones).
