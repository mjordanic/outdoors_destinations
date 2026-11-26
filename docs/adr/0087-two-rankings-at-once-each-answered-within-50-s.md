---
status: accepted (amends ADR-0055 and ADR-0083: two Rankings at once, hike Rankings and BaseRankings together, each answered within 50 s)
---

# Two Rankings run at once per instance, and each is answered within 50 s

Each instance ran one hike Ranking at a time, and BaseRankings had a separate queue that also ran one at a time
(ADR-0055; the BaseRankings came with ADR-0084). A Ranking that waited 12 s for its turn got a 503 "busy" message. So one 3 h drive Ranking
from Munich (up to about 45 s on 1 vCPU, ADR-0083) made every other hike Ranking on the instance wait. The bound
"12 s of waiting plus 45 s of running stays under Cloud Run's 60 s timeout" also assumed that nothing else ran
on the instance at the same time. A BaseRanking running beside the hike Ranking already broke that assumption.

The captain approved launching with slow heavy searches (long drive limits, Find a base), as long as the number
running at once is limited. Now `RANKINGS_AT_ONCE` (2) is the one setting: hike Rankings and BaseRankings share
one queue, and at most two of them run per instance, first come first served. A third waits up to
`RANK_WAIT_S` (12 s) for one of them to end, then gets the 503 "The server is busy with other visitors'
searches: yours waited 12 s for its turn. Try again in a moment." The page shows that message beside the button
with a **Try again** button, as before. A browser tab may still have one hike Ranking and one BaseRanking queued
or running. An address may still have at most 4 Rankings in all.

**No request reaches Cloud Run's 60 s timeout.** Two heavy Rankings side by side on 1 vCPU each run slower than
one alone, so no fixed wait can keep the total under 60 s. Each Ranking runs on a worker thread instead. The
request is answered at `RANK_DEADLINE_S` (50 s after it came), counting both the wait and the run. A Ranking not
finished by then gets a 503 "The server is busy with other visitors' searches, and yours did not finish in time.
Try again in a moment." with `Retry-After: 5`. Such a Ranking cannot be stopped partway. It finishes in the
background and keeps its turn until it ends. So the limit of two still holds for memory and CPU. The same tab's
next Ranking gets a 409 until it ends, which the page waits out. Its drive answer is kept (`DRIVES_KEPT`), so
Try again from the same start is quick.

**Considered Options**: separate limits for heavy and light Rankings, which means more turns and more memory,
while a Ranking without a drive limit also scores every hike; a longer request timeout, or 2 vCPU, both
rejected by the captain in ADR-0083; stopping a late Ranking partway, which would need cancellation checks
through the drive and Ranking code. Rejected.
