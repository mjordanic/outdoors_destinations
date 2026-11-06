# HikesAlike user guide

How to use the site, in plain words. For setup, running it yourself and how
the scores are worked out, see the [technical docs](technical.md).

## What HikesAlike does

You give it a hike you liked. It finds the hikes most like it.

Don't have a hike in mind? Describe the one you want instead, and it finds
hikes that fit.

- It looks at length, climbing, the highest point, the landscape, the
  typical temperature in the month you pick, and whether the hike runs beside
  a lake or the sea. If you ask for more, it also looks at rain and sunshine.
- It lists day hikes and longer tours. A long tour is also cut into day
  stages (8 to 25 km each). Hikes joined from signpost networks are included
  too. Anything over about 25 km is usually more than a day.
- It covers Europe wherever OpenStreetMap maps hiking routes. That is dense
  in the Alps and central Europe, and thin in much of the east and
  south-east.
- It is free: no ads, no account, no cookies, and no tips or donations asked
  for. The code is open source
  ([MIT](../LICENSE)) and the data it uses is credited
  ([credits](https://dayhike-445079235401.europe-west1.run.app/credits.html),
  [DATA-LICENSES.md](../DATA-LICENSES.md)).

The first visit after a quiet spell can take up to about 20 seconds, while
the server wakes up.

## How to search

### Start from a hike you liked

Pick one of three ways:

1. **Type its name** in the search box. A small typo is fine: "Triglov"
   finds Triglav. When no name has every word as you typed it, the list says
   so and marks each name that is only like it "similar name". So does a name
   that holds your word only inside a longer word ("Rappenloch" in
   "Krappenlochklamm"): check that it is the hike you meant. **Enter** picks a
   hike only when one is named exactly as you typed, or it is the only hike
   found; the line beside the chosen hike then says so. Otherwise the list
   stays open and says how many hikes match, so you pick one (or use the arrow
   keys, then Enter). It works the same on a phone and a computer.
   Once picked, the page moves to the hike's panel.

   You can also type a **peak, hut, viewpoint, lake, pass, ridge,
   town or village** the hike goes to or starts from, in any language the
   place has a name in: "Gardasee", "Lake Garda" and "Lago di Garda" all find
   Lake Garda. Under the hike names the list then shows
   "Hikes near Zugspitze (peak, near Ehrwald, AT)" and the closest
   few hikes there, each saying how near it comes: "reaches the top", "passes
   300 m from it", or for a town "starts 1.2 km from it". Where several
   places share a name, the best known comes first (the one with names in
   the most languages, then the highest): "Mont Blanc" is the summit above
   Chamonix before the small hills of that name. A big lake, or a summit no
   trail reaches, lists the hikes up to 10 km away. A lake is measured from
   its middle, so a hike along the shore of a big lake can read several km. A
   typo is fine here too. When it lists only some of the hikes there, press
   **Show on map**: the map opens at that place, with every hike there to
   click.

   Each hike in the list says the town it starts near and its country ("near
   Igls, AT"), so hikes of one name are easy to tell apart. A route of several
   days says so: "multi-day, 1,073 km" (see "Routes of several days" below).
2. **Tick "Select hike on map"**, zoom in and click a hike. Hikes appear once
   you zoom in far enough that fewer than about 100 are in view. After you
   type a peak or place in the search box, or find a starting place, the map
   goes there, open or when you open it. Otherwise it opens on your liked
   hike, or else on your starting place, or else where most hikes are. Where
   two or more hikes pass under your click (a day hike and a long tour's
   stage along one trail, say, or two lines that cross), a click lists them
   all, the shortest first, and you choose one. Under the map it then says
   "Chosen: …".
3. **Press "Design your own from segments"** and draw a walk along the marked
   trails. Click a junction (a dot) or a segment to start, then segments to
   walk on. A click on another junction walks the shortest way there; a click
   on the green start dot walks back to the start. What each click did is said
   under the map, so the map stays still under your pointer. You can then use
   that walk as your liked hike.

Then press **Find similar**. You get 10 hikes from among the most similar
(see "Why the list changes from visit to visit" below), and the page moves to
the list. On a phone the list is wider than the screen: a line above it says to
scroll it sideways for every measure's score.

If you change a setting after a search, a line under **Find similar** says
the list is from before your change, so you see it where you are on a phone.

While **Find similar** is greyed out, the line under it says why: no hike is
chosen yet (choose one first), a hike is still loading, or a search is still
running. If your hike cannot be measured again after you change Return to
start, that line says so and **Find similar** stays usable: pressing it
measures your hike again, then finds.

If hikes on the list score below 0.43, what a typical pair of random hikes
scores, a note above the list says from which row down the matches are weak,
or that all are when even the best one is. This
happens most with a tight distance or drive limit: there is simply nothing
much like your hike nearby. A long tour can then show up on the list too.

### Or describe the hike you want

No particular hike in mind? Press **"No particular hike in mind? Describe the
one you want"** under the search.

It starts like the normal search: the main features (Length, Climbing,
Highest point and Typical temperature) are on at weight 100, each asking for a
typical value already in its box, and the Advanced ones are off. Change the
values you care about, and slide the ones you don't care about to 0. Lake or
sea starts off, since no share suits every hike: turn it on and set the share
you want.

Example: you want a hike of about 10 km with 800 m of climbing and a hut on
the way. Length already asks for 10; type 800 for Climbing. Slide Highest
point and Typical temperature to 0 if they don't matter to you.
Open **Advanced search** and click the "yes" box of Hut along: it already
shows "yes", and the click turns it on (a second click asks for no hut). Press
**Find hikes**.

Typing a value in a measure that is off switches it on, at weight 100. Sliding
its weight up without typing a value asks for the grey example value shown.
**Reset weights** brings back the starting values. **Back to a hike you liked**
takes you back to the normal search.

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
  out, the site uses June to August for temperature and sunshine, and the
  whole year for rain.
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

On a phone, "2. What similar means" starts folded away; like Advanced search and
More options, its title has an arrow and the word "show" while it is closed.

The filters a list was found with show as labels just above it: "Within 50 km
of Bled", "Weather in Jan", "One way, without the way back", "Grouped within
3 km". Press the × on a label to drop that filter; the list is found again
without it.

## Download and share a hike

Open any hike from the results. It has a **Link to this hike**, and two
buttons. The link and Share give the same address, with your settings as
they are now.

- **Share** sends the link to your friends. On a phone it opens the share
  sheet, and a line beside the button says "Opening your phone's share
  menu…". On a computer, or where the share sheet does not open, it copies the
  link, and a line beside the button says "Link copied". If the link cannot be
  copied, that line shows the link for you to copy.
- **Download GPX** saves the hike as a file you can open in most hiking and GPS
  apps.

The link opens the same hike. A walk you drew opens the same walk, too. The
link also carries your search settings: the month, the weights you changed,
Advanced search being open, Return to start, Strict order, the starting place
with its limits, and the grouping distance. Whoever opens it sees the same
search, ready for **Find similar**. A link to a hike you described works the
same way. The address in your browser keeps them up to date, so a reload
keeps them too. Older links without settings still open the hike.

A link that has been cut short or changed by hand says "This link could not be
opened" beside the search box. A part of a link the page does not know is
ignored, and the hike still opens. If you edit the address to other settings,
or go Back or Forward to one, a list found with other settings is marked "The
query changed since this ranking". Back to the home address shows the page as
it opens, with no hike and no list.

Good to know: the GPX file holds the line of the hike as you see it on the
map, with a height at every point. With Return to start on, it also holds the
walk back to the start as a second track, named "…: way back to the start",
so the file matches the length the page shows. With it off, the file is the
hike one way. The route data comes from OpenStreetMap and the heights from an
elevation map, and the file credits both. The first download of a walk back
along the trails can take a few seconds while the server reads the trails.

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
page says so instead of waiting forever. A search that fails says so under
the button you pressed (Find similar, or Show 10 more beside it), with a
**Try again** button, and above the list it leaves, which is still the list
from before.

The server runs one search at a time for everyone, in turn. When other
visitors' searches are ahead of yours for 25 seconds, it says "The server is
busy with other visitors' searches: yours waited 25 s for its turn. Try again
in a moment." Your search itself is quick: press **Try again**.

Each browser tab runs one search at a time. If you press Find again while your
earlier search is still running on the server, the page says "Waiting for your
earlier search to end…" and runs yours once it has. People on one network
(at home, in an office) do not wait for each other's searches. If it still
cannot run after a minute, the page says so, and the old list goes, so it is
never taken for an answer to the new search.

The **?** next to a score opens a longer explanation. While there is more of it
below the box's edge, the box says "More below ↓ scroll".

## Tips

- Not happy with the results? Change one slider at a time and press **Find
  similar** again. The results move with your weights.
- A long tour is also listed as day stages. A stage's name reads "…, stage 2:
  from – to".
- Planning for winter or summer? Set the **Month**.
- Narrow with a drive limit if a result list is huge and far away.
- The address in your browser holds your hike or your drawn walk, so you can
  bookmark it.
- Hiking is risky. Read the [terms and safety](https://dayhike-445079235401.europe-west1.run.app/terms.html)
  page before you go. Trails change, and the map is OpenStreetMap, which anyone
  can edit.

## Where the data comes from

Routes, huts, water and the like come from OpenStreetMap. Heights come from
FABDEM, land cover from ESA WorldCover and climate from CHELSA. The full
credits are on the site's credits page and in
[DATA-LICENSES.md](../DATA-LICENSES.md).

## More details

Optional reading: how the result list is put together, and how loops are
found. You do not need it to use the site.

### Reading the results

#### Every row is an area

Hikes that start close together are grouped into one row, so you don't see ten
near-copies of the same walk. The row shows the best one (var 1). Click the row
to see up to two other versions of it (var 2, 3). Each is more than half a
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
- "No data" means we don't know that fact for the hike. Most often it is the
  difficulty grade, which most trails don't have on OpenStreetMap; a hike with
  no grade scores 0 on difficulty against a hike that has one. Any other fact
  we don't know is simply left out of the score. When you describe the hike
  you want, a hike with no data for something you asked for scores 0 on it
  instead, so it is listed below the hikes that have that data.
- Open a hike to see every measure side by side with the hike you like (or
  the one you want). A greyed row is a measure that is not part of the score:
  it still shows the facts and its score, faded, and says why: "not counted
  (weight 0)", "not counted (Advanced search closed)", or, when you describe
  the hike you want, "not counted (cannot be asked)". A line above the table
  says how to count them: set their weight in step 2, or open Advanced search.

#### Why the list changes from visit to visit

The 10 hikes listed are picked, not simply the top 10. Each search looks at
the best 30 areas and picks 10 of them. A better match is picked more often
than a weaker one, so the very best are nearly always there, and the rest
change from one search to the next. Hikes that match about equally well all
get their turn, instead of the same ones always winning by a hair. The list is
still in score order. Some rows have a small **picked** mark next to their
number: that row is not one of the 10 best areas, but was drawn in for variety
from the best 30. Strict order would not list it; a line beside Redraw says so
whenever the list has one.

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

#### Routes of several days

A hike longer than 40 km one way is more than a day's walk: a long-distance
path or a tour of several stages. It is marked "multi-day" with its length,
for example "multi-day, 1,073 km", in the search list, in the results and in
its panel. When you start from a day hike, or describe one, these routes are
listed after every day-length hike, so they do not take a day hike's place;
their scores are unchanged. When you start from a multi-day route, or ask for
a length over 40 km one way (over 80 km with Return to start on, as the
length then counts the way back), they are listed by score like any other. Their day-sized
stages are listed as hikes of their own (see Tips).

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
"composed" and drawn dashed green on the map. A loop listed as a version of a
catalog hike is marked on that hike's row, as "composed var 2", until you click
the row to see it. A loop is named after a hiking place near its start, or else
the trails it walks.

Press **Edit on the map** to take a loop into the route designer and change
it. The designer opens zoomed in on the loop. Your liked hike and the list stay
as they are until you press **Use this hike**, which measures the drawing and
makes it your hike. **Undo** takes back only your own clicks, never a segment of
the loop you opened; **Clear** starts over. When the drawing is your liked hike
itself, Clear removes that hike too, so Find similar never uses a walk that is
no longer drawn.

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
- Loops are searched for the first 10 rows only. "Show 10 more" adds hikes,
  not more loops, so no later row's loop scores above the rows before it.
  Suggest loops keeps as many rows as you had.
- The search has a time limit of 5 seconds. The search for loops stops after
  3.5 of them, so the rest is left to measure the loops it found; if it hits
  that limit, it lists the best of what it found so far.

So the loops are a good list of ideas, not every loop there is. A note above
the list says this too, and tells you how many starts it searched from, how
many loops it found and how many are listed.

Example: "Loops: searched from 31 starts near the best hikes, 14 found, 3
listed in 2 rows: #2, #5 (marked composed)". Not all 14 are listed. The others
rank lower, are not drawn on this page (Redraw or tick Strict order shows
them), or walk mostly the same trails as a listed hike. When none makes the
list, the note says how the best loop found scores against the last row (below,
level with it to three decimals, or above), and why one that scores as well is
not listed. When the search finds none, it says "no loop found, so none is
listed".
