---
status: accepted (amends ADR-0019's WayBack and ADR-0020's loop band)
---

# Long hikes downstream: a bounded way back, a loop bound, lengths in km

ADR-0033 lifted the Catalog's 20 km cap: a DayHike may now be a 66 km tour or a 1,000 km long-distance path. Code downstream of ingest had leaned on that cap. The WayBack's trail search was bounded only by the hike's own length, so a long line searched a large part of the TrailGraph, and `build-similarity` failed outright once a trail back came out over the 100 km ComposedHike limit (`RouteError` from the composer). The Wish form took lengths up to 40 km, in metres.

**The way back is searched only up to 50 km (`WAY_BACK_SEARCH_M`).** The WayBack is still the shortest of the step, a trail back and the retrace (ADR-0019). The trail search now stops at the shorter of the retrace and 50 km, and does not run at all when the hike's ends are farther apart than that in a straight line. Past the bound the hike is retraced. So the search costs what a day hike's does, whatever the hike's length, and a trail back can never reach the composer's 100 km limit. A long tour that ends near its start still takes its short trail back: Trail du Galibier (53 km) ends 0.5 km from its start. Hikes up to 50 km one way, every DayHike before ADR-0033 among them, keep the way back they had.

**Loops stay at most 40 km (`LOOP_MAX_M`), now as a bound on the search.** The band was "the longest DayHike there and back". It is now a bound on the work one loop search may do on one vCPU (ADR-0028). A Seed or Wish over about 53 km (0.75 × length above 40 km) gets no SuggestedLoops; longer walks come from the Catalog.

**Lengths are wished in km, up to 2,000 km.** The `length` and `dry_stretch` Wish inputs go up to 2,000 km. The API keeps metres, the Measure's fact: `/api/measures` gives each number input a `display` (`{"unit": "km", "per": 1000}`, or null), and the page shows, checks and compares in km. A Wish address keeps metres too, so links made before still open. Under Length, while wishing, a hint says that over about 25 km is usually more than a day, and that with Return to start a line counts there and back (ADR-0019's rule is kept for long lines).

**Unchanged.** The composer's limits (100 km and 50,000 steps per ComposedHike, 2,000 steps per link): composing multi-day walks was not asked for, and its cost is per request. The map's line simplification: see the measurement below.

**Considered Options.**
- Skip the trail search for every hike over 50 km and retrace (the plan's first wording). Simpler, but Trail du Galibier (53 km, 0.5 km apart) would count 106 km with Return to start instead of 53.5 km. Bounding the search instead of the hike costs the same and keeps such tours right.
- Raise the composer's 100 km limit for a trail back. It leaves the search unbounded for 1,000 km lines.
- Lengths in km in the API. It would break every client and address that sends metres.
