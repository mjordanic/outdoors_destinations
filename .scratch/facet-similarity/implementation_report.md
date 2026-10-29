# Implementation report: facet-similarity

- PRD: [PRD.md](PRD.md)
- Started: 2026-10-20 12:11
- Last updated: 2026-10-21
- Parallelism cap: 1
- Integration branch (`BASE_BRANCH`): facet-similarity
- Harness: claude-code
- Isolation: inplace
- runner-model: sonnet (passed `sonnet`, effort medium)
- implementer-model: per-issue `Model:` lines — opus (passed `opus`) for 01, 02, 07, 08; sonnet (passed `sonnet`) for 03, 04, 05, 06; effort high for all (Hiker asked to lower from xhigh)
- Firewall: intact
- Preflight assumptions: none; permissions already complete in `.claude/settings.local.json`

## Status

| ID | Title | Wave | Status | Agent ID | Worktree SHA | Integrated SHA | Started | Finished | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 01-core-effort-altitude | Facet scoring core with Effort and Altitude, end to end | 1 | committed | ae497f608cb75d9b3 | 4858c43 | 4858c43 | 2026-10-20 12:12 | 2026-10-20 12:31 | inplace; 163 tests pass; elevation gain direction-free (max of climb/descent) |
| 02-profile | Profile Facet (ProfileShape, lock-step vs DTW) | 2a | committed | ac65bc658b07ab593 | 47ba04f | 47ba04f | 2026-10-20 12:33 | 2026-10-20 12:49 | inplace; 176 tests pass; lock-step kept over DTW (gap 0.148 vs 0.121); soft dissimilar<=0.4 target missed (0.684) |
| 03-landscape | Landscape Facet and the Corridor | 2b | committed | a5e02c2623d1d6739 | e3b448d | e3b448d | 2026-10-20 12:51 | 2026-10-20 13:20 | inplace; corridor_amenities(vertices, amenities, kind=, radius_m=) reusable; main pair 0.942 vs 0.258; soft target missed on forest vs open karst (0.58) |
| 06-trail | Trail Facet (SurfaceMix and TarmacApproach) | 2c | committed | a6cc67d55f7aac59a | a3b0b65 | a3b0b65 | 2026-10-20 13:22 | 2026-10-20 13:38 | inplace; 247 tests pass; 27 verify PASS, soft targets met; main pair 1.000 vs 0.001; loops take shorter approach |
| 07-weather | Weather Facet (ClimateNormal slice and SolarAspect) | 2d | committed | a337ff0c0a300ef85 | 6feccd4 | 6feccd4 | 2026-10-20 13:40 | 2026-10-20 14:11 | inplace; 258 tests pass; 33 verify PASS; main pair 0.980 vs 0.138; southness missing on fixture (drops out), 1.1% missing in real region |
| 04-water | Water Facet (water count and DryStretch) | 3a | committed | ad9c8034955a1a3fa | 4d3745b | 4d3745b | 2026-10-20 14:13 | 2026-10-20 14:40 | inplace; main pair 0.965 vs 0.050, soft targets met; DryStretch direction-free; 05 and 05a extended; water_facts ~40 s for region |
| 05-facilities | Facilities Facet | 3b | committed | ad6539a8be30e0c5a | 3f12898 | 3f12898 | 2026-10-20 14:44 | 2026-10-20 15:14 | inplace; 279 tests pass; verify PASS in both notebooks; main pair 1.000 vs 0.000, soft targets met; one-flag differences score 0.8 (presence lenient) |
| 08-ranking-demo-and-summary | All-Facet ranking demo and verification summary | 4 | committed | a7c4a9becd78ed324 | 9d2d909 | 9d2d909 | 2026-10-20 15:16 | 2026-10-20 16:40 | inplace; 280 tests pass; 45/45 verify PASS on real region; resumed after usage limit; two FacetWeights sets top-5 overlap 2 of 5; coastal Seed takes whole top ten in two-Seed demo |

## Dependency graph

```
01 ─┬─ 02 ─────────────┐
    ├─ 03 ─┬─ 04 ──────┤
    │      └─ 05 ──────┼─ 08
    ├─ 06 ─────────────┤
    └─ 07 ─────────────┘
```

## Wave plan

1. Wave 1: 01-core-effort-altitude (opus → `opus`)
2. Wave 2a: 02-profile (opus → `opus`)
3. Wave 2b: 03-landscape (sonnet → `sonnet`)
4. Wave 2c: 06-trail (sonnet → `sonnet`)
5. Wave 2d: 07-weather (opus → `opus`)
6. Wave 3a: 04-water (sonnet → `sonnet`)
7. Wave 3b: 05-facilities (sonnet → `sonnet`)
8. Wave 4: 08-ranking-demo-and-summary (opus → `opus`)

## Activity log

