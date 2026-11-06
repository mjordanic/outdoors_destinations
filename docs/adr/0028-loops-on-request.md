---
status: accepted (amends ADR-0020; pages of rows: ADR-0032; loop budget: ADR-0036)
---

# SuggestedLoops only on request

ADR-0020 searched for SuggestedLoops inside every Ranking with a TrailGraph. On the Europe export, limited to 1 CPU like Cloud Run, that search takes 75–85 % of a Ranking's CPU: about 3.3 s on average, against about 0.35 s for the Catalog alone (hosting study for the public site, stage 1). Every visit paid for loops, whether the Hiker wanted them or not, and one instance served a fraction of the visits it could.

**A Ranking lists no loops unless asked.** `ServingData.rank` takes `loops` (default off) and searches for loops only with it on; `/api/rank` takes `"loops": true` and echoes `loops` in its answer. Off, a Ranking is the Catalog's alone: the same rows, grouped the same way, with no loop among them and no drive time asked for loop starts.

**The page asks on the Hiker's click.** Results come without loops. Under the results table, where the server has a TrailGraph, a button "Suggest loops near these hikes (takes a few seconds)" ranks the shown query again with `loops: true` and shows the merged table, as every Ranking did before. It asks the query of the table shown, even when the query on the left changed since, and the table stays marked stale then. Once loops are listed the button hides until the next Ranking.

**Unchanged.** The search itself, its bounds and its 5 s stop (ADR-0020). A Ranking with loops still counts against the per-client Ranking limit and runs one at a time. The CLI `dayhike compose` still lists loops, as it always asks for them.

Asking for loops in the background after every Ranking was rejected: it spends the same CPU on every visit, which is what this change saves.
