---
status: accepted
---

# Bases are ranked by the hike types in reach; filters are exact per hike; traits re-order the best hike matches

The captain asked for a "Find a base" mode: a Hiker names a town they stayed in and a Reach of about 15, 30
or 60 minutes, and gets other towns whose hikes in Reach are alike. The hike score stays fixed (512
HikeTypes, 40 % mix, 60 % coverage). The Hiker narrows which hikes count with HikeFilters (for example up
to 500 m of climb), and sets each TownTrait (town size, places to stay, restaurants, cafes and pubs, best
months) to ignore, filter or "like mine" with an importance. The hidden constants stay hidden. The plan was
approved with "start hiking-base finder"; the work is built on the branch `feature/bases`, one PR per
ticket into it and one final PR into develop.

**Bases are ranked by the hike types in Reach.** A Base is described by its BaseProfile: the HikeGroups in
its Reach, counted by HikeType. Scoring every hike near one town against every hike near another took
6-9 s for 300 towns, so the 512-type score stands in for it. On four seeds it kept the exact order closely
(rank agreement 0.86-0.96) but found only 13-21 of the exact top 30. The weights come from 20 known pairs
(Keswick with Betws-y-Coed, Llanberis, Ambleside and so on) against the other seeds as controls: mix alone
and coverage alone each fail somewhere, and 40 % mix with 60 % coverage (30 % each way) keeps the weakest
analogue and still separates the controls. A town with under half the Hiker's hikes is scored down by the
square root of their ratio, and one with under 10 HikeGroups is left out. Reach is an estimate made once at
build time and kept behind one function: the smaller of the highway estimate with the approach at 20 km/h and
a straight line at 28 km/h found 90-95 % of the truly reachable hikes against the OSRM demo router, with
precision 0.74-0.82. That is fair for a profile, which is an aggregate.

**HikeFilters are exact per hike, not per type.** A HikeType mixes hikes on both sides of any limit. On
"climb up to 500 m", filtering by type got 20-40 % of Chamonix's and Bohinj's counted hikes wrong and kept
only 10-17 of the exact top 30. So a filter is a yes/no mask over the hikes, from facts already mapped in
memory (climb, length, highest point, summit, lake or sea, round trip), and a HikeGroup counts when any of
its hikes passes. Both Bases are described by the hikes that pass. The mask costs milliseconds. The
BaseProfile is counted per request from the stored list of HikeGroups in Reach with their minutes, in
bands up to 15, 30 and 60 minutes, not precomputed, so the unfiltered ranking is the same code with an
all-yes mask. Measured on the real export, one pass over 70 million pairs takes 105 ms in numba. If the
Hiker's own Base has under 10 HikeGroups that pass, the page says so and does not rank. Difficulty is known
for 30 % of hikes, so a filter on it would silently drop the rest, and it is not offered.

**TownTraits re-order the best hike matches; they do not join the score.** The hike score is computed
first, for the best 3,000 Bases that pass every trait filter. A "like mine" trait then multiplies it by
`1 - (w / 6) * (1 - s)`, with s the similarity of the two towns on the trait and w 1, 2 or 3 for *a little*,
*matters*, *important*: a trait takes at most a sixth, a third or a half off, and a town like the Hiker's
loses nothing. A missing value costs nothing. A weighted mean of the hike score and the traits was tried
first; with places to stay and restaurants both set to *matters* it put Willingen (hike score 0.64) first
for Keswick, above Llanberis (0.87) and Hebden Bridge (0.86). Hotel counts outvoted the hikes and the list
stopped being about hiking. The multiplier keeps the hikes in charge, and a few per cent still move rows,
because good hike matches bunch (Chamonix's top ten are 0.91-0.94). Size, places to stay and eats use the
site's ratio Measure, months its tolerance Measure month by month, so the row's explanation (ADR-0025)
needs no new maths.

**Considered Options**:
- *Filtering by HikeType (keep a type when half its hikes pass, or scale by the share that passes).*
  Cheaper, and rejected for the error above.
- *Profiles stored per type, climb bin and length bin.* Exact only at fixed bin edges and for those two
  facts, with about the same request time. Rejected.
- *A weighted mean of the hike score and the traits.* Rejected for the Willingen result.
- *Scoring every hike near one town against every hike near the other.* Too slow for a request and cannot
  be precomputed for every pair of towns.

**Consequences**:
- Request time is estimated at 0.2-0.4 s (30 min) and 0.4-0.7 s (60 min) on Cloud Run's one vCPU, with
  about +0.4-0.6 GB of mapped files and 40-80 MB of working memory per ranking. The Bases get their own
  lock and rate limit apart from the hike ranking's. The estimates are confirmed on the real image in
  ticket 13.
- The export grows by about 0.5-0.6 GB. A build stage of about 1-2.5 hours on a 16 GB Mac reads a finished
  export and needs no catalog rebuild. Places to stay and restaurants are not in the export; one OSM pass counts them
  within 2 km of each place.
- US OSM is mapped more thinly than Europe's, so "like mine" on places to stay leans against US towns across
  continents; within one continent it is fair. Population is missing for many small places.
- The constants (the cut of a sixth per importance step, 3,000, the 8-22 C mild band, the size exponent
  of 0.5, 2 km, the months tolerance of 10 C) are tuned in ticket 13 and are not shown to the Hiker.
- The words are in GLOSSARY.md: Base, Reach, HikeType, HikeGroup, BaseProfile, BaseRanking, HikeFilter,
  TownTrait. "Destination" names a hike kind and is not used for a Base.
