# Implementation report: main-walk-r2

- PRD: [PRD.md](PRD.md)
- Started: 2026-10-23T07:03Z
- Last updated: 2026-10-23T19:16Z
- Parallelism cap: 1
- Integration branch (BASE_BRANCH): main-walk (base commit a6a2049)
- Harness: claude-code
- Isolation: inplace
- runner-model: sonnet (passed sonnet)
- implementer-model: per issue (user chose "select by complexity"; see wave plan)
- Firewall: intact
- Preflight assumptions:
  - Working tree clean after committing the plan (b6e1247) and tickets (a6a2049).
  - Round-1 `<id>:` commits on main-walk use different ids (e.g. `01-one-main-walk-assembly`); only full round-2 ids count as matches.
  - Ticket 06 runs the full Europe re-ingest unattended (about 10 h) and deletes `data/baseline/` only if all 14 real-data checks pass. Disk had 63 GiB free at start.
  - Ticket 06 names this same file as its report. It must add a "Round 2" section here, not overwrite the orchestrator sections.

## Status

| ID | Title | Wave | Status | Agent ID | Worktree SHA | Integrated SHA | Started | Finished | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 01-parking-only-access | Parking-only access | 1 | committed | a8846600d75a64456 | 96e9ca2 | 96e9ca2 | 2026-10-23T07:04Z | 2026-10-23T07:21Z | inplace; 318 passed, 14 skipped; ingest work-file schema 5 |
| 02-dead-end-tip-join | Dead-end tip join: gap-closed routes are loops | 2 | committed | abf2231218e3d93ac | 32daa63 | 32daa63 | 2026-10-23T07:24Z | 2026-10-23T07:31Z | inplace; 322 passed, 14 skipped; 4 new build_catalog fixture tests; code review done inline by implementer |
| 03-bridge-and-cycle-on-a-line | A cycle hanging off a line is a dead end (no stalled walks) | 3 | committed | aa2b8cbfd17ceb98e | e0b023c | e0b023c | 2026-10-23T07:34Z | 2026-10-23T08:00Z | inplace; 327 passed, 14 skipped; 5 new fixtures in tests/test_main_walk.py; real-data re-assembly of the 1,395 round-1 stalled relations: 0 stall (1,187 before) |
| 04-real-data-checks-agree-with-ingest | Real-data checks agree with ingest | 4 | committed | aafecdef72debf525 | 3b4a673 | 3b4a673 | 2026-10-23T08:03Z | 2026-10-23T08:11Z | inplace; 331 passed, 14 skipped; new tests/test_real_data_checks.py (3 fixture tests); full 80-min walk_measures pass not run (ticket 06) |
| 05-stale-sweep | Stale sweep after the main walk and parking-only access | 5 | committed | a9cdbfb978b91ede0 | 9ced8f7 | 9ced8f7 | 2026-10-23T08:14Z | 2026-10-23T08:26Z | inplace; 331 passed, 14 skipped; text-only sweep (code comments, docstrings, README, GLOSSARY.md, ADR-0005, notebook markdown, one fixture comment); no behaviour change |
| 06-round-2-rollout-and-report | Round 2 rollout and report | 6 | committed | a711ca5b8414355e9 | 08a9f0d | 08a9f0d | 2026-10-23T08:30Z | 2026-10-23T19:09Z | inplace; 331 passed, 14 skipped; 14 of 14 real-data checks pass; 0 member-order fallbacks (round 1: 1,395); loop-start failures 0.42%; data/baseline/ deleted; "Round 2" section added; stopped by the usage limit 3 times, resumed each time |

## Dependency graph

```
01 ─┬─> 02 ─┐
    ├─> 03 ─┼─> 05 ─> 06
    └─> 04 ─┘
```

## Wave plan

Graph waves are {01}, {02, 03, 04}, {05}, {06}; cap 1 splits the second into three sub-waves.

1. 01-parking-only-access: opus -> opus
2. 02-dead-end-tip-join: opus -> opus
3. 03-bridge-and-cycle-on-a-line: opus -> opus
4. 04-real-data-checks-agree-with-ingest: sonnet -> sonnet
5. 05-stale-sweep: sonnet -> sonnet
6. 06-round-2-rollout-and-report: opus -> opus

## Activity log

- 2026-10-23T07:03Z preflight passed (claude-code, main-walk clean at a6a2049, uv 0.11.13, permissions present, 63 GiB free); plan written (cap 1, inplace, models by complexity).
- 2026-10-23T07:04Z wave 1 dispatched (01, opus; runner sonnet, runner agent acaea2b56256f2b48).