- 2026-10-20 12:11 — preflight passed; plan approved by Hiker; report written with all issues pending
- 2026-10-20 12:12 — wave 1 dispatched: wave-runner agent ab719a9d8d042cb2d (sonnet, medium) for 01-core-effort-altitude
- 2026-10-20 12:31 — wave 1: 01-core-effort-altitude committed as 4858c43 (inplace, agent ae497f608cb75d9b3)
- 2026-10-20 12:33 — orchestrator verified 4858c43 (issue in done/, no trailer); wave 2a dispatched for 02-profile
- 2026-10-20 12:49 — wave 2a: 02-profile committed as 47ba04f (inplace, agent ac65bc658b07ab593)
- 2026-10-20 12:51 — orchestrator verified 47ba04f (issue in done/, no trailer); wave 2b dispatched for 03-landscape
- 2026-10-20 13:20 — wave 2b: 03-landscape committed as e3b448d (inplace, agent a5e02c2623d1d6739)
- 2026-10-20 13:22 — orchestrator verified e3b448d (issue in done/, no trailer); wave 2c dispatched for 06-trail
- 2026-10-20 13:38 — wave 2c: 06-trail committed as a3b0b65 (inplace, agent a6cc67d55f7aac59a)
- 2026-10-20 13:40 — orchestrator verified a3b0b65 (issue in done/, no trailer); wave 2d dispatched for 07-weather
- 2026-10-20 14:11 — wave 2d: 07-weather committed as 6feccd4 (inplace, agent a337ff0c0a300ef85)
- 2026-10-20 14:13 — orchestrator verified 6feccd4 (issue in done/, no trailer); wave 3a dispatched for 04-water
- 2026-10-20 14:40 — wave 3a: 04-water committed as 4d3745b (inplace, agent ad9c8034955a1a3fa)
- 2026-10-20 14:44 — orchestrator verified 4d3745b (issue in done/, no trailer; full suite 268 passed); wave 3b dispatched for 05-facilities
- 2026-10-20 15:14 — wave 3b: 05-facilities committed as 3f12898 (inplace, agent ad6539a8be30e0c5a)
- 2026-10-20 15:16 — orchestrator verified 3f12898 (issue in done/, no trailer); wave 4 dispatched for 08-ranking-demo-and-summary
- 2026-10-20 16:40 — wave 4: 08-ranking-demo-and-summary committed as 9d2d909 (inplace, agent a7c4a9becd78ed324, resumed after usage limit)
- 2026-10-20 16:50 — orchestrator verified 9d2d909 (issue in done/, no trailer on any branch commit); fixed 05-facilities row status (was left in-progress); full suite 280 passed; no worktrees to clean (inplace). Run complete: 8 committed, 0 failed, 0 blocked
- 2026-10-21 00:24 — after review, outside the wave run: WaterSources (springs, taps, water points) added to ingest and the Water Facet (5a88804), Europe re-ingested and re-sampled (ingest 105 min, sample-dem 15 min, sample-landcover 10 min, sample-climate 3.5 h), facts rebuilt; region no-WaterSource share 71% -> 60%; two Water verification pairs re-picked

## Outstanding follow-ups

- 01: FABDEM vertex gain may be inflated by noise (Vršič - Razor 1,997 m gain on 902 m range); consider smoothing before Profile Facet relies on it
- 01: Altitude reversed check is order-free by construction; only Effort exercises the vertex chain
- 01: facts-table join is written in both notebooks 05 and 05a; each later Facet ticket must extend both
- 01: no type checker configured
- 02: Profile soft target (dissimilar <= 0.4) missed (0.684); ticket 08 summary should list it as a known design point, not a failure
- 02: ticket 08 should reset one-Seed ranking weights, now Effort 50 / Altitude 30 / Profile 20
- 03: Corridor helper is `corridor_amenities(vertices, amenities, kind=, radius_m=)` in dayhike.similarity (distance_m only; Water must add position-along-line itself); 05/05a now both carry landscape facts
- 03: Landscape soft target missed on forest vs open karst (0.58, both-absent summit/Viewpoint flags agree); ticket 08 should list as known design point
- 06: trail_facts reuses private catalog._extend_node_chain; promote to public if later tickets need it
- 06: 05a ranking demo still weights only effort, altitude, profile (landscape and trail not added); ticket 08 should reset weights
- 07: ticket 08 ranking demo can add weather_score to FACET_SCORES; weather_score binds QUERY_MONTH at definition time, so re-run that cell after changing it
- 07: GLOSSARY.md says SolarAspect is not ClimateNormal, yet the Weather Facet groups them; consider a glossary/ADR-0011 line
- 07: southness on mostly flat walks rests on few sloped points; consider a minimum sloped-point count if noisy in Ranking
- 07: notebook 04 SolarAspect prototype still has the old west-as-south formula (out of scope per PRD)
- 04: water_facts takes about 40 s for the region; no water on 71% of hikes, so capped count is 1.00 for most random pairs
- 04: springs gap does not show strongly by altitude; notebooks say so (superseded 2026-10-21: with springs, hikes above 1,500 m with no WaterSource fell from 73% to 57%)
- 04: natural springs are not in the GeometryStore (needs ingest filter change and re-ingest); Water understates water for backcountry hikes (resolved 2026-10-21: 5a88804 keeps springs, taps and water points as WaterSources, Europe re-ingested and re-sampled, 228cd8d adds opt-in real-data checks; see PRD Amendments)
- 04: water_facts per-segment Python loop could be vectorized for the whole-of-Europe phase
- 04: full suite not re-run after a markdown-only notebook edit (resolved: orchestrator ran it, 268 passed)
- 05: Facilities presence is lenient (both-absent agrees), so a one-flag difference scores 0.8 and misses the 0.4 soft target; revisit only if FacetWeights demos need sharper separation
- 05: parking_trailhead true for 56% of hikes partly because the Catalog Trailhead is the endpoint nearer parking
- 05: facilities_facts takes about 30 s for the region; notebooks 05 and 05a both extended
- 08: in the two-Seed demo the coastal Seed takes the whole top ten (first alpine win at rank 36); a future demo could show per-Seed top lists
- 08: open design questions are listed in the 05a for-review cell

## Resume instructions

Re-run `/implement-issues .scratch/facet-similarity/`. Phase 2 reconciles the status table from `issues/done/`, `<id>:` commits on `facet-similarity`, and the Agent IDs above.
