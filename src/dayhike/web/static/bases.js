"use strict";
// "Find a base" (ADR-0084): a town the Hiker stayed in and a Reach give a BaseRanking, the towns whose hikes
// within that drive are alike. A mode of its own over app.js's helpers (api, postRank, fmt, helpButton,
// foldName, addBaseLayer, readLink, shownAddress), which loads first; it starts once app.js has /api/info. The address
// names the town, the Reach and the Base opened, so a reload or a shared link opens them again (ADR-0073);
// each step the Hiker takes is a step of the browser's history, so Back undoes it.

const BASE_PIN_KM = 5; // a click on the map chooses the nearest Base at least this close to it,
const BASE_PIN_PX = 40; // or within this many pixels of it at the map's zoom,
const BASE_PIN_MAX_KM = 50; // but never farther than this
const BASE_REACH = 30; // the Reach the mode opens with, in minutes
const BASE_PAGE = 100; // rows shown at a time; "Show more" adds as many
const BASE_MAP_ROWS = 10; // the map opens on the Hiker's Base and this many best rows
const BASE_SETTLE_MS = 600; // a choice changed waits this long for the next before the list is asked for
const BASE_TYPING_MS = 900; // the same for a number being typed
const BASE_FEW_HIKES = 30; // under this many of the Hiker's hikes, the list says the scores rest on few
const BASE_CACHE = 30; // answers kept, so going back to a list or a town asks the server nothing
const MONTH_LETTERS = "JFMAMJJASOND";
// Each "like mine" TownTrait as a row says what it took off (ADR-0025).
const TRAIT_NAMES = { size: "town size", stays: "places to stay", eats: "restaurants, cafes and pubs", months: "best months" };
// The HikeFilter's ranges (link key stem, name, metric unit, what it measures) and yes-only toggles, as
// dayhike.hike_filter names them; a hike is measured one way, as mapped.
const HIKE_RANGES = [
  ["climb", "Climb", "m", "one way"],
  ["length", "Length", "km", "one way, as mapped"],
  ["highest", "Highest point", "m", ""],
];
const HIKE_TOGGLES = [
  ["summit", "Reaches a summit", ""],
  ["waterside", "By a lake or the sea", "much of the way beside water"],
  ["round_trip", "Round trip", "ends where it starts; a there-and-back is not one"],
];
// Each TownTrait's filter: "at least" and "up to" a number (``ends``), or the months a Base is mild in.
const TRAIT_FILTERS = {
  size: { ends: ["at_least", "up_to"], unit: "people" },
  stays: { ends: ["at_least"], unit: "places" },
  eats: { ends: ["at_least"], unit: "places" },
  months: {},
};
const TRAIT_DEFAULTS = { size: { up_to: 50_000 } }; // every other TownTrait is ignored
const IMPORTANCE = [[1, "a little"], [2, "matters"], [3, "important"]];
// "like mine" in an address: "size=like-mine-important"; "like1" to "like3" of older links are read too.
const LIKE_WORDS = { 1: "like-mine-a-little", 2: "like-mine", 3: "like-mine-important" };