- 2026-10-23T07:21Z wave 1: 01-parking-only-access committed as 96e9ca2 on main-walk (inplace, no cherry-pick needed; subject and tree verified, no trailers).
- 2026-10-23T07:24Z wave 1 summary merged (01 committed 96e9ca2); orchestrator full suite 318 passed, 14 skipped. Wave 2 dispatched (02, opus).

- 2026-10-23T07:31Z wave 2: 02-dead-end-tip-join committed as 32daa63 on main-walk (inplace, no cherry-pick needed; subject and tree verified, no trailers, working tree clean apart from this report).
- 2026-10-23T07:34Z wave 2 summary merged (02 committed 32daa63); orchestrator full suite 322 passed, 14 skipped. Wave 3 dispatched (03, opus).

- 2026-10-23T08:00Z wave 3: 03-bridge-and-cycle-on-a-line committed as e0b023c on main-walk (inplace, no cherry-pick needed; subject and tree verified, no trailers, working tree clean apart from this report).
- 2026-10-23T08:03Z wave 3 summary merged (03 committed e0b023c); orchestrator full suite 327 passed, 14 skipped. Wave 4 dispatched (04, sonnet).

- 2026-10-23T08:11Z wave 4: 04-real-data-checks-agree-with-ingest committed as 3b4a673 on main-walk (inplace, no cherry-pick needed; subject and tree verified, no trailers, working tree clean apart from this report, issue file in done/).
- 2026-10-23T08:14Z wave 4 summary merged (04 committed 3b4a673); orchestrator full suite 331 passed, 14 skipped. Wave 5 dispatched (05, sonnet).

- 2026-10-23T08:26Z wave 5: 05-stale-sweep committed as 9ced8f7 on main-walk (inplace, no cherry-pick needed; subject, tree and trailers verified, no Co-Authored-By, working tree clean apart from this report, issue file in done/).
- 2026-10-23T08:30Z wave 5 summary merged (05 committed 9ced8f7); orchestrator full suite 331 passed, 14 skipped; 64 GiB free. Wave 6 dispatched (06, opus).
- 2026-10-23T10:38Z (approx.) wave 6 runner and implementer stopped on the claude.ai usage limit; fresh Europe ingest had finished (126.7 min, kept 161,614); sampling not started.
- 2026-10-23T12:00Z orchestrator reconcile: no 06 commit, no live process, data/baseline/ intact, 78 GiB free. Wave 6 runner resumed by message; asked to resume implementer a711ca5b8414355e9 from sampling onward (no re-ingest).
- 2026-10-23T14:50Z wave 6 runner and implementer stopped again on the usage limit (resets 16:40Z). Detached sampling kept running: DEM 24 min, LandCover 12 min, climate 234 min, all exit 0, done 16:30Z. The 12 checks that need no samples had already passed (1 h 35 min, run during sampling); facts, 05a re-pick, the 2 sample checks, Round 2 section and commit still pending.
- 2026-10-23T17:05Z orchestrator reconcile: no 06 commit, no live process, GeometryStore 20.7 GB sampled, data/baseline/ intact. Wave 6 runner resumed by message again; asked to resume implementer a711ca5b8414355e9 from the notebook 05 facts rebuild onward.
- 2026-10-23T18:47Z wave 6 runner and implementer stopped a third time on the usage limit. Done before the stop: notebook 05 facts rebuilt (17:08Z), 05a pairs re-picked (notebooks 05/05a and .scratch/main-walk-r2/rollout_ids.md modified, uncommitted). The detached final real-data run kept going: 14 passed in 1:41:52, exit 0 at 19:02Z (scratchpad logs/checks_final.log).
- 2026-10-23T19:06Z orchestrator reconcile after the user re-logged in: no 06 commit, no live process, data/baseline/ intact. Wave 6 runner resumed by message; implementer to finish the Round 2 section, commit 06 and decide on data/baseline/.
- 2026-10-23T19:09Z wave 6: 06-round-2-rollout-and-report committed as 08a9f0d on main-walk (inplace, no cherry-pick needed; subject and tree verified, no Co-Authored-By or agent trailer, issue file in done/, notebooks 05 and 05a carry no saved outputs, data/baseline/ deleted, working tree clean before this report edit).
- 2026-10-23T19:16Z wave 6 summary merged (06 committed 08a9f0d); orchestrator full suite 331 passed, 14 skipped. Final cleanup: no owned issue-* worktrees or branches. Run complete: 6 committed, 0 failed, 0 blocked.

