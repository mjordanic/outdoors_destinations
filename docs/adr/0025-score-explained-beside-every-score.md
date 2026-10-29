---
status: accepted (amends ADR-0021, ADR-0022)
---

# Every score explained beside it, from one place

A Hiker saw "0.1" beside a Measure for two hikes that looked alike and could not tell what it meant. The compare note of ADR-0021 and ADR-0022 says one pair's score in words, but nothing on the page said what the number is, why equal values score 1.0 (ADR-0023), why alike hikes can still score low on one Measure, what data a Measure reads, or how the overall score is made. The paragraph under the results table said some of it, written into the page by hand, and it did not say that equal values now score 1.0 too.

**A short line and a "?" beside every score.** The overall score in a compare view gets a one-sentence line under it and a "?". The compare table keeps each Measure's note as its short line and adds a "?" beside each score, plus one in its Score heading. The results table gets a "?" in its Score heading and in every Measure column's heading, and the paragraph under it carries the short lines; a "?" in every cell of a 27-column table would bury the numbers. The "?" opens one explanation box: on hover, on keyboard focus, and on click or tap, which keeps it open until it is closed, Escape is pressed, focus leaves it, or the Hiker clicks elsewhere. The box is a non-modal dialog. The "?" is a button with an `aria-label`, `aria-expanded` and `aria-controls`. A click moves focus into the box, and Escape or its × returns focus to the "?".

**What the box says.** A Measure's "?" opens with that Measure's rule, its data source, and the short line. Every box then has the same six sections: what a Measure score means (the share of 100,000 random hike pairs that differ more, so 0.1 is "differ more than about 90% of random hike pairs"), that the same values score 1.0 (as does a pair closer than every random pair), why alike hikes can score low on one Measure, that rare shared traits tell more than common ones, where the facts come from (OpenStreetMap, FABDEM, ESA WorldCover, CHELSA), and how the scores are made (measure, compare, rank against random pairs, weighted mean). The rare-traits section says what the score does today: two equal values score 1.0 whether the value is rare or common, and more credit for rare shared traits is being studied.

**One place.** `SCORE_HELP` (the short lines and the sections) sits in `dayhike.serving` next to `score_note`, and each Measure's data source is the fourth field of `MEASURE_INFO`, next to its rule. `/api/measures` serves both, and the page builds every box from them. A change to the scoring rule changes these words and nothing in the page. The number of random pairs comes from `CALIBRATION_PAIRS`.

**Considered Options**: a native `title` tooltip (no touch, no keyboard, no structure); a separate help page (away from the number it explains); the text written into `app.js` (a second place to keep in step with the Measures); a "?" in every results cell (too dense). Rejected.