const bases = {
  seed: null, // the Hiker's Base: {id, name, country, lat, lon, ...}
  reach: BASE_REACH,
  firstReach: BASE_REACH, // the Reach a link without one opens with
  opened: null, // the Base whose hikes are shown: {id, name, ...}, or only {id} until they are in
  rows: null, // the BaseRanking's rows once one is shown
  body: null, // the BaseRanking shown
  shownRows: BASE_PAGE, // how many of its rows are shown
  ranked: false, // a BaseRanking is asked for or shown, so a changed town or Reach asks again
  asked: 0, // the number of the latest BaseRanking asked for; an earlier answer is dropped
  pinned: 0, // the same for map clicks
  opening: 0, // the same for an opened Base's hikes
  settling: null, // the timer of a choice changed, before the list is asked for again
  answers: new Map(), // request text to answer, the last BASE_CACHE of them
  listScroll: 0, // where the page was scrolled when a Base was opened from the list
  returnTo: null, // the address of the Base whose hike was opened, to come back to
  map: null,
  layer: null,
  markers: [], // the Hiker's Base, then each row's, in the list's order
  hikeMarkers: [], // an opened Base's hikes, at their Trailheads

  start() {
    if (!state.info.bases) return; // the site has no Bases
    const reaches = state.info.bases.reaches;
    if (!reaches.includes(this.reach)) this.reach = this.firstReach = reaches[Math.floor(reaches.length / 2)];
    $("#base-reaches").innerHTML = reaches
      .map((minutes) => `<label class="check"><input type="radio" name="base-reach" value="${minutes}"${minutes === this.reach ? " checked" : ""}> about ${minutes} min</label>`)
      .join("");
    $("#base-reaches").addEventListener("change", (event) => {
      this.reach = Number(event.target.value);
      this.changed();
    });
    this.unchosen = $("#base-chosen").innerHTML;
    this.pinHint = $("#base-pin-hint").textContent;
    $("#bases-on").hidden = false;
    $("#bases-on").addEventListener("click", () => this.toggle(true));
    $("#bases-off").addEventListener("click", () => this.toggle(false));
    $("#base-find").addEventListener("click", () => this.find());
    $("#base-table").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-base]");
      if (button) this.open(this.rows.find((row) => row.id === Number(button.dataset.base)));
    });
    $("#base-more").addEventListener("click", () => this.more());
    for (const button of document.querySelectorAll(".base-hikes-close")) button.addEventListener("click", () => this.close());
    $("#base-hike-list").addEventListener("click", (event) => {
      if (event.target.closest("a[href^='#hike=']")) this.leaveFor(location.hash);
    });
    $("#bases-back").addEventListener("click", () => this.comeBack());
    document.addEventListener("hikesalike:units", () => this.relabel());
    this.search();
    this.choices();
    window.addEventListener("hashchange", () => this.follow(true));
    this.follow(false);
  },

  // On: the base panel and card take the place of everything that finds hikes. ``address``: the address
  // says so, as a step of its own (a link followed has already).
  toggle(on, address = true) {
    document.body.classList.toggle("finding-bases", on);
    $("#base-panel").hidden = $("#base-card").hidden = !on;
    if (on) $("#bases-back").hidden = true;
    if (address) history.pushState(null, "", on ? this.hash() : shownAddress() || location.pathname + location.search);
    if (!on) return;
    if (!this.map) {
      this.map = L.map($("#base-map"));
      this.layer = L.layerGroup().addTo(this.map);
      addBaseLayer(this.map);
      const [south, west, north, east] = state.info.core ?? state.info.coverage;
      this.map.fitBounds([[south, west], [north, east]]);
      this.map.on("click", (event) => this.pin(event.latlng));
    }
    this.map.invalidateSize();
    $("#base-search").focus();
  },

  // A hike of an opened Base was followed into the hike search: a button there comes back to it.
  leaveFor(address) {
    this.returnTo = address;
    $("#bases-back").hidden = false;
  },

  comeBack() {
    $("#bases-back").hidden = true;
    if (this.returnTo) location.hash = this.returnTo;
  },

  // ``base`` is the Hiker's: the list is asked for at once.
  choose(base, via = "search") {
    this.seed = base;
    this.say(base);
    $("#base-search").value = base.name;
    if (via !== "map") $("#base-pin-hint").textContent = this.pinHint;
    $("#base-find").disabled = false;
    this.opening++;
    this.opened = null;
    this.openedBody = null;
    this.hikeMarkers = [];
    $("#base-hikes").hidden = true;
    this.mark([]);
    this.rank({ push: true });
  },

  // Find bases: the town typed, when the box names another than the one chosen, then the list.
  async find() {
    const typed = $("#base-search").value.trim();
    if (typed && foldName(typed) !== foldName(this.seed?.name ?? "")) {
      const found = await this.lookUp(typed);
      if (found) this.choose(found);
      return;
    }
    clearTimeout(this.settling);
    this.settling = null;
    this.rank({ push: true, fresh: true });
    if (window.matchMedia("(max-width: 600px)").matches) $("#base-card").scrollIntoView({ block: "start" });
  },

  // The best town named ``typed``, or null, said where the list is, when there is none.
  async lookUp(typed) {
    try {
      const found = (await api(`/api/bases/search?q=${encodeURIComponent(typed)}&limit=1`)).results[0];
      if (!found) this.problem(`No town found named “${typed}”. Check the spelling, or click the map near it.`);
      return found ?? null;
    } catch (error) {
      this.problem(error.message);
      return null;
    }
  },

  // A choice changed: the address says it at once, as one step for every change made in a row, and the
  // list (or the Base opened) is asked for once the changes settle.
  changed(wait = BASE_SETTLE_MS) {
    this.showTraits();
    const shown = this.ranked || Boolean(this.opened);
    this.address(shown && !this.settling);
    if (!shown) return;
    $("#base-results").classList.add("stale");
    clearTimeout(this.settling);
    this.settling = setTimeout(() => {
      this.settling = null;
      if (this.ranked) this.rank();
      if (this.opened) this.open(this.opened, false);
    }, wait);
  },

  // The hike filter and TownTrait controls; a change asks again once a list or a Base is shown.
  choices() {
    const number = (id, label, hint = "any") => `<input type="number" id="${id}" name="${id}" min="0" step="any" inputmode="decimal" placeholder="${hint}" aria-label="${escapeHtml(label)}">`;
    $("#base-hike-filter").innerHTML = HIKE_RANGES.map(([stem, name, , what]) => `<div class="base-range" data-stem="${stem}">
        <span class="range-name">${name}${what ? ` <span class="what">(${what})</span>` : ""}</span>
        <span class="range-ends"><span class="end">from ${number(`base-${stem}-min`, `${name} from`)}</span>
        <span class="end">up to ${number(`base-${stem}-max`, `${name} up to`)} <span class="unit"></span></span></span>
        <span class="range-error error" hidden></span></div>`).join("")
      + HIKE_TOGGLES.map(([key, name, what]) => `<label class="check"><input type="checkbox" id="base-${key}" name="base-${key}"> <span>${name}${what ? ` <span class="what">(${what})</span>` : ""}</span></label>`).join("");
    $("#base-traits").innerHTML = Object.entries(TRAIT_FILTERS).map(([trait, filter]) => {
      const name = TRAIT_NAMES[trait], label = name[0].toUpperCase() + name.slice(1);
      const ends = trait === "months"
        ? `mild in ${MONTHS.map((month, i) => `<label class="check"><input type="checkbox" name="base-months" value="${i + 1}" aria-label="${month}"> ${month}</label>`).join("")}
           <span class="hint months-none">Tick the months it must be mild in.</span>`
        : filter.ends.map((end) => `${end === "up_to" ? "up to" : "at least"} ${number(`base-${trait}-${end}`, `${label} ${end === "up_to" ? "up to" : "at least"}`, trait === "size" ? "any" : "e.g. 5")}`).join(" ") + (filter.unit ? ` ${filter.unit}` : "");
      return `<div class="base-trait" data-trait="${trait}">
        <label><span>${label}</span> <select class="setting" name="base-${trait}" aria-label="${label}">
          <option value="ignore">Ignore</option><option value="filter">Filter</option><option value="like_mine">Like mine</option></select></label>
        <span class="trait-filter">${ends}</span>
        <select class="importance" name="base-${trait}-importance" aria-label="How much ${name} ${trait === "size" ? "matters" : "matter"}">${IMPORTANCE.map(([w, text]) => `<option value="${w}">${text}</option>`).join("")}</select>
      </div>`;
    }).join("");
    this.relabel();
    this.setChoices({}, {});
    for (const id of ["#base-hike-filter", "#base-traits"]) {
      $(id).addEventListener("change", () => this.changed());
      $(id).addEventListener("input", (event) => { if (event.target.type === "number") this.changed(BASE_TYPING_MS); });
    }
  },

  // The ranges' units as the page shows them (app.js's Units): each box keeps the units it shows its
  // number in, so a switch converts what is typed.
  relabel() {
    const filter = this.hikeFilter();
    for (const [stem, , unit] of HIKE_RANGES) {
      $(`.base-range[data-stem="${stem}"] .unit`).textContent = shownRangeUnit(unit);
    }
    this.setRanges(filter);
    if (this.openedBody) this.showBase(this.openedBody);
    else if (this.body && this.rows) { // the temperatures in the units shown
      $("#base-mine").innerHTML = mineLine(this.body.seed, this.body);
      this.table();
    }
  },

  // Each trait's filter or importance shown as its setting says.
  showTraits() {
    for (const row of document.querySelectorAll(".base-trait")) {
      const setting = row.querySelector(".setting").value;
      row.querySelector(".trait-filter").hidden = setting !== "filter";
      row.querySelector(".importance").hidden = setting !== "like_mine";
      const none = row.querySelector(".months-none");
      if (none) none.hidden = Boolean(row.querySelector(".trait-filter input:checked"));
    }
  },

  // The HikeFilter as the API takes it (HikeFilter.from_mapping): only the limits set, in metres and km.
  hikeFilter() {
    const filter = {};
    for (const [stem, , unit] of HIKE_RANGES) {
      for (const end of ["min", "max"]) {
        const input = $(`#base-${stem}-${end}`), value = input && typedNumber(input);
        if (value != null) filter[`${stem}_${end}_${unit}`] = toMetric(value, unit, input.dataset.units);
      }
    }
    for (const [key] of HIKE_TOGGLES) if ($(`#base-${key}`)?.checked) filter[key] = true;
    return filter;
  },

  // What is wrong with the choices typed, for each range: a number under 0, or a range the wrong way round.
  wrongChoices() {
    const wrong = [];
    for (const row of document.querySelectorAll(".base-range")) {
      const stem = row.dataset.stem, name = HIKE_RANGES.find(([s]) => s === stem)[1];
      const [low, high] = ["min", "max"].map((end) => $(`#base-${stem}-${end}`));
      let text = "";
      if ([low, high].some((input) => input.value.trim() !== "" && typedNumber(input) == null)) text = `${name}: type a number of 0 or more.`;
      else if (typedNumber(low) != null && typedNumber(high) != null && typedNumber(low) > typedNumber(high)) {
        text = `${name}: “from ${low.value}” is more than “up to ${high.value}”.`;
      }
      row.querySelector(".range-error").textContent = text;
      row.querySelector(".range-error").hidden = !text;
      if (text) wrong.push(text);
    }
    return wrong;
  },

  // Each TownTrait not ignored, as the API takes it (TraitChoice); a filter with nothing set is ignored.
  traits() {
    const traits = {};
    for (const row of document.querySelectorAll(".base-trait")) {
      const trait = row.dataset.trait, setting = row.querySelector(".setting").value;
      let choice = {};
      if (setting === "like_mine") choice = { like_mine: Number(row.querySelector(".importance").value) };
      else if (setting === "filter" && trait === "months") {
        const mild = [...row.querySelectorAll(".trait-filter input:checked")].map((box) => Number(box.value));
        if (mild.length) choice = { mild_in: mild };
      } else if (setting === "filter") {
        for (const end of TRAIT_FILTERS[trait].ends) {
          const value = typedNumber($(`#base-${trait}-${end}`));
          if (value != null) choice[end] = value;
        }
      }
      if (Object.keys(choice).length) traits[trait] = choice;
    }
    return traits;
  },

  // The range boxes to ``filter`` (metres and km), in the units shown.
  setRanges(filter) {
    for (const [stem, , unit] of HIKE_RANGES) {
      for (const end of ["min", "max"]) {
        const input = $(`#base-${stem}-${end}`), value = filter[`${stem}_${end}_${unit}`];
        input.value = value == null ? "" : String(fromMetric(value, unit));
        input.dataset.units = state.units;
      }
    }
  },

  // Every control to ``filter`` and ``traits`` (as hikeFilter and traits give them); a trait left out of
  // ``traits`` takes its default, one given as null is ignored. A section with a choice not at its
  // default opens, so a shared link shows what its list is for.
  setChoices(filter, traits) {
    this.setRanges(filter);
    for (const [key] of HIKE_TOGGLES) $(`#base-${key}`).checked = filter[key] === true;
    for (const row of document.querySelectorAll(".base-trait")) {
      const trait = row.dataset.trait, choice = trait in traits ? traits[trait] ?? {} : TRAIT_DEFAULTS[trait] ?? {};
      row.querySelector(".setting").value = choice.like_mine ? "like_mine" : Object.keys(choice).length ? "filter" : "ignore";
      row.querySelector(".importance").value = String(choice.like_mine || 2);
      for (const box of row.querySelectorAll(".trait-filter input[type=checkbox]")) box.checked = (choice.mild_in ?? []).includes(Number(box.value));
      for (const end of TRAIT_FILTERS[trait].ends ?? []) $(`#base-${trait}-${end}`).value = choice[end] ?? "";
    }
    this.showTraits();
    const [hikes, town] = document.querySelectorAll(".base-choices");
    if (Object.keys(filter).length) hikes.open = true;
    if (Object.keys(traits).some((trait) => JSON.stringify(traits[trait] ?? {}) !== JSON.stringify(TRAIT_DEFAULTS[trait] ?? {}))) town.open = true;
  },

  // The choices in words, for the list's note: "reaches a summit; climb up to 1,200 m; town size like yours (important)".
  choiceWords() {
    const words = [], filter = this.hikeFilter();
    for (const [stem, name, unit] of HIKE_RANGES) {
      const low = filter[`${stem}_min_${unit}`], high = filter[`${stem}_max_${unit}`];
      const shown = (value) => `${fromMetric(value, unit).toLocaleString("en")} ${shownRangeUnit(unit)}`;
      if (low != null || high != null) words.push(`${name.toLowerCase()} ${[low != null && `from ${shown(low)}`, high != null && `up to ${shown(high)}`].filter(Boolean).join(" ")}`);
    }
    for (const [key, name] of HIKE_TOGGLES) if (filter[key]) words.push(name.toLowerCase());
    for (const [trait, choice] of Object.entries(this.traits())) {
      if (JSON.stringify(choice) === JSON.stringify(TRAIT_DEFAULTS[trait] ?? {})) continue;
      const name = TRAIT_NAMES[trait];
      if (choice.like_mine) words.push(`${name} like yours (${IMPORTANCE.find(([w]) => w === choice.like_mine)[1]})`);
      else if (choice.mild_in) words.push(`mild in ${choice.mild_in.map((month) => MONTHS[month - 1]).join(", ")}`);
      else words.push(`${name} ${[choice.at_least != null && `at least ${choice.at_least.toLocaleString("en")}`, choice.up_to != null && `up to ${choice.up_to.toLocaleString("en")}`].filter(Boolean).join(" ")}`);
    }
    return words;
  },

  // The choices as an address's parts: the hike filter's limits ("&climb_max_m=500&summit") and each
  // TownTrait not at its default ("&size=ignore", "&eats=like-mine-important", "&stays=3-", "&size=-5000", "&months=7,8").
  choiceHash() {
    const parts = [];
    for (const [key, value] of Object.entries(this.hikeFilter())) parts.push(value === true ? key : `${key}=${value}`);
    const traits = this.traits();
    for (const trait of Object.keys(TRAIT_FILTERS)) {
      const choice = traits[trait] ?? {};
      if (JSON.stringify(choice) === JSON.stringify(TRAIT_DEFAULTS[trait] ?? {})) continue;
      if (choice.like_mine) parts.push(`${trait}=${LIKE_WORDS[choice.like_mine]}`);
      else if (choice.mild_in) parts.push(`${trait}=${choice.mild_in.join(",")}`);
      else if (Object.keys(choice).length) parts.push(`${trait}=${choice.at_least ?? ""}-${choice.up_to ?? ""}`);
      else parts.push(`${trait}=ignore`);
    }
    return parts.map((part) => `&${part}`).join("");
  },

  // The choices an address's ``parts`` name, every other one at its default (ADR-0073); a part that is no
  // valid setting is left out.
  followChoices(parts) {
    const filter = {}, traits = {};
    for (const [stem, , unit] of HIKE_RANGES) {
      for (const end of ["min", "max"]) {
        const key = `${stem}_${end}_${unit}`, text = decoded(parts.get(key) ?? "") ?? "";
        const value = text !== "" ? Number(text) : NaN;
        if (Number.isFinite(value) && value >= 0) filter[key] = value;
      }
    }
    for (const [key] of HIKE_TOGGLES) if (parts.has(key)) filter[key] = true;
    for (const trait of Object.keys(TRAIT_FILTERS)) {
      if (!parts.has(trait)) continue;
      const text = decoded(parts.get(trait)) ?? "";
      const like = Object.entries(LIKE_WORDS).find(([, words]) => words === text)?.[0];
      let choice = null, found;
      if (like) choice = { like_mine: Number(like) };
      else if (trait === "months") {
        const months = text.split(",").map(Number).filter((month) => Number.isInteger(month) && month >= 1 && month <= 12);
        if (months.length) choice = { mild_in: [...new Set(months)].sort((a, b) => a - b) };
      } else if ((found = /^(\d*\.?\d*)-(\d*\.?\d*)$/.exec(text))) {
        choice = {};
        if (found[1] && TRAIT_FILTERS[trait].ends.includes("at_least")) choice.at_least = Number(found[1]);
        if (found[2] && TRAIT_FILTERS[trait].ends.includes("up_to")) choice.up_to = Number(found[2]);
      }
      traits[trait] = choice;
    }
    this.setChoices(filter, traits);
  },

  say(base) {
    const near = base.near ? `, near ${escapeHtml(base.near.name)}` : "";
    $("#base-chosen").innerHTML = `Chosen: <strong>${escapeHtml(base.name)}</strong>${base.country ? `, ${escapeHtml(base.country)}` : ""}${near}`;
  },

  // "#base=1&reach=60&open=2": the mode, the Hiker's Base, the Reach and the Base opened, then the choices.
  hash() {
    return `#base${this.seed ? `=${this.seed.id}` : ""}&reach=${this.reach}${this.opened ? `&open=${this.opened.id}` : ""}${this.choiceHash()}`;
  },

  // The address keeps the mode's settings as they change; ``push``: as a step of the history of its own.
  address(push = false) {
    if ($("#base-panel").hidden) return;
    const hash = this.hash();
    if (hash === location.hash) return;
    if (push) history.pushState(null, "", hash);
    else history.replaceState(null, "", hash);
  },

  // An address naming the mode opens it with every setting at its default but those it names (ADR-0073);
  // ``changed`` (a hashchange: Back, Forward, or a link) to one that does not leaves the mode for app.js to
  // open it. A list or a Base already asked for is shown again from the answers kept.
  follow(changed) {
    const parts = readLink(location.hash);
    if (!parts.has("base")) {
      if (changed && !$("#base-panel").hidden) this.toggle(false, false);
      return;
    }
    const id = (decoded(parts.get("base")) ?? "").trim(), open = (decoded(parts.get("open") ?? "") ?? "").trim();
    const reach = Number(parts.get("reach")), wasOpen = Boolean(this.opened);
    this.asked++; // drops answers still on their way
    this.opening++;
    this.pinned++;
    clearTimeout(this.settling);
    this.settling = null;
    this.reach = state.info.bases.reaches.includes(reach) ? reach : this.firstReach;
    for (const radio of document.querySelectorAll('input[name="base-reach"]')) radio.checked = Number(radio.value) === this.reach;
    this.followChoices(parts);
    const seed = /^\d{1,17}$/.test(id) ? Number(id) : null;
    if (seed !== this.seed?.id) {
      this.seed = seed == null ? null : { id: seed };
      $("#base-chosen").innerHTML = this.unchosen;
      $("#base-search").value = "";
    }
    this.opened = this.seed && /^\d{1,17}$/.test(open) ? { id: Number(open) } : null;
    this.openedBody = null;
    this.hikeMarkers = [];
    this.ranked = false;
    this.rows = null;
    $("#base-find").disabled = !this.seed;
    $("#base-status").textContent = "";
    $("#base-pin-hint").textContent = this.pinHint;
    this.problem("");
    $("#base-results").hidden = $("#base-hikes").hidden = true;
    this.toggle(true, false);
    this.layer.clearLayers();
    if (this.seed) this.rank({ back: wasOpen && !this.opened });
    if (this.opened) this.open(this.opened, false);
  },

  // A click on the map: the nearest Base, when one is close enough at the map's zoom.
  async pin(at) {
    const hint = $("#base-pin-hint");
    hint.classList.remove("error");
    hint.textContent = "Looking for the nearest town…";
    const number = ++this.pinned;
    const perPixel = 40_075_016.686 * Math.cos((at.lat * Math.PI) / 180) / 2 ** (this.map.getZoom() + 8);
    const within = Math.min(BASE_PIN_MAX_KM, Math.max(BASE_PIN_KM, (BASE_PIN_PX * perPixel) / 1000));
    try {
      const found = await api(`/api/bases/nearest?lat=${at.lat.toFixed(5)}&lon=${at.lng.toFixed(5)}`);
      if (number !== this.pinned) return;
      if (found.distance_km > within) {
        hint.textContent = `No base within ${fmt.long(within * 1000)} of there: the nearest is ${found.name}, ${fmt.km(found.distance_km * 1000)} away. Zoom in, or click nearer a town.`;
        return;
      }
      hint.textContent = `${found.name} is the nearest base, ${fmt.km(found.distance_km * 1000)} from there.`;
      this.choose(found, "map");
    } catch (error) {
      if (number === this.pinned) sayFailed(hint, error, () => this.pin(at));
    }
  },

  // The town box: names starting with what is typed, as a listbox under it. Enter takes the one
  // highlighted, else the first; Enter before they are in looks the name up.
  search() {
    const input = $("#base-search"), list = $("#base-suggestions");
    let found = [], active = -1, timer = null, asking = null, shownFor = null;
    const close = () => {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      active = -1;
    };
    const say = (text) => {
      list.innerHTML = `<li class="nothing">${escapeHtml(text)}</li>`;
      list.hidden = false;
    };
    const show = () => {
      if (!found.length) {
        const still = this.rows && this.seed?.name ? ` The list is still for ${this.seed.name}.` : "";
        say(`No town found by that name. Check the spelling, or click the map near it.${still}`);
        return;
      }
      list.innerHTML = found.map((base, i) => `<li role="option" id="base-option-${i}" aria-selected="${i === active}" data-index="${i}">${escapeHtml(base.name)}<span class="meta">${escapeHtml(placeLine(base, true))}</span></li>`).join("");
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      if (active >= 0) input.setAttribute("aria-activedescendant", `base-option-${active}`);
    };
    const pick = (i) => {
      const base = found[i];
      close();
      this.choose(base);
    };
    input.addEventListener("input", () => {
      clearTimeout(timer);
      asking?.abort();
      found = [];
      shownFor = null;
      close();
      const q = input.value.trim();
      $("#base-find").disabled = !this.seed && !q; // Find bases looks a typed name up
      if (q.length < 2) return;
      timer = setTimeout(async () => {
        asking = new AbortController();
        try {
          found = (await api(`/api/bases/search?q=${encodeURIComponent(q)}&limit=10`, { signal: asking.signal })).results;
          shownFor = q;
          active = -1;
          show();
        } catch (error) {
          if (error.name !== "AbortError") say(error.message); // a refusal is not "no town of that name"
        }
      }, 200);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        if (found.length && shownFor === input.value.trim()) pick(Math.max(active, 0));
        else if (input.value.trim()) {
          clearTimeout(timer);
          asking?.abort();
          close();
          this.find();
        }
        return;
      }
      if (list.hidden || !found.length) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (event.key === "ArrowDown") active = (active + 1) % found.length;
        else active = active <= 0 ? found.length - 1 : active - 1;
        show();
      } else if (event.key === "Escape") {
        close();
      }
    });
    list.addEventListener("mousedown", (event) => event.preventDefault()); // the box keeps the focus
    list.addEventListener("click", (event) => {
      const option = event.target.closest("li[data-index]");
      if (option) pick(Number(option.dataset.index));
    });
    input.addEventListener("blur", close);
  },

  // A problem with the list asked for, said above it: the list shown is marked as not for the choices now.
  problem(text, again = null) {
    const host = $("#base-problem");
    host.hidden = !text;
    host.textContent = "";
    $("#base-results").classList.toggle("stale", Boolean(text) && Boolean(this.rows));
    if (!text) return;
    host.textContent = text + (this.rows ? " The list below is for your earlier choices." : "");
    if (!again) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary retry";
    button.textContent = "Try again";
    button.addEventListener("click", () => again());
    host.append(" ", button);
  },

  // The BaseRanking for the choices, from the answers kept unless ``fresh``; ``push``: the address as a
  // step of its own; ``back``: the list as it was left, scrolled where it was.
  async rank({ push = false, fresh = false, back = false } = {}) {
    if (!this.seed) return;
    const wrong = this.wrongChoices();
    if (wrong.length) {
      this.problem(wrong.join(" "));
      return;
    }
    this.address(push);
    const number = ++this.asked, current = () => number === this.asked;
    const status = $("#base-status");
    status.classList.remove("error");
    status.textContent = "Finding bases…";
    $("#base-find").disabled = true;
    this.ranked = true;
    const request = { seed: this.seed.id, reach: this.reach }, filter = this.hikeFilter(), traits = this.traits();
    if (Object.keys(filter).length) request.hike_filter = filter;
    if (Object.keys(traits).length) request.traits = traits;
    const key = JSON.stringify(request);
    try {
      const body = (!fresh && this.answers.get(key)) || await postRank(request, current, "/api/bases/rank", (text) => { status.textContent = text; });
      if (!current()) return;
      this.keep(this.answers, key, body);
      status.textContent = "";
      this.problem("");
      this.show(body, back);
    } catch (error) {
      if (!current()) return;
      status.textContent = "Not updated: see the list.";
      if (error.status === 404) {
        this.rows = null;
        $("#base-results").hidden = true;
        this.problem(error.message);
      } else this.problem(error.message, error.status === 422 ? null : () => this.rank({ fresh: true }));
    } finally {
      if (current()) $("#base-find").disabled = false;
    }
  },

  keep(answers, key, body) {
    answers.delete(key);
    answers.set(key, body);
    if (answers.size > BASE_CACHE) answers.delete(answers.keys().next().value);
  },

  show(body, back = false) {
    const seed = body.seed;
    if (seed.id === this.seed.id && this.seed.lat == null) { // from a link: the town is known now
      this.seed = seed;
      this.say(seed);
      if (!$("#base-search").value) $("#base-search").value = seed.name;
    }
    this.body = body;
    this.rows = body.too_few ? [] : body.results;
    if (!back) this.shownRows = BASE_PAGE;
    $("#base-results").hidden = Boolean(this.opened);
    $("#base-results").classList.remove("stale");
    $("#base-title").textContent = `Bases with hikes like ${seed.name}'s`;
    $("#base-mine").innerHTML = mineLine(seed, body);
    const choices = this.choiceWords();
    const choiceLine = choices.length ? ` Your choices: ${choices.join("; ")}.` : "";
    if (body.too_few) {
      $("#base-note").textContent = tooFewText(seed, body, Object.keys(this.hikeFilter()).length > 0) + choiceLine;
      $("#base-table").innerHTML = "";
      $("#base-more").hidden = true;
      if (!this.opened) this.mark([]);
      return;
    }
    const rows = body.results, notes = [];
    notes.push(`${rows.length ? "Best first" : "No town is alike"}: hikes within about ${body.reach} min by car, ${seed.hike_groups} of them counted near ${seed.name} (hikes of one kind that start close together count once).${choiceLine}`);
    if (seed.hike_groups < BASE_FEW_HIKES) notes.push(`Only ${seed.hike_groups} of your hikes count, so the scores rest on few hikes.`);
    if (rows.length && rows[0].score < state.info.bases.weak_score) notes.push(`Even the best town is not much alike (${rows[0].score.toFixed(2)}): try other choices.`);
    if (rows.length && rows.length < BASE_PAGE) notes.push(`${rows.length} area${rows.length === 1 ? "" : "s"}: with a longer drive more towns share their hikes and join one row, so the list can be short.`);
    notes.push(state.info.bases.score_help.overall);
    $("#base-note").textContent = notes.join(" ");
    this.table(back);
  },

  // The rows shown so far, a hundred at a time; the map marks them.
  table(back = false) {
    const seed = this.body.seed, rows = this.rows.slice(0, this.shownRows);
    $("#base-table").innerHTML = `<thead><tr><th>#</th><th>Town</th><th>Score</th><th>Mild months</th></tr></thead>
      <tbody>${rows.map((row, i) => baseRow(row, i + 1, seed, this.traits())).join("")}</tbody>`;
    const left = this.rows.length - rows.length;
    $("#base-more").hidden = left <= 0;
    $("#base-more").textContent = `Show ${Math.min(left, BASE_PAGE)} more`;
    if (!this.opened) this.mark(rows, !back);
    if (back) window.scrollTo(0, this.listScroll);
  },

  more() {
    this.shownRows += BASE_PAGE;
    this.table();
  },

  // A Base's hikes in Reach, nearest first, on the map at their Trailheads; ``base``: a row, or {id} from a
  // link; ``step``: the Hiker opened it, a step of the history, the page scrolled to its top.
  async open(base, step = true) {
    if (this.wrongChoices().length) {
      $("#base-hikes").classList.add("stale");
      return;
    }
    const number = ++this.opening, note = $("#base-hikes-note"), filter = this.hikeFilter();
    if (step && !this.opened) this.listScroll = window.scrollY;
    this.opened = base;
    if (step) this.address(true);
    $("#base-results").hidden = true;
    $("#base-hikes").hidden = false;
    if (step) {
      const card = $("#base-card");
      if (card.getBoundingClientRect().top < 0) card.scrollIntoView({ block: "start" });
    }
    $("#base-hikes-title").textContent = base.name ? `Hikes within about ${this.reach} min of ${base.name}` : "Hikes in reach";
    $("#base-hike-list").innerHTML = "";
    note.classList.remove("error");
    note.textContent = "Finding its hikes…";
    const request = Object.keys(filter).length ? { base: base.id, reach: this.reach, hike_filter: filter } : { base: base.id, reach: this.reach };
    const key = JSON.stringify(request);
    this.details ??= new Map();
    try {
      const body = this.details.get(key) ?? await api("/api/bases/detail", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      });
      if (number !== this.opening) return;
      this.keep(this.details, key, body);
      this.opened = body.base;
      this.showBase(body);
    } catch (error) {
      if (number === this.opening) sayFailed(note, error, () => this.open(base, false));
    }
  },

  showBase(body) {
    const base = body.base, hikes = body.hikes; // nearest first
    this.openedBody = body;
    $("#base-hikes").classList.remove("stale");
    $("#base-hikes-title").textContent = `Hikes within about ${body.reach} min of ${base.name}`;
    $("#base-hikes-note").textContent = `${hikes.length} hike${hikes.length === 1 ? "" : "s"}, nearest first; ${base.hike_groups} of them count for the score, as hikes of one kind that start close together count once. The minutes are an estimate of the drive to where each starts.`;
    $("#base-hike-list").innerHTML = hikes.map((hike) => `<li>
      <span class="hike-name">${escapeHtml(hike.name || "A hike without a name")}</span> <span class="minutes">about ${hike.minutes} min</span>
      <span class="hike-facts">${escapeHtml(hikeFacts(hike))}</span>
      <a href="#hike=${hike.id}">Open this hike to find ones like it</a></li>`).join("");
    this.layer.clearLayers();
    this.hikeMarkers = hikes.map((hike) => L.circleMarker(hike.trailhead, { radius: 5, color: "#fff", weight: 1, fillColor: "#1d4ed8", fillOpacity: 0.9, bubblingMouseEvents: false })
      .bindTooltip(`${escapeHtml(hike.name || "A hike")} · about ${hike.minutes} min`));
    const marked = [
      L.circleMarker([base.lat, base.lon], { radius: 8, color: "#fff", weight: 2, fillColor: "#2f7d4f", fillOpacity: 1, bubblingMouseEvents: false }).bindTooltip(escapeHtml(base.name)),
      ...this.hikeMarkers,
    ];
    const group = L.featureGroup(marked).addTo(this.layer);
    this.map.fitBounds(group.getBounds(), { padding: [24, 24], maxZoom: 12 });
  },

  // Back to the list from an opened Base, scrolled where it was.
  close() {
    this.opening++;
    this.opened = null;
    this.openedBody = null;
    this.hikeMarkers = [];
    this.address(true);
    $("#base-hikes").hidden = true;
    $("#base-results").hidden = !this.rows;
    if (this.seed?.lat != null) this.mark(this.rows?.slice(0, this.shownRows) ?? [], false);
    window.scrollTo(0, this.listScroll);
  },

  // Markers for the Hiker's Base and each row's, numbered as the list; a row's opens it. ``fit``: the map
  // shows the Hiker's Base and the best rows.
  mark(rows, fit = true) {
    this.layer.clearLayers();
    const seed = this.seed;
    if (seed?.lat == null) return;
    this.markers = [
      L.circleMarker([seed.lat, seed.lon], { radius: 9, color: "#fff", weight: 2, fillColor: "#c2410c", fillOpacity: 1, bubblingMouseEvents: false })
        .bindTooltip(`Your base: ${escapeHtml(seed.name)}`),
      ...rows.map((row, i) => L.marker([row.lat, row.lon], {
        icon: L.divIcon({ className: "base-marker", html: `<span>${i + 1}</span>`, iconSize: [22, 22] }),
        keyboard: false,
        zIndexOffset: (rows.length - i) * 1000, // the best on top
      }).bindTooltip(`${i + 1}. ${escapeHtml(row.name)} · ${row.score.toFixed(2)}`)
        .on("click", () => this.open(row))),
    ];
    L.featureGroup(this.markers).addTo(this.layer);
    if (!fit) return;
    if (rows.length) this.map.fitBounds(L.featureGroup(this.markers.slice(0, BASE_MAP_ROWS + 1)).getBounds(), { padding: [24, 24], maxZoom: 11 });
    else this.map.setView([seed.lat, seed.lon], Math.max(this.map.getZoom(), 9));
  },
};