## Outstanding follow-ups

- From 01 (for ticket 05): notebook 01's "Ingest stages" markdown table still lists an access stage (endpoints, driveable highways).
- From 01 (for ticket 05): README lines 153, 782-793 and 832, and ADR-0005, still describe the access stage, the access extract, the `*.sqlite.access*` sidecars and driveable-highway Trailheads.
- From 01 (for ticket 04): in `tests/test_real_data.py`, `_main_walk_reasons` still assembles with no access points, and the loop-start check comment still mentions drivable-road access nodes.
- From 01 (pre-existing, stale scratch): `.scratch/ingest-perf/profile_croatia.py` passes `snapshot.access_points` as a list to `_pick_trailhead`, which expects a tree. Not touched.
- From 01 (decisions in commit body): `run_ingest` and the GeometryStore writer each build the access tree from the same parking; `.gitignore` access-sidecar line removed; notebook 01 resume cell now stops on `assemble:` instead of `access:`.
- From 02 (for ticket 06): real-data effect (gap-closed routes becoming loops, loop-start share under 2%) is measured only in the ticket 06 rollout.
- From 02 (process): implementer did the code review inline on both axes instead of spawning the two code-review sub-agents (diff small, nothing found). No type checker configured in the repo; checks were an import check plus the full suite.
- From 02 (decisions in commit body): tip gaps rank after piece gaps in member order; `_pairs_within_gap` is shared by `_gap_edges` and `_tip_gap_edges`; `_osm`/`Walk` test helpers gained an optional `roles` map.

- From 03 (for ticket 06): expect about 133 of the round-1 stalled DayHikes to leave the Catalog as over 20 km; count stalls (target 0; a local re-assembly of the 1,395 round-1 stalled relations gave 0, down from 1,187 before the commit).
- From 03 (accepted by design): when a line end is shorter than the ways to a hanging cycle (or hanging dumbbell), that end is set aside as `dead_end`, so the Trailhead may not be the line's member-order end. 9 re-assembled relations walk slightly less than their stalled member-order walk because each step chooses locally.
- From 03 (decisions in commit body): a bridge qualifies when its kept-side end still meets at least two other edges; least length set aside goes first, ties in member order; `_shed_bridge_side` in `src/dayhike/catalog.py` (iterative Tarjan), looped with the dead-end and shortcut steps from `assemble_main_walk`. ADR-0012 text already covered the rule, not edited. No type checker configured; pyright shows only pre-existing errors in `catalog.py`.

- From 04 (for ticket 06): the full real-data pass is the first full run of the new step rule, and the loop-start check has not run on real data under parking-only access.
- From 04 (accepted by design): the step check's excused join is inferred from dropped way-end nodes because the GeometryStore holds no coordinates for them; a real jump landing on a way end with no vertex of its own would also be excused (none in the stored round-1 data). An exact check would need node coordinates from the extract.
- From 04 (for ticket 05): notebook 01 stage table text still lists an access stage (already recorded from 01); notebooks 05/05a pairs not touched by 04.
- From 04 (decisions in commit body): `_main_walk_reasons` reads all parking from GeometryStore `amenities` (new `extract` and `geometry` parameters); `walk_measures` fixture body became `_measure_walks(geometry)`; module docstring now names the 2026-10-21 baseline (155,844 DayHikes) and points the report step at ticket 06; ticket 01's follow-up on `_main_walk_reasons` and the loop-start comment is resolved.

- From 05 (for ticket 06): re-pick the notebook 05/05a example pairs and refresh their data-driven numbers after the re-ingest; 05/05a markdown was reworded only, numbers untouched.
- From 05 (resolves): notebook 01 "Ingest stages" access row dropped; README access stage, access extract, `*.sqlite.access*` sidecars and driveable-highway Trailhead text rewritten; ADR-0005 carries a Round 2 note pointing at ADR-0012; GLOSSARY.md Trailhead entry says an access point is parking only.
- From 05 (pre-existing dead code, listed in the commit body, not deleted): `parse_osm`, `_take_node`, `_take_way`, `_member_points` and `OsmSnapshot.merge` in `osm.py` (only `.scratch/ingest-perf/profile_croatia.py` calls `parse_osm`); `_scale_vector` in `catalog.py`; `sample_landcover_classes` in `landcover.py`; `IngestReport.dropped_unnamed` always 0 with an unused "unnamed" count key in `_keep_dayhikes`, and the `build_catalog` docstring still mentions unnamed `highway=path` ways in the report.
- From 05 (decisions in commit body): `in_chain` column and `MainWalk.in_chain` field keep their names because renaming changes stored data; `_positions_along` in `similarity.py` keeps `chain_lat`/`chain_lon` parameter names to match its docstring; two comments in `tests/test_main_walk.py` about "a one-pass chain" explain the regression and stay.
- From 05 (process): implementer did the code review inline instead of spawning the two code-review sub-agents (docs and comments only). No type checker configured.

