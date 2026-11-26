---
status: accepted (amends ADR-0054, ADR-0086 and ADR-0088)
---

# Both tools after the fourth visitor check: a Base row is the town it is scored for, quick changes never lock a visitor out

Three visitors (holiday planner, phone, breaker) re-tested both tools after ADR-0088. The captain chose to undo
round 3's row naming and to fix every bug and confusing item of their triage. The decisions that change how the
page behaves:

**A row of Find a base is the town it is scored for.** ADR-0088 named a row after the biggest town of its area
and said "X figures for Y" when that was not the town scored; every visitor misread it (the link named one town
and opened another, the people shown were the other's, towns 70 km apart were paired). A row is named after the
Base it is scored as again (ADR-0086), that is the town it opens and whose people, places to stay and eat and
hikes it shows, and its area's other towns are listed under it, biggest first, as before. Hamlets, which ADR-0088's
naming kept off the rows' names, are kept out by the default town size instead: **at least 1,000 people** and up
to 50,000, shown over the list as two chips ("Town size at least 1,000 people (the default)") and changeable in
step 4. A place of unknown size fails "at least", so it is out by default too (Foy, US, London's 15-min #1).

**Quick changes send only the latest ranking.** A change made while a tab's ranking runs used to send another at
once; the server refused it (one ranking per tab) and the page asked again every 1.5 s, each try counted against
the limit of 20 requests a minute per address, so five quick changes ended in "Too many requests; wait a minute"
and an empty list. Now one ranking per tab runs at a time, a change made meanwhile waits for it and only the
latest is sent. A refused request keeps the list shown and says "The site is busy right now; try again in a
moment." The limit itself is unchanged. While a list is found again, the old one is greyed and the line over it
says which town and drive it is being updated for.

**A map click over a list offers the town, it does not take it.** With a list shown, a click on the base map
looks up the nearest town and offers a "Choose" button; the town changes only on that click. With no list, a
click picks the town as before.

**"near X" only names a bigger town of the same country within 60 km.** ADR-0086 allowed 250 km and any
country ("Moab near Fruita, 96 km", "Garmisch-Partenkirchen near Hötting", a district of Innsbruck). With none,
no hint is said.

**Find a hike:** a result opened from the list is in the address (`&open=<id>`), so a copied address opens that
hike over the list; "Find place" offers every place of the name, several in one country told apart by the
bigger town they are near, and a much bigger place whose name starts with the one typed ("Frankfurt" offers
Frankfurt am Main), with countries in full, places with no hike within 50 km last (Keswick, Ontario: Canada has hikes only along
the border), and offers them again on
every Find place; Enter in the hike search picks the top suggestion, as the town box does (amends ADR-0054); day
hikes are suggested before multi-day routes; the list opens with one sentence and folds the rest under "How to
read this list", and the loop search says what it found in one sentence by its button; a hike measured with its
way back says "Round trip: no: one way, measured with its way back"; the month's weather help names the month.
Both tools name countries in full ("United Kingdom", not "GB" or "CA").

**Found as data causes, not changed:** military bases and suburbs as Bases (Air Force Academy, Fort Carson,
Estes Park's list). They are ordinary populated places in GeoNames, and `places.sqlite` keeps no feature code
to leave them out by; doing so needs the export to keep the codes, a rebuild. The drive-time limit greyed on the
visitors' local copy was that copy's setting; its note now says "not available on this copy of the site".

**Considered Options**:

- *Keep the "figures for" naming and explain it better.* All three visitors misread it, and a row naming two
  towns cannot be read at a glance.
- *Raise the request limit.* The lockout came from the page's own retries, not from the visitors' pace.
