---
status: accepted (amends ADR-0018's resolver and ADR-0038's "Edit on the map")
---

# A gap step walks the trail between its ends

"Edit on the map" failed for 18,369 of the 97,349 built DayHikes of the Europe rebuild 4 (18.9 %: 27.0 % of NetworkHikes, 3.5 % of DayStages), with "the gap from OSM node X to OSM node Y is no longer on the marked network" although nothing in OSM had changed. A built hike and the TrailGraph join gaps by different rules. A built hike's Route writes `g.{node}` wherever two consecutive vertices share none of the hike's **own** ways: a main walk or a chain of legs jumps straight, up to 50 m (`GAP_JOIN_M`), from one of its ways to the next. The TrailGraph has a `gap` Segment only where a dead-end tip lies within the 20 m snap tolerance of another way and the network does not already join them (ADR-0018). Mostly another relation's way or a graded path already joins the two ends, as a `trail` Segment, so there is no `gap` Segment to cross. ADR-0038 priced this failure at 1 in 40 on the Briançon cut; at Europe scale with NetworkHikes it was 1 in 4.

**The rule.** A `g.` step from Junction X to Junction Y walks, in this order:

1. the `gap` Segment between them;
2. else the shortest other Segment between them (never a Link) of at most `GAP_CROSS_M` (3 × `GAP_JOIN_M`, 150 m);
3. else the shortest walk over trail and gap Segments (never a Link) from X to Y, shorter than `GAP_CROSS_M`, found with the search the way back uses (ADR-0019).

Where neither end is a Junction or no such walk exists, the step is refused, and the message says so: "this hike jumps a gap from X to Y that the trail network does not join". The resolver never makes up a straight step. An `l.` step still walks only its Link.

**Consequences.** No rebuild: the Route text, and so the built ids, `#hike=` and `#compose=` links, stay the same. ADR-0018's "it never opens a different walk" no longer holds for a gap step: the walk follows the trail between the gap's ends rather than the straight jump, so a composed built hike can be a little longer than the stored one. Walking every stored Route of rebuild 4 with this rule, 1,028 of 97,349 fail (1.06 %): 519 NetworkHikes and 503 DayStages jump a gap the trail network does not join (92 of them with an end inside a Segment rather than at a Junction), and 6 DayStages fail on a way that no longer leads between two nodes, as before. Of the hikes with a gap step that compose, the composed walk is at most 2.1 % longer than the stored one (99th percentile 0.39 %). The first gap step that reaches rule 3 makes the process build the way back's network (`TrailGraph._network`) if it has not yet.

**Considered Options.** Write the Route over every snapshot way, not only the hike's own (rejected: it needs a re-ingest, changes the Route text and so every built id and link, and still misses gaps a graded path joins). Let the TrailGraph join the same 50 m gaps the built hikes join (rejected: a re-ingest, junk gaps in the composer's network, and it misses the common case where a trail Segment already joins the ends). Accept any Segment between the two Junctions whatever its length (rejected: a 2 km trail round a lake is not the 30 m jump the hike made). Draw the remaining gaps as straight steps (rejected: the resolver would invent ground the TrailGraph does not have).
