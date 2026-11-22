---
status: accepted (amends ADR-0019's WayBack)
---

# A way back is never harder than the hike

Round 4 of the visitor test: every tester's top issue was Mount Whitney Trail (OSM relation 3289187). It is
17 km one way, graded `hiking`, but with the way back the card said 24 km, "+ 7.1 km back, along the trails",
and graded T5. The trail back was the shortest walk over the TrailGraph from the summit to Whitney Portal,
the Mountaineers Route scramble, so every figure with the way back described "up the trail, down the
scramble". Over the export, 25,392 hikes (2.9 %) had a way back graded harder than the hike, 1,928 of them
by 3 or more SAC grades (a hike with no `sac_scale` counted as below `hiking`).

**The trail back walks no Segment graded harder than the hike.** The search for the trail back (ADR-0019,
bounded by ADR-0035) skips every Segment whose `sac_scale` ranks above the hike's own strictest grade: the
Catalog's Difficulty of a DayHike, the strictest of its Segments for a ComposedHike. A hike with no
`sac_scale` takes a trail back with none either: 7,952 of the 25,392 were such hikes with a way back graded
`hiking`, which gave the round trip a Difficulty the hike has not. When no such trail is shorter than
retracing the hike, the hike is retraced, as before. Steps, loops and the 50 km
bound are unchanged; an easier trail back that is shorter than the retrace is still taken. Whitney's way back is
now its own trail retraced, unless a trail graded `hiking` or with no grade is shorter.

**The way-back network keeps the shortest Segment of each grade between two Junctions.** It used to keep only
the shortest, which could be the scramble beside an easier path between the same Junctions. Each entry now
carries its grade (`sac_rank.npy` in `trailgraph.network/`); a network saved without it is built again from
the Segments on first use.

The figures with the way back of Catalog DayHikes are measured at export (`build-similarity`), so they change
only with a new similarity build and serving export. A ComposedHike's way back is found when it is measured,
so it changes as soon as the code is served.