- From 06 (misleading message): given a `*.catalog.osm.pbf`, ingest prints "filter: skipped (not PBF)" although the file is PBF and is read as is.
- From 06 (stale README): the section "Re-ingest after an ingest change" still gives the WaterSource-era command, its timings and "nothing but the WaterSources may change".
- From 06 (.gitignore): `data/catalog.sqlite.work-journal`, which exists while an ingest runs, is not covered.
- From 06 (loop-start check): 381 lines whose junction ends are within 50 m count as loops in the check. They pass today; checking only true loops would need the assembly's loop decision stored.
- From 06 (ingest time): ingest is only 3 min faster than round 1. Dropping the road scan saved 16 min, but the bridge step makes the two assembly passes about 11 min slower.
- From 06 (process): implementer did the code review inline (notebook text and report files only). Stalls and the loop/line share were counted by an uncommitted scratch wrapper around `build_catalog`; output unchanged.

## Round 2

Ticket 06, run 2026-10-23T08:31Z to 19:02Z on the same Europe snapshot as the
baseline and round 1. The usage limit stopped the run three times (see the
activity log); the ingest and each sampler ran once. The long id lists are in
[rollout_ids.md](rollout_ids.md).

### What ran

| Step | Command | Time | Notes |
| --- | --- | --- | --- |
| Baseline | none | | `data/baseline/` still held the 2026-10-21 Catalog (155,844 DayHikes) and its similarity facts. 64 GiB free before the ingest. The round-1 Catalog and facts were copied out for the round-1 comparison. |
| Ingest | `dayhike ingest data/osm/europe-latest.catalog.osm.pbf --catalog data/catalog.sqlite --fresh`, through a wrapper (below) | 2 h 7 min (round 1: 2 h 10 min) | Peak RSS 6.9 GB. No `*.access*` sidecar, no access extract and no work file left behind. |
| DEM | `dayhike sample-dem` | 24 min (16) | 42,218,932 vertices. Ran next to the first check run. |
| LandCover | `dayhike sample-landcover` | 12 min (11) | 28,746,824 vertices |
| Climate | `dayhike sample-climate` | 3 h 54 min (3 h 51 min) | 24,581,560 vertices |
| Facts | notebook 05, code cells run in order as `tests/test_demonstration_notebooks.py` runs them; no output saved | 2 min | 4,584 region rows (round 1: 4,587; baseline: 4,408) |
| Checks | `DAYHIKE_REAL_DATA=1 uv run pytest tests/test_real_data.py` | 1 h 42 min | 14 of 14 pass |

The ingest ran the CLI's `build_catalog` call through a wrapper kept out of the
repo. It time-stamps each progress line and records, for each assembled
relation, its walk length, the `_loop` decision, the dead-end tip gaps joined
and whether `_walk` fell back to the member-order walk. It changes no output.

New files: `data/catalog.sqlite` 130 MB, `data/catalog.geometry.sqlite` 20.7 GB
after sampling (5.0 GB before; 42,247,411 `vertices` rows, 3,454,463 `ways`
rows), `data/catalog.similarity_facts.pkl` 27 MB. 62 GiB free after sampling.

### Ingest time by stage

| Stage | Round 2 | Round 1 |
| --- | --- | --- |
| relations, ways, nodes | 37 min (0.5 + 14 + 22) | 35 min |
| choosing endpoints | removed | 12 min |
| access extract and scan | removed | 4 min |
| work-file load | 2 min | 2 min |
| assemble, keep DayHikes | 54 min | 46 min |
| write Catalog and GeometryStore | 33 min | 30 min |
| Total | 2 h 7 min | 2 h 10 min |

