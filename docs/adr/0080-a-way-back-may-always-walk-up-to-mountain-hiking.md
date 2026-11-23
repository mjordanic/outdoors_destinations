---
status: accepted (amends ADR-0076)
---

# A way back may always walk up to `mountain_hiking`

ADR-0076 capped the trail back at the hike's own strictest `sac_scale`, and a hike with none at no grade at
all. Most hikes have no `sac_scale` (625,416 of 889,525 DayHikes in the last export), so their trail back
could walk only untagged ways, not even a `hiking` (T1) one: it often took a longer way round or retraced
the hike where a plain marked path led back.

**The cap is the hike's own grade, but never below `mountain_hiking` (T2).** A hike with no grade, or graded
`hiking`, may walk back on untagged, `hiking` and `mountain_hiking` ways; a harder hike keeps its own grade as
the cap (a T4 hike walks back on up to T4, never harder). Mount Whitney Trail (`hiking`) is still walked back
down its own trail, not down the Mountaineers Route (T5). The rest of ADR-0076 stands, and so does ADR-0078:
the round trip keeps the hike's own grade whichever way back is found.

**How many hikes change.** On a random sample of 3,000 hikes with no grade and 3,000 graded `hiking` from
the last export, 2.2 % and 3.4 % take a different way back, by about 200 m at the median: about 14,000 and
3,800 hikes, some 18,000 (2 %) in all. Hikes graded T2 or harder are unchanged.

**Consequences.** `build-similarity` again (its way backs and the facts with the way back change), then
`export-serving`; no re-ingest. A ComposedHike's way back changes as soon as the code is served.
