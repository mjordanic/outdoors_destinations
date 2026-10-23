# Implementation report: main-walk

- PRD: [PRD.md](PRD.md)
- Started: 2026-10-22T16:54Z
- Last updated: 2026-10-22T16:54Z
- Parallelism cap: 1
- Integration branch (BASE_BRANCH): main-walk (base commit a2cf43d)
- Harness: claude-code
- Isolation: inplace
- runner-model: sonnet (passed sonnet)
- implementer-model: per issue (user chose "mixed by complexity"; see wave plan)
- Firewall: intact
- Preflight assumptions:
  - Working tree clean after committing the plan (a2cf43d).
  - Ticket 08 runs the full re-ingest unattended (user chose ready-for-agent). Disk had 68 GiB free at start.
  - Ticket 08 names this same file as its report. It must add a "Rollout results" section here, not overwrite the orchestrator sections.

## Status

| ID | Title | Wave | Status | Agent ID | Worktree SHA | Integrated SHA | Started | Finished | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 01-one-main-walk-assembly | One main-walk assembly | 1 | committed | a64b7b00cb998b53e | c47c891 | c47c891 | 2026-10-22T16:55Z | 2026-10-22T17:01Z | inplace; suite 284 passed/22 skipped; no real-fixture diff run |
| 02-member-roles-and-each-way-once | Member roles and each way once | 2 | committed | af2b1c7c1fbbbe101 | 040e105 | 040e105 | 2026-10-22T17:01Z | 2026-10-22T17:10Z | inplace; suite 294 passed/22 skipped at last full run; no code-review |
| 03-main-walk-pieces-gaps-lines | Main walk on the way graph: pieces, gaps, lines | 3 | committed | ae77664fb8d044bd4 | ea3c2afb | ea3c2afb | 2026-10-22T17:10Z | 2026-10-22T17:40Z | inplace; suite 307 passed/22 skipped |
| 04-loops-shortcuts-stems-start | Loops: shortcuts, stems, start near access | 4 | committed | a3448c4088af15f0b | d2eea94 | d2eea94 | 2026-10-22T17:29Z | 2026-10-22T18:05Z | inplace; suite 318 passed/22 skipped |
| 05-surface-mix-and-tarmac-follow-the-walk | SurfaceMix and TarmacApproach follow the walk | 5 | committed | a9b5a12bf57ccfa1c | 5624a3d | 5624a3d | 2026-10-22T17:46Z | 2026-10-22T17:50Z | inplace; suite 320 passed/22 skipped; no code-review |
| 06-opt-in-real-data-checks | Opt-in real-data checks | 6 | committed | a3beb9bac73187a16 | 846ab20 | 846ab20 | 2026-10-22T17:51Z | 2026-10-22T18:30Z | inplace; implementer report fence not parsed, committed per git |
| 07-notebook-04a-leftovers-by-reason | Notebook 04a: Leftovers by reason | 7 | committed | afffd067b7b2e8d3f | 605c727 | 605c727 | 2026-10-22T17:58Z | 2026-10-22T18:40Z | inplace; suite 321 passed/14 skipped |
| 08-rollout-and-report | Rollout and report | 8 | in-progress | ad81fca311b9ab466 | | | 2026-10-22T18:07Z | | inplace |

## Dependency graph

```
01 -> 02 -> 03 -> 04 -> 06 --+
              |     \-> 07 --+-> 08
              \-> 05 --------+
```

## Wave plan

1. 01-one-main-walk-assembly: sonnet -> sonnet
2. 02-member-roles-and-each-way-once: sonnet -> sonnet
3. 03-main-walk-pieces-gaps-lines: opus -> opus
4. 04-loops-shortcuts-stems-start: opus -> opus
5. 05-surface-mix-and-tarmac-follow-the-walk: sonnet -> sonnet
6. 06-opt-in-real-data-checks: sonnet -> sonnet
7. 07-notebook-04a-leftovers-by-reason: sonnet -> sonnet
8. 08-rollout-and-report: opus -> opus

## Activity log

