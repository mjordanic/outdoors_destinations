---
status: accepted (builds on ADR-0033 and ADR-0040)
---

# Multi-day routes are marked and listed after the day hikes

With no upper length cap (ADR-0033) a Ranking for a 13 km day hike listed the 1,073 km Traumpfad
München-Venedig and the 1,928 km Nordalpenweg: four of six visitors in visitor check 4 found that spoiled
"similar". Their DayStages are in the Catalog too (ADR-0040), so the day-sized pieces stay to be found.

**A multi-day route is a DayHike longer than 40 km one way (`MULTI_DAY_M`).** The threshold is from the data
and the DayStage split. On the review export (rebuild 4, 258,305 hikes), the 3,895 OSM relations named as a
day's stage (Etappe, stage, tappa, étape, etapa) are a mapper's own idea of a day: their median is 16.6 km, 95 %
are at most 28.6 km and 99 % at most 38.7 km. At 25 km, the longest DayStage and `STAGE_SPLIT_M`, 11 % of those
day stages would read multi-day; at 40 km under 1 %. A route over 40 km is also always cut into at least two
DayStages of at most 25 km, and 40 km is `LOOP_MAX_M`, the longest loop the site suggests (ADR-0035). Of the
relation DayHikes, 4,965 are longer (3 %). The length is the hike's own, one way: with Return to start a 25 km
line counts 50 km in the Measures but is a long day, not two.

**Marked, not hidden.** Every hike the API lists (search, Landmarks, the map, a hike's detail and a Ranking's
rows) says `multi_day` and its one-way `walk_m`. The page marks it "multi-day, 1,073 km": in a search
suggestion in place of its length, beside its name in a Ranking's row, first in a hike's panel, and in the
map's "choose one" list and tooltip.

**Listed after the day hikes.** Unless the Seed is itself a multi-day route, or a Wish asks a length over
40 km one way, a Ranking lists every multi-day route after every day-length hike, by score within each, and says
`day_first`. With Return to start a Wish's length is the whole walk, way back included, so it asks for a
multi-day route only over 80 km: a 45 km out-and-back Wish is a long day and still lists the day hikes first.
The scores are unchanged: a multi-day route still shows how alike it is, it only stops taking a
day hike's place. Rows, areas, loops near the best DayHikes, "Show 10 more" and the counts follow that order.
The page draws its rows from the pool by score (ADR-0047); it draws a multi-day route of a `day_first`
Ranking with its score less 1, so it is drawn only once the pool's day hikes run out, as in Strict order.

**Considered Options**: hide routes over about 40 km unless asked (a Hiker who wants the tour, or who liked
a stage of it, would not see it; the captain chose to mark and rank lower); 25 km, the DayStage split (one
mapped day stage in nine would read multi-day); a score penalty (changes every score shown and the "?"
explanations, and scores were out of scope); leave them (four visitors' finding). Rejected.