The two removed stages saved 16 min, but the two assembly passes (keeping
DayHikes, then writing the GeometryStore) took 11 min longer. The bridge step
(ticket 03) adds a depth-first search per step, and the wrapper counts each
walk's odd nodes once more. So ingest is only 3 min faster.

### DayHike count, by drop reason

From `catalog_meta.ingest_report` of each Catalog. All three ingests saw the
same 245,409 hiking relations.

| | Baseline (2026-10-21) | Round 1 | Round 2 | Round 2 − baseline | Round 2 − round 1 |
| --- | --- | --- | --- | --- | --- |
| Kept (DayHikes) | 155,844 | 161,599 | 161,614 | +5,770 | +15 |
| Dropped, length band | 84,051 | 77,603 | 77,561 | −6,490 | −42 |
| Dropped, superroute | 5,514 | 6,207 | 6,234 | +720 | +27 |

In round 2 the length drops are 69,105 under 1 km and 8,456 over 20 km.
"Superroute" includes `nwn` routes whose main walk is over 20 km.

- Against the baseline: 3,033 left (2,343 over 20 km, 568 superroute, 122
  under 1 km) and 8,803 joined. None left for another reason, so none for lack
  of parking.
- Against round 1: 149 left (124 over 20 km, 24 superroute, 1 under 1 km) and
  164 joined. 133 of the 149 are round-1 stalled walks now walked in full. The
  other 16 had a dead-end tip gap joined (ticket 02), which took 15 over 20 km
  and one, 6420175, under 1 km. 62 of the 164 that joined had left round 1 as
  under 1 km.

Total length 886,626 km (round 1: 883,912 km; baseline: 788,075 km), median
4,017 m (4,003 m; 3,537 m). Of the 161,450 DayHikes in both rounds, 735 changed
length by more than 25% (715 longer, 20 shorter).

### Real-data checks

All fourteen pass on the final data. The twelve that need no samples also
passed on a copy of the unsampled GeometryStore while sampling ran (1 h 35 min).

| Check | Result |
| --- | --- |
| every DayHike has a geometry of at least two vertices | pass |
| the GeometryStore holds every amenity kind with the WaterSources | pass |
| no stored WaterSource is tagged not drinkable | pass |
| springs and taps tagged as drinking water stay drinking water | pass |
| every WaterSource node in the extract is in the GeometryStore | pass |
| the facts table has Water facts for each region DayHike | pass |
| notebook 05a passes every verification line | pass (45 of 45) |
| no step between consecutive ways is over 50 m | pass |
| no way is walked twice in one DayHike | pass |
| every Catalog length is between 1 and 20 km | pass |
| every Leftover has a reason and every walked way a walk order | pass |
| a loop by a parking point starts within 300 m of one | pass, 0.42% |
| the DayHikes that left and joined are listed | pass |
| no DayHike left for any reason but the main walk | pass |

Round 1's four failures are gone: the 05a pairs are re-picked (below), the
101 m false step at 19839380 and 19885873 is excused (ticket 04), the loop
starts pass, and 1920172 and 16868807 now leave as over 20 km in ingest and in
the check alike (ticket 04).

### Stalled line walks

0 (target 0; round 1: 1,395 Catalog DayHikes). Counted during the ingest: the
wrapper checked every `_walk` call and counted the relations where more than
two nodes were still odd, which is exactly when `_walk` falls back to the
member-order walk. None of the 245,409 relations reached it, in the Catalog or
not.

Of the 1,395 round-1 stalled DayHikes, 1,262 are still in the Catalog (633 of
them more than 25% longer) and 133 left over 20 km, as ticket 03 expected (109
by the length band, 24 `nwn` routes as superroutes). Hermannshöhen - Etappe 02
(15021932) walks 19.7 km (round 1: 4.0 km), Jahrhundertweg (6536559) 14.3 km
(2.0 km) and Las Tejoneras (13492306) 8.7 km (1.4 km). Of the 428 stalled walks
that had joined the Catalog against the baseline (the round-1 report counted
435), 62 left. `shortcut` Leftovers fell from 50,013 listings in 4,889
DayHikes to 19,539 in 4,335, so they are real shortcuts now.

### Loop and line share

The assembly's decision, recorded by the wrapper (it is not stored): 42,849
loops (26.5%) and 118,765 lines (73.5%). 4,465 DayHikes have a dead-end tip gap
joined (ticket 02), and 3,563 of them are loops. Of the 5,379 walks that round
1 stored closed by a gap of 50 m or less and read as lines, 4,967 are loops
now, 402 are still lines and 10 left as over 20 km.

