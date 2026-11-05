# HikesAlike user guide

How to use the site, in plain words. For setup, running it yourself and how
the scores are worked out, see the [technical docs](technical.md).

## What HikesAlike does

You give it a hike you liked. It finds the hikes most like it.

Don't have a hike in mind? Describe the one you want instead, and it finds
hikes that fit.

- It looks at length, climbing, the highest point, the landscape and the
  typical temperature in the month you pick. If you ask for more, it also
  looks at rain and sunshine.
- It lists day hikes and longer tours. A long tour is also cut into day
  stages (8 to 25 km each). Hikes joined from signpost networks are included
  too. Anything over about 25 km is usually more than a day.
- It covers Europe wherever OpenStreetMap maps hiking routes. That is dense
  in the Alps and central Europe, and thin in much of the east and
  south-east.
- It is free: no ads, no account, no cookies, and no tips or donations asked
  for. The code is open source
  ([MIT](../LICENSE)) and the data it uses is credited
  ([credits](../src/dayhike/web/static/credits.html),
  [DATA-LICENSES.md](../DATA-LICENSES.md)).

The first visit after a quiet spell can take up to about 20 seconds, while
the server wakes up.

## How to search

### Start from a hike you liked

Pick one of three ways:

1. **Type its name** in the search box.
2. **Tick "Select hike on map"**, zoom in and click a hike. Hikes appear once
   you zoom in far enough that fewer than about 100 are in view.
3. **Press "Design your own from segments"** and draw a walk along the marked
   trails. You can then use that walk as your liked hike.

Then press **Find similar**. You get the 10 most similar hikes.

### Or describe the hike you want

No particular hike in mind? Press **"No particular hike in mind? Describe the
one you want"** under the search.

Example: you want a hike of about 10 km with 800 m of climbing and a hut on
the way. Type 10 for Length, 800 for climbing and tick the hut box. Press
**Find hikes**.

Everything starts at "ask for nothing". A measure only counts once you give it
a value. **Back to a hike you liked** takes you back to the normal search.

### Say what matters to you

Each thing the site compares (length, climbing and so on) has a slider from 0
to 100.

- 100 means "this matters a lot". 0 means "ignore it".
- You see a short list first: Length, Climbing, Highest point, Landscape,
  Typical temperature, and Lake or sea.
- Open **Advanced search** for more, grouped by topic. These only count while
  it is open.

Example: you care about huts more than anything. Open Advanced search and set
the hut slider to 100. Turn Length down to 30.

### Other choices

- **Month.** Pick the month you plan to hike. Temperature (and, in Advanced
  search, rain and sunshine) are compared for that month. If you leave it
  out, the site uses June to August.
- **Return to start** (on by default). Every hike is compared with its walk
  back to where it started. A hike that goes up to a summit and the same way
  down counts as there and back. Switch it off to compare every hike one way
  only.
- **Where from, and how far.** Give a starting place and a maximum drive (up
  to 4 hours), a maximum distance in a straight line (up to 200 km), or both.
  Only hikes that start within those limits are kept. Type the place as
  "lat, lon", use your browser's location, or use "Find place" to look up a
  town or village. The drive time is an estimate based on main roads, so
  treat it as a rough guide. The limits stay set when you open another hike
  in the same tab, and the note above the results always names them, for
  example "within a 2 h drive of Munich, DE". A fresh page starts with none.
- **More options.** Under the limits, this sets how close hikes must start to
  be grouped as one area (see "Reading the results" below).

## Download and share a hike

Open any hike from the results. It has a **Link to this hike**, and two
buttons.

- **Share** sends the link to your friends. On a phone it opens the share
  sheet. Where there is none, or it does not open, it copies the link, and the
  button says "Link copied".
- **Download GPX** saves the hike as a file you can open in most hiking and GPS
  apps.

The link opens the same hike. A walk you drew opens the same walk, too.

Good to know: the GPX file holds the line of the hike as you see it on the
map, without heights, and not the walk back to the start. The route data comes
from OpenStreetMap, and the file says so.

## One way or with the way back

The search box lists every hike one way, as it is walked: "5.3 km one way,
↑ 860 m ↓ 51 m". With Return to start on, the hike's panel and the results
count the walk back to the start too, and say so: "↑ 860 m ↓ 854 m with the
way back". With it off they say "one way". A hike that ends where it starts
has no way back to add.

In the route designer, the climb of a walk you are drawing is an estimate
from its segments ("about ↑ 52 m ↓ 86 m"). Once you use it, or open a loop
with **Edit on the map**, the designer shows the same measured climb as the
hike's panel ("↑ 54 m ↓ 59 m, as measured").

## When something goes wrong

If the server does not answer within a minute, or cannot be reached, the
page says so instead of waiting forever. A search that fails says so above
the list it leaves, which is still the list from before: press **Find
similar** to try again.

## Tips

- Not happy with the results? Change one slider at a time and press **Find
  similar** again. The results move with your weights.
- A long tour is also listed as day stages. A stage's name reads "…, stage 2:
  from – to".
- Planning for winter or summer? Set the **Month**.
- Narrow with a drive limit if a result list is huge and far away.
- The address in your browser holds your hike or your drawn walk, so you can
  bookmark it.