- 2026-10-22T16:54Z preflight passed; plan approved (cap 1, inplace, mixed models).
- 2026-10-22T16:55Z wave 1 dispatched (01, sonnet).
- 2026-10-22T17:01Z 01 committed c47c891 on main-walk (inplace).
- 2026-10-22T17:01Z wave 1 summary merged (01 committed c47c891); wave 2 dispatched (02, sonnet).
- 2026-10-22T17:10Z 02 committed 040e105 on main-walk (inplace).
- 2026-10-22T17:10Z wave 2 summary merged (02 committed 040e105); orchestrator full suite: 295 passed, 22 skipped. Wave 3 dispatched (03, opus).
- 2026-10-22T17:29Z wave 3 runner handed back while implementer ran; orchestrator waited on git, found 03 commit ea3c2af and done/ file, ran full suite (307 passed, 22 skipped), marked committed.
- 2026-10-22T17:29Z wave 4 dispatched (04, opus).
- wave 4: 04 committed d2eea94 on main-walk (inplace).
- 2026-10-22T17:46Z wave 4 summary merged (04 committed d2eea94); orchestrator full suite 318 passed, 22 skipped. Wave 5 dispatched (05, sonnet).

- wave 5: 05 committed 5624a3d on main-walk (inplace).
- 2026-10-22T17:51Z wave 5 summary merged (05 committed 5624a3d); orchestrator full suite 320 passed, 22 skipped. Wave 6 dispatched (06, sonnet).
- wave 6: 06 committed 846ab20 on main-walk (inplace).
- 2026-10-22T17:58Z wave 6 summary merged (06 committed 846ab20); orchestrator full suite 320 passed, 14 skipped (8 WaterSource baseline checks removed). Wave 7 dispatched (07, sonnet).
- 2026-10-22T18:07Z wave 7 summary merged (07 committed 605c727); orchestrator full suite 321 passed, 14 skipped. Disk free 68 GiB. Wave 8 dispatched (08, opus).

## Outstanding follow-ups

- 01: Trailhead point still chosen in _dayhike_from_relation via access tree, not by the assembly; tickets 02-04 decide where it moves.
- 01: write_geometry_store and endpoint choice pass no access points to assemble_main_walk; ticket 04 must supply them.
- 02: _max_sac_scale still reads every member way regardless of role.
- 02: a way first listed as disconnected and listed again stays disconnected, not repeat; ticket 03 may revisit.
- 03: 04 should make b-e a shortcut (piece with no dead ends is walked whole now).
- 03: write_geometry_store uses work-file access points plus all parking; recheck for 04 stem/loop start.
- 03: _extend_node_chain kept only for similarity.trail_facts; remove in 05.
- 03: assembly speed ~1.7 s on synthetic 3,600-way relation; check total ingest time in 08.
- 03: assemble_main_walk takes an access BallTree; write_geometry_store passes work-file access points plus all parking; ticket 04 stem/loop rules must recheck that set.
- 03: pieces with no dead ends but >2 odd nodes already shed shortcuts; a-b-c-d-e-a-b-e-b still walks b-e until ticket 04.
- 03: _extend_node_chain kept only for similarity.trail_facts (ticket 05 removes).
- 04: access pass has more search points (dead-end ends, loop samples); check osmium extract and total ingest time in 08.
- 04: loop search points 5 km apart; access 2.5-5 km from a loop vertex can be missed. Retune if ticket 06 98% check fails.
- 04: write_geometry_store now takes access points from run_ingest (4-tuple), no longer adds all parking.

- 05: trail_facts loop detection = first/last vertex within GAP_JOIN_M; lollipop counts forward direction only. Recheck on real data in 06/08.
- 06: loop check treats a walk as a loop only when first and last vertex are within 50 m, so lollipops (stem + loop) are excluded; reasons for ids that left are recomputed from the Europe extract (osmium getid + run_ingest) and may be slow.
- 07: leftover metres per reason count only nodes on a walk (store keeps no other coordinates).
- 07: fixture has only the disconnected reason; a fixture with role/repeat/dead_end/shortcut relations would show all.
- 07: real data under data/ is the old ingest; ticket 08 re-ingest needed before notebook shows real Leftovers.

## Rollout results

