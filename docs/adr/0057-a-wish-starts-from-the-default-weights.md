---
status: accepted (amends ADR-0031)
---

# A Wish starts from the default weights

The Wish form ("No particular hike in mind? Describe the one you want") opened with every weight at 0: all
Measures off and struck through, every value box empty. The liked-hike form opens with the basic set on at
100 (ADR-0031). The captain found the two forms incoherent. Now the Wish form opens with the same default
weights as the liked-hike form: the basic set on at 100, the advanced Measures at 0, and only what shows
counts, as before. Each Measure that is on asks the default value its value box already showed as a grey
hint (`WISH_INPUTS`: 10 km, 600 m of climbing, 1,500 m highest point, 18 °C), so no box starts on and
empty. "Reset weights" brings the same defaults back.

Lake or sea is the exception: it starts off in the Wish form (weight 0, struck through) and stays at 100 in
the liked-hike form. It has no neutral wanted value. A Wish scores the gap between the share it asks and
the hike's share, so 0 % ranks lakeside hikes down and 50 % ranks them up. The Hiker who cares turns Lake
or sea on and sets the share they want.

A wish address that names Measures opens exactly as before: each Measure it names with its value and
weight, every other wishable Measure at 0. Only an address naming none (`#wish=`, which could not be
searched) now opens the defaults.

**Considered Options**: turn the basic set on but leave its value boxes empty, for the Hiker to fill (Find
would refuse every empty box until the Hiker typed or switched off each one); keep the all-off start
(the reported incoherence). Rejected. The API is unchanged: a Wish without `weights` still weighs every
Measure with a value 100 (ADR-0024).