- Hiking is risky. Read the [terms and safety](../src/dayhike/web/static/terms.html)
  page before you go. Trails change, and the map is OpenStreetMap, which anyone
  can edit.

## Where the data comes from

Routes, huts, water and the like come from OpenStreetMap. Heights come from
FABDEM, land cover from ESA WorldCover and climate from CHELSA. The full
credits are on the site's credits page and in
[DATA-LICENSES.md](../DATA-LICENSES.md).

## More details

### Reading the results

#### Every row is an area

Hikes that start close together are grouped into one row, so you don't see ten
near-copies of the same walk. The row shows the best one. Click the row to see
up to three other versions of it (var 1, 2, 3). Each is more than half a
different route.

The same walk is not listed twice. If a hike is already on an earlier page,
a near-identical copy of it does not turn up again on a later page.

How close counts as close? By default, 5 % of your distance or drive limit,
and at least 0.5 km. With no limit it is 10 km. "More options" lets you change
it from 0.1 to 50 km.

#### Scores

Each hike has a score, and each thing compared has its own score. Press the
**?** next to a score to see what it means.

- A higher score means more alike.
- A low score on one thing does not mean the hikes are different overall.
  It can mean they share something common, like not passing a lake.
- "No data" means we don't know that fact for the hike (climate and land
  cover do not cover all of Europe yet). That thing is simply left out of the
  score. When you describe the hike you want, a hike with no data for
  something you asked for scores 0 on it instead, so it is listed below the
  hikes that have that data.

#### Why the list changes from visit to visit

The 10 hikes listed are picked, not simply the top 10. Each search looks at
the best 30 areas and picks 10 of them. A better match is picked more often
than a weaker one, so the very best are nearly always there, and the rest
change from one search to the next. Hikes that match about equally well all
get their turn, instead of the same ones always winning by a hair. The list is
still in score order. Some rows have a small **picked** mark next to their
number. A row marked picked would not appear in Strict order; a line beside
Redraw says so whenever the list has one.

- **Redraw**, above the list, picks again from the same 30 areas. It is
  instant and keeps any loops already suggested. If no other hike is close
  enough to come up, it says "No other close matches here; widen the distance
  or turn on Strict order".
- Tick **Strict order** to see the best 10 in the exact order of their
  scores, the same every time. Redraw is off while it is ticked, and a line
  beside it says why.
- Redraw and Strict order change the first 10 rows and remove the rows "Show
  10 more" added; press it again for the next ones.
- When the best areas score exactly the same, the note above the list says
  so. Their order among themselves means nothing.
- A link to a hike or a wish keeps Strict order when it is ticked. Without
  it, whoever opens the link gets their own pick.

#### "Show 10 more" and the counts

The list shows 10 areas at a time. Press **Show 10 more** for the next 10
(up to 100 rows), picked the same way from the areas not shown yet. Two counts tell you what is not on the list.

**"12 more good matches start here"**

Example: a row reads "3 versions · 12 more good matches start here".
That means 12 more hikes start in the same area as this row, and score
nearly as well. They are not listed, and later pages will not list them
either. They are there so you know the area has plenty to offer.

**"24,113 more hikes start in other areas"**

This is next to "Show 10 more". It is how many hikes the next pages can
still show you, all from other areas.

#### Why one start can show 4 to 8 hikes

Many hikes in the signpost networks start from the same place. Each start
keeps 4 of them. Slots 5 to 8 are not filled with more of the same.
Each of them is kept only when it leads to a new named place (a peak, a hut, a
viewpoint or a lake) that the other hikes there don't reach.

Example: from one car park, four hikes cover the nearby lake and two peaks. A
fifth is added only if it goes to a hut none of the four reaches. If there is
no such hut, you see 4.

### Loops

#### What a loop suggestion is

Under the list, a button says **"Suggest loops near these hikes (takes a few
seconds)"**. It looks for loops drawn on the marked trail network, near the
start you gave (if any) and near the best results. They are marked
"composed" and drawn dashed green on the map. A loop is named after a hiking
place near its start, or else the trails it walks.

Press **Edit on the map** to take a loop into the route designer and change
it.

A loop that mostly walks your liked hike again is not suggested.

#### Why loops can change

Searching every possible loop would take too long. So the site picks around
40 starting points near your start and near 20 of the best 60 results, and
looks for loops from those. The 20 are picked like the rows of the list, the
better ones more often, so a new search can find other loops. In Strict order
they are the 20 best.

- Near-ties matter. Two start areas may score almost the same. After a data
  rebuild, a tiny score change can swap their order, so a different set of
  starts gets searched, and a different loop can show up (or one can
  disappear).
- The search has a time limit of 5 seconds. If it hits that limit, it lists
  what it found so far.

So the loops are a good list of ideas, not every loop there is. A note above
the list says this too, and tells you how many starts it searched from, how
many loops it found and how many are listed.

Example: "Loops: searched from 31 starts near the best hikes, 14 found, 3
listed". Not all 14 are listed. The others rank lower, or walk mostly the
same trails as a listed hike.
