---
status: accepted (amends ADR-0012)
---

# A way the relation leaves out joins its pieces

Round 4 of the visitor test found Besseggen, the best-known day hike in Norway, listed as 7.6 km one way;
the traverse from Gjendesheim to Memurubu is about 14 km. The main walk (ADR-0012) is the longest connected
piece of a relation's member ways, joined only across gaps of 50 m or less between way ends (`GAP_JOIN_M`).
Relation 1417687 "Gjendesheim-Besseggen-Memurubu" and relation 2294719 "JOT2" both leave out one 175 m way
(1417738777, near the Bessvatnet saddle) after a mapper split the path, so their two pieces lie 150 m apart in a
straight line. The walk kept the 7.6 km piece and dropped 6.6 km as `disconnected`. The left-out way is in the
snapshot: relation JOT5 lists it.

**The rule.** At ingest, after the 50 m gaps are joined, a way of the snapshot that the relation does not list
joins two of its pieces when it runs from a node of one to a node of the other, is at most 500 m long
(`BRIDGE_M`), and has no other node on the member ways. Shortest first, as long as the pieces are still apart;
the walk follows its nodes, so there is no straight jump. It is no member: it is not in the GeometryStore's
`ways`, and its metres count in the length but in no way's `walked_m`. The snapshot holds the member ways of
every hiking relation (with graded paths at the `graded` level only in the TrailGraph), so at the default level a
bridge is a way another hiking relation lists; at the `trails` level and above it can be a path too
(ADR-0065). The ways are found by their end nodes (`WayEnds`, two sorted arrays, about 160 MB for Europe).

**Consequences.** A re-ingest, then every step after it. On a Jotunheimen extract, of 40 DayHikes exactly the 4
broken relations changed: Besseggen 7.6 → 14.1 km, JOT2 7.7 → 14.7 km, JOT4 12.0 → 20.5 km and JOT88 9.7 → 16.3 km;
a DestinationHike that the short Besseggen walk had covered is listed again. Over the Europe and US build,
an estimate on the TrailGraph finds 520 relation DayHikes with more than 1 km dropped whose pieces one way of
another relation joins (302 of them had lost more than half their length), and up to 1,159 when any short
TrailGraph Segment between the pieces counts; the ingest decides the exact number. A
DayStage cut from a tour that a bridge makes longer changes with it.

**Considered Options.** Join straight gaps up to 200 m (rejected: the walk would cross ground no way maps,
and a 200 m jump on a ridge is not a path). Join pieces over the TrailGraph (rejected: it is built after the
DayHikes). Bridge from any node of a way, not only its ends (rejected: it needs every node of the snapshot
indexed, about 83 M in Europe, at the ingest's memory peak; a way its mappers left out ends where the member
ways end).
