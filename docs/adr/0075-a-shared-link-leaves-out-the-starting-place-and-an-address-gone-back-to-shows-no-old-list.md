---
status: accepted
---

# A shared link leaves out the starting place, and an address gone back to shows no list of other settings

Round 3 of the visitor test found two places where the page said more, or other, than the visitor meant.

**"Link to this hike" and Share leave out the starting place.** ADR-0055 made them the same address as the
address bar, every setting included. That sent the visitor's starting place, its coordinates and the drive
or distance limit to whoever got the link, with no word of it. A friend starts from somewhere else, and a
place can be the visitor's exact coordinates. The links now carry every other setting (month, weights,
regions, units, Strict order and the rest) but not `from`, `place`, `hours` or `km`; the address bar keeps
them, so a reload still opens the visitor's own search. The GPX file's link already named the hike alone. A plain click on "Link to this hike" copies it as Share does instead of opening it in the same tab, where
it would drop the visitor's starting place and list; a click to open it in a new tab still opens it.

**An address gone back or forward to, or typed, opens as it does in a new tab (ADR-0073), with no list.**
ADR-0055 kept the list shown and marked it "The query changed since this ranking" when the address named other
settings. After Back, a visitor read that list ("within 2 h of Denver") as the answer to the settings now on
the left (no starting place), the note notwithstanding. Now such a list goes, as it would on a reload of that
address; an address whose settings match the list shown keeps it.

**Considered Options**:

- *Keep the starting place in the link and say so beside it.* The visitor would have to read the note before
  every share, and the place would still travel by default.
- *Run the search again after Back.* A drive-limited search can take 20 s, unasked for.