Ticket 08, run 2026-10-22T18:11Z to 2026-10-23T04:01Z on the Europe snapshot
already on disk (same snapshot as the 2026-10-21 Catalog). The long id lists
are in [rollout_ids.md](rollout_ids.md).

### What ran

| Step | Command | Time | Notes |
| --- | --- | --- | --- |
| Baseline | `cp -c data/catalog.sqlite data/catalog.similarity_facts.pkl data/baseline/` | instant | GeometryStore not copied. 68 GiB free before the ingest. Lowest free space during the ingest was 52 GiB. |
| Ingest | `dayhike ingest data/osm/europe-latest.osm.pbf --catalog data/catalog.sqlite --fresh` | 2 h 10 min (was 105 min) | Cached `europe-latest.catalog.osm.pbf` reused (same filter list). relations, ways and nodes 35 min; work-file load 2 min; choosing endpoints 12 min (878,027 access search points); access extract and scan 4 min; assemble 46 min; write Catalog and GeometryStore 30 min. Peak RSS 6.6 GB. |
| DEM | `dayhike sample-dem` | 16 min | 42,076,699 of 42,105,125 vertices sampled |
| LandCover | `dayhike sample-landcover` | 11 min | 28,664,753 vertices |
| Climate | `dayhike sample-climate` | 3 h 51 min | 24,514,427 vertices |
| Facts | notebook 05 (nbconvert, output not saved over the committed notebook) | 3 min | 4,587 region rows (was 4,408) |
| Checks | `DAYHIKE_REAL_DATA=1 uv run pytest tests/test_real_data.py` | 87 min | 10 passed, 4 failed (see below). The `walk_measures` fixture takes about 80 min. |
| 04a | notebook 04a on the real data (nbconvert, not saved over) | 21 min | all five Leftover reasons present |

The ingest was given the unfiltered `europe-latest.osm.pbf`, not the
`.catalog.osm.pbf` the PRD names. Ingest then scans the cached hiking extract
and cuts the access disks from the full source. Given the hiking extract, the
access pass would have looked for roads only in that extract, which has
almost none. The 2026-10-21 baseline was built the same way.

New files: `data/catalog.sqlite` 130 MB, `data/catalog.geometry.sqlite` 20.7 GB
after sampling (5.0 GB before; `vertices` holds walked vertices only:
42,105,125 rows, 3,476,532 `ways` rows), `data/catalog.similarity_facts.pkl`
27 MB.

### DayHike count, by drop reason

Read from `catalog_meta.ingest_report` of each Catalog. Both runs saw the
same 245,409 hiking relations.

| | Before (2026-10-21) | After (main walk) | Change |
| --- | --- | --- | --- |
| Kept (DayHikes) | 155,844 | 161,599 | +5,755 |
| Dropped, length band | 84,051 | 77,603 | -6,448 |
| Dropped, superroute | 5,514 | 6,207 | +693 |

### DayHikes that left or joined (against `data/baseline/`)

3,012 left and 8,767 joined. All ids are in [rollout_ids.md](rollout_ids.md).

| Left because | DayHikes |
| --- | --- |
| Main walk over 20 km | 2,261 |
| Superroute | 561 |
| Main walk under 1 km | 188 (65 of them are stalled line walks, see "Not caught by the checks") |
| Kept by the rules when re-assembled without access points | 2 (1920172, 16868807; both over 20 km with access points, see failure 4) |
| Lack of an access point | 0 |

Of the 8,767 that joined, 435 are stalled line walks: relations whose real
main walk is longer, cut short into the band by the stall.

### Before audit next to after

Before values are copied from the PRD. After values come from a one-off
script over the new GeometryStore, using way lengths from
`europe-latest.catalog.osm.pbf`. Like the PRD's audit scripts, it is not
committed.

