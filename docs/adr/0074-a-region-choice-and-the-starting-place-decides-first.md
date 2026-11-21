---
status: accepted
---

# A region choice keeps a Ranking to Europe or the United States, and a starting place decides first

In a visitor test a European hiker planning a US trip liked Zugspitze and the Eigertrail and pressed Find
similar: all 20 results were Alpine. A Ranking is worldwide and Europe holds most of the hikes, so a US
hike like an Alpine favourite showed only with a starting place in the US, and nothing said so.

**The page has a region checkbox per region the Catalog has hikes in**: Europe and United States, beside the
starting place in step 3, just above Find similar. Every one is ticked as the page opens, which is anywhere,
the Ranking as before. Find sends the regions ticked (`regions` in `/api/rank`) only while not every one is,
and waits, saying why, while none is. A Catalog of one region shows no choice.

**The choice applies before ranking.** The server keeps the hikes starting in the regions asked to before it
ranks and draws, as it does with the drive limits: a US-only list holds the best US matches, not the US rows
left of a worldwide list. It applies to Find similar and to Describe the one you want alike.

**A hike's region is where it starts**: the United States west of 40° W, Europe east of it. The line runs
through the Atlantic, west of the Azores (31° W) and east of the contiguous United States (67° W), so it
needs no data the export does not have and no rebuild. The few hikes of the US build starting just across
the Canadian or Mexican border count as the United States, as their build does.

**A starting place decides first.** While "Only hikes near a starting place" is ticked with a place, the hikes
are those near it, wherever it is: the region choice is greyed out and says the starting place is used
instead, and Find sends no regions (the server refuses both in one request). The ticks stay as they were and
apply again once the place is cleared or the filter unticked. Ticking a region never changes the place.

**A link carries the choice** as `&regions=us` while not every region is ticked; a link without it, or the
home address, ticks every region (ADR-0073).

**Considered Options**:

- *An "Anywhere" control beside the regions.* Every region ticked already says anywhere; a third control
  would say the same thing twice and need rules for when it and the boxes disagree.
- *Regions from `countries.npy`.* Not every export has it, and the coordinates tell the two regions apart
  without it.
- *Filter the worldwide list after ranking.* A US-only list would then hold the few US rows left of the
  best 30 areas, often none.