The stored walk, by the round-1 rules:

| Walk shape | Round 1 | Round 2 |
| --- | --- | --- |
| Closed on a shared node | 26,010 (16.1%) | 28,534 (17.7%) |
| First and last vertex different nodes 50 m or less apart | 5,379 (3.3%) | 2,421 (1.5%) |
| Lollipop-like: not closed, a node revisited over half the length or more | 12,973 (8.0%) | 13,678 (8.5%) |
| Line | 117,237 (72.5%) | 116,981 (72.4%) |

Of the 2,421 walks open by 50 m or less, 2,040 are loops that start at the far
end of a stem 50 m or less long, so the walk ends where the stem meets the loop.
The other 381 are lines whose ends are junctions the walk passes again, not
dead-end tips, so the tip join does not apply (for example 27971, 32878,
33544). The loop check counts all 2,421 as loops.

### Loop-start failure share

0.42%: 99 of the 23,643 loops that pass within 100 m of parking start over
300 m from it (target under 2%; round 1: 4.81%, 1,152 of 23,957). The check
counts 30,955 loops (first and last vertex within 50 m).

### Notebooks 05 and 05a

The text of both notebooks gave figures from the 2026-10-21 Catalog. They now
give the round-2 figures (05: WaterSources by height band and the Facilities
shares; 05a: every spread, pair score, the two rankings and the review table).
Notebook outputs are not saved.

Pairs re-picked on the criteria each was chosen for, as the WaterSource
re-ingest did:

| Pair | Was | Now | Why |
| --- | --- | --- | --- |
| Water, dry karst walks | Kurtagić - Vaganj - Vrdovo | Guge-Brezovac | left the Catalog (25 km) |
| Water, one WaterSource at the very end | Mala Kočevska planinska pot | Šegova pot | left the Catalog (53 km) |
| Trail, all-paved walk | Welschlauf Südsteiermark | 135 ~ Pot kurirjev in vezistov NOV Slovenije | left the Catalog (36 km) |
| Profile, single top off centre | Gozdna učna pot Planina–Mirna gora | Učna pot škrata Bisera | now a loop that starts at its top |
| Profile, flat | P16 | Iter Aquileiense - variante della Roggia | now 11.9 km with 540 m of climbing |
| Profile, rolling | Dom Paklenica - Jurline | Nacionalni park Sjeverni Velebit II - Rožanski kukovi | now 3.9 km with one climb |
| Weather, south-facing coast | Karst in Kornati | Baška Voda-magistrala(Novaci) | now a flat loop, southness missing |
| Weather, north-facing Julian Alps | Koča pri izviru Soče - Bavški Grintavec | Koča v Krnici - Križ | now 6 km, southness 0.42 |

The Seed "Aljažev dom - Kredarica - Triglav" now walks to the top of Triglav
(5.8 km to 2,800 m; it stopped at the Kredarica hut at 2,500 m before the main
walk), so the two ranked top fives and the several-Seeds result changed and are
described afresh. Notebook 05a passes 45 of 45 verification lines on the real
region; the Profile six-pair margin is 0.173 for lock-step and 0.161 for DTW,
so lock-step stays.

### `data/baseline/`

Deleted after all fourteen checks passed (143 MB: the 2026-10-21 Catalog and
its similarity facts). 63 GiB free afterwards.

### Follow-ups

- Given a `*.catalog.osm.pbf`, ingest prints `filter: skipped (not PBF)`,
  although the file is PBF and is read as is. The message should say that the
  extract is used as is.
- README "Re-ingest after an ingest change" still gives the WaterSource
  re-ingest's command (the unfiltered Europe file, which still works) and
  timings, and says the baseline checks allow only WaterSource changes. The
  round-2 timings above and the main-walk baseline checks could replace them.
- `data/catalog.sqlite.work-journal` is not ignored by `.gitignore` while an
  ingest runs (`*.sqlite.work` is).
- 381 lines whose ends are junctions within 50 m of each other count as loops
  in the loop check. They pass today; if they ever push the share up, the
  check could count only walks the assembly treats as loops, which needs the
  decision stored.

## Resume instructions

Re-run `/implement-issues .scratch/main-walk-r2 --cap 1`. Phase 2 reconciles from this report, `issues/done/`, and `<id>:` commits on `main-walk`.