| Measure | Before (one-pass chain) | After (main walk) |
| --- | --- | --- |
| DayHikes | 155,844 | 161,599 |
| With any skipped way / any Leftover | 31,021 (19.9%) | 30,140 (18.7%) |
| `length_m` more than 5% / 25% off distinct member length | 31,824 (20.4%) / 20,712 (13.3%) | 14,620 (9.0%) / 4,839 (3.0%). Now each difference is Leftovers set aside by a rule. |
| Recovered by retrying skipped ways | 14,158 DayHikes, 75,851 km | Out-of-order listings are walked now. 49,153 DayHikes walk their ways in an order different from the listing. |
| Main route on a smaller piece | 2,747 | 0 by rule (longest piece). `disconnected` Leftovers in 5,326 DayHikes, 13,573 km. |
| Way listed and glued more than once | 11,594 DayHikes, 8,393 km | 0 walked twice (check passes). `repeat` Leftovers: 13,574 DayHikes, 57,696 listings, 8,664 km. |
| Relations totalling over 20 km in the Catalog | 3,414 | 781 (distinct member length over 20 km, main walk in the band). 2,261 left as over 20 km. |
| Single piece that can be walked once, all ways | 133,852 (85.9%) | 131,459 DayHikes have no Leftover (81.3%) |
| Extra pieces 50 m or less from the main piece | 42% of 14,097 (about 17,500 km) | Joined: 7,814 gap joins in 6,199 DayHikes, 115 km of drawn gap |
| Dead ends 1 km or shorter (DayHikes with branches) | 57% of 45,368 | `dead_end` Leftovers in 13,883 DayHikes, 7,870 km. In 12,071 of them (87%) they total 1 km or less. |
| Loops starting over 300 m from parking, parking 100 m or less from the loop | 22.5% (3,000 sample) | 4.81% of all 23,957 such walks. 0.05% (10 of 19,273) for loops closed on a shared node, 24.4% (1,142 of 4,684) for walks closed by a gap of 50 m or less (failure 3). |
| Steps between vertices over 2 km | 16 DayHikes, all inside a single way | 21 DayHikes, 25 steps, all inside a single way (table below) |
| Relations with any member role other than empty | about 3k | `role` Leftovers: 1,762 DayHikes, 7,911 listings, 1,528 km |
| DayHikes whose longest piece is over 20 km | 2,754 (1.8%) | 2,261 left as over 20 km |

Total `length_m` went from 788,075 km to 883,912 km, and the median from
3,537 m to 4,003 m. The sum of `unconnected_m` is 36,117 km.

### Leftover metres and DayHikes per reason

"Full km" is the whole length of each Leftover listing's way, from the extract.
"km on walk nodes" is what notebook 04a reports: only nodes that are also on
the walk have coordinates in the GeometryStore. A `repeat` way is walked once
under its first listing, so its metres are not in `unconnected_m`.

| Reason | DayHikes | Listings | Full km | km on walk nodes (04a) |
| --- | --- | --- | --- | --- |
| role | 1,762 | 7,911 | 1,528 | 463 |
| repeat | 13,574 | 57,696 | 8,664 | 7,415 |
| disconnected | 5,326 | 55,481 | 13,573 | 3,738 |
| dead_end | 13,883 | 46,781 | 7,870 | 2,450 |
| shortcut | 4,889 | 50,013 | 11,368 | 3,603 |

Most `shortcut` metres come from stalled line walks: the 1,395 stalled
DayHikes alone have 11,104 km of `unconnected_m`. 11,797 listings in 9,249
DayHikes are walked only in part (`walked_m` below the way length).

### Loop and line share

The assembly's loop/line decision is not stored. These shares come from the
stored walk:

| Walk shape | DayHikes | Share |
| --- | --- | --- |
| Closed on a shared node (assembly loop, or a figure-eight) | 26,010 | 16.1% |
| Closed by a gap of 50 m or less (first and last vertex are different nodes; the assembly treats these as lines) | 5,379 | 3.3% |
| Lollipop-like: not closed, and the walk revisits a node over a span of at least 50% of its length (stem plus loop) | 12,973 | 8.0% |
| Line | 117,237 | 72.5% |

Notebook 04a and the loop check use the first and last vertex within 50 m, so
they count 31,389 loops and 130,210 lines. They count lollipops as lines.

### DayHikes whose length changed by more than 25%

152,832 DayHikes are in both Catalogs. 17,562 (11.5%) changed by more than
25%: 16,111 longer and 1,451 shorter. 27,135 changed by more than 5%. The
median change factor of the 17,562 is 2.02.

