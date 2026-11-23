---
status: accepted (amends ADR-0014: the drive filter is capped at 3 hours, not 4)
---

# The drive filter stops at three hours

The Europe and US deploy failed its smoke test: a Ranking within 4 h of Munich ran past Cloud Run's 60 s
request timeout (HTTP 504), on 1 vCPU and 4 GiB, with the export of 889,525 hikes. The other drive Rankings
took 19.3 s (2 h Ljubljana), 34.2 s (4 h Ljubljana) and 22.8 s (2 h Munich) there.

Most of a long drive Ranking is Valhalla (ADR-0051): on one local CPU, the 4 h Munich one spent about 12.7 s
of its 15 s on one isochrone and eleven matrix requests of 2,500 Trailheads near the limit, each a full search
from the Origin. Those requests stay as they are: Valhalla's matrix times depend on which targets share a
request, so batching them differently changes some drive times. The rest was made faster without changing any
answer (16.7 s to 14.9 s), and the drive times of the last 8 starts and limits are kept, so the next rows from
the same start take about a second. Cloud Run ran 3.7 to 5.5 times slower than that local CPU, so a 4 h
Ranking from Munich would still be about 55 to 80 s there.

The captain: "I prefer option 3. Reduce to 3 hours drive time". The API refuses `max_hours` over 3 (`422`,
"Input should be less than or equal to 3"), `/api/info` says `max_drive_hours` 3, the page offers 0.5 to 3 h,
and `web/smoke.py` checks 2 h and 3 h drives. A 3 h Ranking from Munich takes 8.3 s on one local CPU, an
estimated 30 to 45 s on Cloud Run, so a Ranking now waits at most 12 s for its turn (ADR-0055):
12 s and 45 s stay under the 60 s timeout, and more concurrent searches get the busy 503 sooner. A 3 h limit groups at 7.5 km by default (ADR-0032), not the 10 km a 4 h one
did.

**Considered Options**: a longer request timeout, 2 vCPU, or keeping 4 h on the faster code alone. Rejected by
the captain; 4 h stays out of reach of the 60 s timeout on 1 vCPU.