// A number box's value, or null when it is empty or no number of 0 or more.
function typedNumber(input) {
  const value = input.value.trim() === "" ? NaN : Number(input.value);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

// A range's unit as the page shows it: "m" is ft and "km" mi in miles and feet.
function shownRangeUnit(unit) {
  return imperial() ? { m: "ft", km: "mi" }[unit] : unit;
}

// ``value`` in ``unit`` (m or km), as shown: whole feet, or miles to a tenth.
function fromMetric(value, unit) {
  if (!imperial()) return value;
  return unit === "m" ? Math.round(value / FOOT_M) : Math.round((value * 1000) / MILE_M * 10) / 10;
}

// ``value`` typed in ``units`` ("imperial" or "metric"), in ``unit`` (m or km), to a tenth.
function toMetric(value, unit, units) {
  if (units !== "imperial") return value;
  return Math.round((unit === "m" ? value * FOOT_M : (value * MILE_M) / 1000) * 10) / 10;
}

// "GB · 4,658 people": a Base's country and size as a list names them; ``near``: and the big town it is near.
function placeLine(base, near = false) {
  const people = base.population != null ? `${base.population.toLocaleString("en")} ${base.population === 1 ? "person" : "people"}` : "";
  return [base.country, people, near && base.near && `near ${base.near.name}, ${base.near.km.toLocaleString("en")} km`].filter(Boolean).join(" · ");
}

// "12 places to stay · 40 to eat": the counts a TownTrait looks at, when known.
function townCounts(base) {
  return [base.stays != null && `${base.stays.toLocaleString("en")} place${base.stays === 1 ? "" : "s"} to stay`,
    base.eats != null && `${base.eats.toLocaleString("en")} to eat`].filter(Boolean).join(" · ");
}

// The Hiker's own town over the list: its mild months and the values the TownTraits compare.
function mineLine(seed, body) {
  const [low, high] = state.info.bases.mild_c;
  const values = [placeLine(seed), townCounts(seed)].filter(Boolean).join(" · ");
  return `<span class="mine-name">Your town, ${escapeHtml(seed.name)}:</span> ${seed.months ? monthsStrip(seed.months) : ""}
    <span class="hint">${escapeHtml(values)}</span>
    <span class="hint months-help">A month is mild when the hikes in reach have a median temperature of ${escapeHtml(fmt.temp(low))} to ${escapeHtml(fmt.temp(high))}.
    “Best months: like mine” compares each month’s temperature, so a town with the same mild months can still lose a little.</span>`;
}

// Why no town is listed: how many of the Hiker's hikes count, how many are needed, and what to change.
function tooFewText(seed, body, filtered) {
  const longest = Math.max(...state.info.bases.reaches), need = state.info.bases.min_hike_groups;
  const ways = [filtered && "loosen your hike choices", body.reach < longest && "try a longer drive"].filter(Boolean);
  const count = seed.hike_groups === 0 ? "None" : `Only ${seed.hike_groups}`;
  return `${count} of your hikes within about ${body.reach} min of ${seed.name} count${filtered ? " under your hike choices" : ""}: `
    + `at least ${need} are needed to compare towns on them.${ways.length ? ` To get a list, ${ways.join(", or ")}.` : " There are too few hikes mapped near this town."}`;
}

// What a TownTrait's cut came from when the town has no data on it.
function unknownTrait(row, name) {
  if (name === "size") return row.population == null;
  if (name === "months") return !row.months || row.months.every((t) => t == null);
  return row[name] == null;
}

// One row of a BaseRanking, its score explained (ADR-0025): how alike the hikes are, then what fewer hikes
// and each TownTrait took off, so the parts multiply to the score.
function baseRow(row, number, seed, traits) {
  const pct = (x) => `${Math.round(x * 100)} %`;
  const alike = row.enough > 0 ? row.hike_score / row.enough : row.hike_score;
  const why = [`hikes alike ${alike.toFixed(2)}: mix ${row.mix.toFixed(2)}, has ${pct(row.coverage)} of your kinds, yours has ${pct(row.back)} of its`];
  if (row.enough < 0.995) why.push(`fewer hikes than ${seed.name} (${row.hike_groups} against ${seed.hike_groups}) −${pct(1 - row.enough)}`);
  for (const [name, cut] of Object.entries(row.cuts)) {
    if (Math.round(cut * 100) > 0) why.push(`${TRAIT_NAMES[name] ?? name} −${pct(cut)}${unknownTrait(row, name) ? " (no data)" : ""}`);
  }
  const area = row.area_bases > 1
    ? `<span class="hint area" title="Towns close together have much the same hikes, so they share a row; which towns join it depends on your choices.">and ${row.area_bases - 1} nearby town${row.area_bases > 2 ? "s" : ""} with much the same hikes</span>`
    : "";
  const counts = (traits.stays || traits.eats) && townCounts(row);
  return `<tr>
    <td><span class="number">${number}</span></td>
    <td class="name"><button type="button" class="base-name" data-base="${row.id}">${escapeHtml(row.name)}</button> <span class="hint place">${escapeHtml(placeLine(row))}</span> ${area}
      <span class="figure-line">${row.hike_groups} hikes counted${counts ? ` · ${escapeHtml(counts)}` : ""}</span></td>
    <td class="score">${row.score.toFixed(2)} ${helpButton("base")}<span class="why">${escapeHtml(why.join("; "))}</span></td>
    <td>${monthsStrip(row.months)}</td>
  </tr>`;
}

// "6.2 km · ↑ 540 m · top 1,100 m · summit · round trip": what a hike of an opened Base is like, one way.
function hikeFacts(hike) {
  return [hike.length_m != null && fmt.km(hike.length_m), hike.climb_m != null && `↑ ${fmt.m(hike.climb_m)}`,
    hike.highest_m != null && `top ${fmt.m(hike.highest_m)}`, hike.summit && "summit", hike.round_trip && "round trip"].filter(Boolean).join(" · ");
}

// Twelve months, the mild ones marked: the median temperature of the hikes in reach in the mild band.
function monthsStrip(months) {
  const [low, high] = state.info.bases.mild_c;
  const cells = months.map((t, i) => {
    const mild = t != null && t >= low && t <= high;
    const title = `${MONTHS[i]}: ${t == null ? "no data" : `${fmt.temp(t)}${mild ? ", mild" : ""}`}`;
    return `<li class="${t == null ? "unknown" : mild ? "mild" : ""}" title="${escapeHtml(title)}">${MONTH_LETTERS[i]}</li>`;
  });
  return `<ul class="months" aria-label="Months mild for hiking">${cells.join("")}</ul>`;
}

if (state.info) bases.start();
else document.addEventListener("hikesalike:info", () => bases.start(), { once: true });