- Longer: mostly DayHikes the one-pass chain cut short. The top ten went from
  about 1.0 km to 18-20 km, for example 12298788 Svētā Jēkaba ceļš 1 etaps
  (1.0 to 19.9 km) and 6996413 IceTrail (1.1 to 19.6 km).
- Shorter: 180 of the 1,451 are stalled line walks (wrong). By their largest
  Leftover reason, the rest are repeat 751, dead_end 330, shortcut 163,
  role 19, disconnected 2, and 6 with no Leftover. Example: 6011349
  Dom Paklenica - Jurline went from 15.5 to 3.9 km, as the PRD expects.

### Steps over 2 km inside a single way

All 25 steps run between two consecutive nodes of one walked way. None is a
jump between ways. 12 DayHikes cross a ferry way.

| DayHike | Step m | Way | Way tags |
| --- | --- | --- | --- |
| 18709253 | 16,153 | 233099302 | route=ferry (Alicante - Isla de Tabarca) |
| 19023911 | 6,346 | 1380120418 | no route or highway tag |
| 5546932 | 5,548; 2,771; 2,337 | 245527921 | route=ferry |
| 3221278, 5546931 | 4,262 | 238823503 | route=ferry (Halnekongen) |
| 6301217, 6313277 | 4,257 | 37299297 | route=ferry |
| 9377346 | 3,770 | 140336377 | highway=trunk (Westerscheldetunnelweg) |
| 5512388 | 2,806; 2,775; 2,032 | 26119387 | route=ferry (Gjendebåtane) |
| 13344920 | 2,433 | 285381875 | highway=track |
| 4442865 | 2,398 | 84722067 | highway=path (Levada do Pico Ruivo) |
| 8461359 | 2,369 | 417598143 | highway=track |
| 3038377 | 2,253 | 229909062 | highway=track |
| 6261468, 6261469 | 2,247 | 39839365 | route=ferry (Ritsem - Vaisaluokta) |
| 1765669 | 2,206 | 23464481 | route=ferry (Leoni - Possenhofen) |
| 6325968 | 2,203 | 425722279 | no route or highway tag |
| 9286486 | 2,195 | 525709629 | route=ferry (Westerschelde Ferry) |
| 1711482 | 2,097 | 126108924 | highway=tertiary |
| 16997711 | 2,063 | 171960195 | highway=path (old railway line) |
| 5457577 | 2,019 | 345088504 | route=ferry |

### Real-data check failures

10 of 14 checks pass: geometry for every DayHike, the WaterSource checks,
facts for every region DayHike, no way walked twice, every length in the band,
every Leftover has a reason and every walked way a walk order, and the
left/joined listing. Four fail:

1. **`test_notebook_05a_passes_every_verification_on_the_real_facts`**: "pair
   names must be unique osm_name DayHikes". Three pair DayHikes left the
   Catalog as over 20 km: 1966959 Kurtagić - Vaganj - Vrdovo (WATER_MORE),
   16364831 Mala Kočevska planinska pot (WATER_MORE), and 1550539 Welschlauf
   Südsteiermark (TRAIL_MORE). Follow-up: re-pick those pairs in notebooks 05
   and 05a from DayHikes still in the Catalog, as the WaterSource re-ingest
   did.
2. **`test_no_step_between_consecutive_ways_is_over_50_m`**: 19839380 and
   19885873, a 101 m step each. This is a false positive. Nodes 13305837749
   (end of way 433058087) and 4319747989 (start of way 1450809737) have the
   same coordinates, so the assembly joins them with a 0 m gap. The vertex
   builder drops the repeated point. The stored step 13305837749 to
   13305837708 is then really the first segment of way 1450809737, but the
   check compares node ids. Follow-up: the check should treat a step as on a
   way when its first vertex has the same coordinates as that way's node, or
   the assembly should keep the entering way's node id.
3. **`test_a_loop_by_a_parking_point_starts_within_300_m_of_one`**: 4.81% (1,152
   of 23,957; limit 2%). 1,142 of the failures are walks whose first and last
   vertices are different nodes within 50 m. The closing gap is inside one
   piece, and rule 3 joins gaps only between pieces, so the assembly treats the
   loop as a line and puts the Trailhead at one of the two ends. Loops closed
   on a shared node fail 0.05% (10). Follow-up, needs a PRD decision: close
   a piece whose two ends are 50 m or less apart with a gap edge, so it can be
   a loop. Or have the check count only loops closed on a shared node.
4. **`test_no_dayhike_left_the_catalog_for_any_reason_but_the_main_walk`**:
   1920172 and 16868807 are "kept by the main-walk rules" when re-assembled
   without access points (15,238 m and 19,907 m). With the GeometryStore's
   parking as access points they are 20,074 m and 20,492 m. A stem whose far
   end is near parking is kept, and it pushes them over 20 km. No DayHike
   left for lack of an access point, but an access point can push a DayHike
   out of the band through its stem, which the PRD's "an access point never
   decides whether a DayHike is kept" does not foresee. Follow-up:
   `_main_walk_reasons` should assemble with parking as access points, and
   the PRD/ADR should say whether stems count toward the band.

### Not caught by the checks

- **Stalled line walks (assembly bug).** 1,395 Catalog DayHikes, plus 65
  relations that left as under 1 km, have a line whose odd nodes cannot be
  cut down to two. The usual shape is a small cycle hanging off the line by a
  bridge edge. Removing the bridge would split the piece (rule 7). The cycle
  is not a dead end, so `_shed_shortcuts` finds no removable path, and `_walk`
  falls back to the member-order greedy walk. That walk stops early, and the
  rest of the piece becomes `shortcut` Leftovers. Examples: 15021932
  Hermannshöhen - Etappe 02 (19.8 km of ways; 4.0 km walked, 15.7 km
  `shortcut`) has a 26 m loop on an 11 m bridge. 6536559 Jahrhundertweg and
  13492306 Las Tejoneras stall the same way. Of these 1,395, 435 joined the
  Catalog only because the stall cut them into the band. Follow-up, needs a
  rule: set aside a cycle hanging off a bridge (bridge plus cycle) like a dead
  end, or let the walk go out and back over a short bridge. Then re-ingest.
  The ids are in [rollout_ids.md](rollout_ids.md).
- **The access extract covers one place only (bug from before this feature,
  a804dc4, 2026-09-19).** `_write_access_geojson` writes one Feature per
  endpoint box in a FeatureCollection, and `osmium extract --polygon` uses
  only the first Feature. Checked on the Croatia extract with two boxes: only
  the first was extracted. On Europe the access extract was 6 MB, around
  lon 13.75-16.63, lat 48.34-48.62. Only 205,307 road vertices became access
  points. Everywhere else, access points are only parking within 5 km of a
  search point. The 2026-10-21 baseline has the same bug, so the before/after
  comparison is like for like. Line Trailheads, the 200 m stem test and loop
  starts do not see drivable roads outside that area. Follow-up: write one
  MultiPolygon from the union of the boxes (checked: osmium leaves out the
  overlap of two parts of one MultiPolygon, so union first), add a test with two endpoints
  far apart, and re-ingest. The access extract will then take much longer
  than today's 3 minutes.

### Rechecks asked by earlier tickets

- 03/04 ingest time: 2 h 10 min against 105 min before. Assembly runs three
  times (choosing endpoints, keeping DayHikes, writing the GeometryStore),
  about 1 h 30 min in all. The osmium access extract took 3 min only because
  of the access-extract bug above.
- 04 loop search spacing (5 km) against the 98% check: the spacing is not the
  cause of the failure. Loops closed on a shared node start near parking in
  99.95% of cases. The failures are gap-closed loops (failure 3).
- 05/06 lollipops: 12,973 lollipop-like walks are not loops for the loop check
  or for `trail_facts`. TarmacApproach measures them in the walk direction
  only, and so the 5,379 gap-closed loops. Of the lollipops that pass within
  100 m of parking, 485 of 11,928 (4.1%) start over 300 m from it. That is
  expected for long stems with no parking at their end.
- 07 notebook 04a on real data: runs in 21 min and shows all five reasons
  with an example each. Its metres count only walk nodes and are 14-72% under
  the full way metres (table above).

## Resume instructions

Re-run `/implement-issues .scratch/main-walk --cap 1`. Phase 2 reconciles from this report, `issues/done/`, and `<id>:` commits on `main-walk`.
