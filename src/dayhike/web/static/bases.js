"use strict";
// "Find a base" (ADR-0084): a town the Hiker stayed in and a Reach give a BaseRanking, the towns whose hikes
// within that drive are alike. A mode of its own over app.js's helpers (api, postRank, fmt, helpButton,
// addBaseLayer, readLink, shownAddress), which loads first; it starts once app.js has /api/info. The address
// names the town, the Reach and the Base opened, so a reload or a shared link opens them again (ADR-0073).

const BASE_PIN_KM = 5; // a click on the map chooses the nearest Base this close to it
const BASE_REACH = 30; // the Reach the mode opens with, in minutes
const MONTH_LETTERS = "JFMAMJJASOND";
// Each "like mine" TownTrait as a row says what it took off (ADR-0025).
const TRAIT_NAMES = { size: "town size", stays: "places to stay", eats: "restaurants, cafes and pubs", months: "best months" };
// The HikeFilter's ranges (link key stem, name, unit) and yes-only toggles, as dayhike.hike_filter names them.
const HIKE_RANGES = [["climb", "Climb", "m"], ["length", "Length", "km"], ["highest", "Highest point", "m"]];
const HIKE_TOGGLES = [["summit", "Reaches a summit"], ["waterside", "By a lake or the sea"], ["round_trip", "Round trip"]];
// Each TownTrait's filter: "at least" and "up to" a number (``ends``), or the months a Base is mild in.
const TRAIT_FILTERS = { size: { ends: ["at_least", "up_to"], unit: "people" }, stays: { ends: ["at_least"] }, eats: { ends: ["at_least"] }, months: {} };
const TRAIT_DEFAULTS = { size: { up_to: 50_000 } }; // every other TownTrait is ignored
const IMPORTANCE = [[1, "a little"], [2, "matters"], [3, "important"]];

const bases = {
  seed: null, // the Hiker's Base: {id, name, country, lat, lon, ...}
  reach: BASE_REACH,
  firstReach: BASE_REACH, // the Reach a link without one opens with
  opened: null, // the Base whose hikes are shown: {id, name, ...}, or only {id} until they are in
  rows: null, // the BaseRanking's rows once one is shown
  ranked: false, // a BaseRanking is asked for or shown, so a changed town or Reach asks again
  asked: 0, // the number of the latest BaseRanking asked for; an earlier answer is dropped
  pinned: 0, // the same for map clicks
  opening: 0, // the same for an opened Base's hikes
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
      this.address();
      if (this.ranked) this.rank();
      if (this.opened) this.open(this.opened);
    });
    this.unchosen = $("#base-chosen").innerHTML;
    $("#bases-on").hidden = false;
    $("#bases-on").addEventListener("click", () => this.toggle(true));
    $("#bases-off").addEventListener("click", () => this.toggle(false));
    $("#base-find").addEventListener("click", () => this.rank());
    $("#base-table").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-base]");
      if (button) this.open(this.rows.find((row) => row.id === Number(button.dataset.base)));
    });
    $("#base-hikes-close").addEventListener("click", () => this.close());
    this.search();
    this.choices();
    window.addEventListener("hashchange", () => this.follow(true));
    this.follow(false);
  },

  // On: the base panel and card take the place of everything that finds hikes. ``address``: the address
  // says so (a link followed has already).
  toggle(on, address = true) {
    document.body.classList.toggle("finding-bases", on);
    $("#base-panel").hidden = $("#base-card").hidden = !on;
    if (address) history.replaceState(null, "", on ? this.hash() : shownAddress() || location.pathname + location.search);
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

  choose(base) {
    this.seed = base;
    this.say(base);
    $("#base-find").disabled = false;
    if (this.opened) this.close();
    this.address();
    if (this.ranked) this.rank();
    else this.mark([]);
  },

  // The hike filter and TownTrait controls; a change asks again once a list or a Base is shown.
  choices() {
    const number = (id, label) => `<input type="number" id="${id}" min="0" step="any" inputmode="decimal" aria-label="${escapeHtml(label)}">`;
    $("#base-hike-filter").innerHTML = HIKE_RANGES.map(([stem, name, unit]) => `<div class="base-range"><span>${name}</span>
        from ${number(`base-${stem}-min`, `${name} from`)} up to ${number(`base-${stem}-max`, `${name} up to`)} ${unit}</div>`).join("")
      + HIKE_TOGGLES.map(([key, name]) => `<label class="check"><input type="checkbox" id="base-${key}"> ${name}</label>`).join("");
    $("#base-traits").innerHTML = Object.entries(TRAIT_FILTERS).map(([trait, filter]) => {
      const name = TRAIT_NAMES[trait], label = name[0].toUpperCase() + name.slice(1);
      const ends = trait === "months"
        ? `mild in ${MONTHS.map((month, i) => `<label class="check"><input type="checkbox" value="${i + 1}" aria-label="${month}"> ${month}</label>`).join("")}`
        : filter.ends.map((end) => `${end === "up_to" ? "up to" : "at least"} ${number(`base-${trait}-${end}`, `${label} ${end === "up_to" ? "up to" : "at least"}`)}`).join(" ") + (filter.unit ? ` ${filter.unit}` : "");
      return `<div class="base-trait" data-trait="${trait}">
        <label><span>${label}</span> <select class="setting" aria-label="${label}">
          <option value="ignore">Ignore</option><option value="filter">Filter</option><option value="like_mine">Like mine</option></select></label>
        <span class="trait-filter">${ends}</span>
        <select class="importance" aria-label="How much ${name} ${trait === "size" ? "matters" : "matter"}">${IMPORTANCE.map(([w, text]) => `<option value="${w}">${text}</option>`).join("")}</select>
      </div>`;
    }).join("");
    this.setChoices({}, {});
    $("#base-hike-filter").addEventListener("change", () => {
      this.address();
      if (this.ranked) this.rank();
      if (this.opened) this.open(this.opened); // its passing hikes
    });
    $("#base-traits").addEventListener("change", () => {
      this.showTraits();
      this.address();
      if (this.ranked) this.rank();
    });
  },

  // Each trait's filter or importance shown as its setting says.
  showTraits() {
    for (const row of document.querySelectorAll(".base-trait")) {
      const setting = row.querySelector(".setting").value;
      row.querySelector(".trait-filter").hidden = setting !== "filter";
      row.querySelector(".importance").hidden = setting !== "like_mine";
    }
  },

  // The HikeFilter as the API takes it (HikeFilter.from_mapping): only the limits set.
  hikeFilter() {
    const filter = {};
    for (const [stem, , unit] of HIKE_RANGES) {
      for (const end of ["min", "max"]) {
        const value = typedNumber($(`#base-${stem}-${end}`));
        if (value != null) filter[`${stem}_${end}_${unit}`] = value;
      }
    }
    for (const [key] of HIKE_TOGGLES) if ($(`#base-${key}`).checked) filter[key] = true;
    return filter;
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

  // Every control to ``filter`` and ``traits`` (as hikeFilter and traits give them); a trait left out of
  // ``traits`` takes its default, one given as null is ignored.
  setChoices(filter, traits) {
    for (const [stem, , unit] of HIKE_RANGES) {
      for (const end of ["min", "max"]) $(`#base-${stem}-${end}`).value = filter[`${stem}_${end}_${unit}`] ?? "";
    }
    for (const [key] of HIKE_TOGGLES) $(`#base-${key}`).checked = filter[key] === true;
    for (const row of document.querySelectorAll(".base-trait")) {
      const trait = row.dataset.trait, choice = trait in traits ? traits[trait] ?? {} : TRAIT_DEFAULTS[trait] ?? {};
      row.querySelector(".setting").value = choice.like_mine ? "like_mine" : Object.keys(choice).length ? "filter" : "ignore";
      row.querySelector(".importance").value = String(choice.like_mine || 2);
      for (const box of row.querySelectorAll(".trait-filter input[type=checkbox]")) box.checked = (choice.mild_in ?? []).includes(Number(box.value));
      for (const end of TRAIT_FILTERS[trait].ends ?? []) $(`#base-${trait}-${end}`).value = choice[end] ?? "";
    }
    this.showTraits();
  },

  // The choices as an address's parts: the hike filter's limits ("&climb_max_m=500&summit") and each
  // TownTrait not at its default ("&size=ignore", "&eats=like3", "&stays=3-", "&size=-5000", "&months=7,8").
  choiceHash() {
    const parts = [];
    for (const [key, value] of Object.entries(this.hikeFilter())) parts.push(value === true ? key : `${key}=${value}`);
    const traits = this.traits();
    for (const trait of Object.keys(TRAIT_FILTERS)) {
      const choice = traits[trait] ?? {};
      if (JSON.stringify(choice) === JSON.stringify(TRAIT_DEFAULTS[trait] ?? {})) continue;
      if (choice.like_mine) parts.push(`${trait}=like${choice.like_mine}`);
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
      let choice = null, found;
      if ((found = /^like([123])$/.exec(text))) choice = { like_mine: Number(found[1]) };
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
    $("#base-chosen").innerHTML = `Chosen: <strong>${escapeHtml(base.name)}</strong>${base.country ? `, ${escapeHtml(base.country)}` : ""}`;
  },

  // "#base=1&reach=60&open=2": the mode, the Hiker's Base, the Reach and the Base opened, then the choices.
  hash() {
    return `#base${this.seed ? `=${this.seed.id}` : ""}&reach=${this.reach}${this.opened ? `&open=${this.opened.id}` : ""}${this.choiceHash()}`;
  },

  // The address keeps the mode's settings as they change.
  address() {
    if (!$("#base-panel").hidden) history.replaceState(null, "", this.hash());
  },

  // An address naming the mode opens it with every setting at its default but those it names (ADR-0073);
  // ``changed`` (a hashchange) to one that does not leaves the mode for app.js to open it.
  follow(changed) {
    const parts = readLink(location.hash);
    if (!parts.has("base")) {
      if (changed && !$("#base-panel").hidden) this.toggle(false, false);
      return;
    }
    const id = (decoded(parts.get("base")) ?? "").trim(), open = (decoded(parts.get("open") ?? "") ?? "").trim();
    const reach = Number(parts.get("reach"));
    this.asked++; // drops answers still on their way
    this.opening++;
    this.pinned++;
    this.reach = state.info.bases.reaches.includes(reach) ? reach : this.firstReach;
    for (const radio of document.querySelectorAll('input[name="base-reach"]')) radio.checked = Number(radio.value) === this.reach;
    this.followChoices(parts);
    this.seed = /^\d{1,17}$/.test(id) ? { id: Number(id) } : null;
    this.opened = this.seed && /^\d{1,17}$/.test(open) ? { id: Number(open) } : null;
    this.ranked = false;
    this.rows = null;
    $("#base-chosen").innerHTML = this.unchosen;
    $("#base-search").value = "";
    $("#base-find").disabled = !this.seed;
    $("#base-status").textContent = $("#base-pin-hint").textContent = "";
    $("#base-results").hidden = $("#base-hikes").hidden = true;
    this.toggle(true, false);
    this.layer.clearLayers();
    if (this.seed) this.rank();
    if (this.opened) this.open(this.opened);
  },

  // A click on the map: the nearest Base, when one is within BASE_PIN_KM.
  async pin(at) {
    const hint = $("#base-pin-hint");
    hint.classList.remove("error");
    hint.textContent = "Looking for the nearest town…";
    const number = ++this.pinned;
    try {
      const found = await api(`/api/bases/nearest?lat=${at.lat.toFixed(5)}&lon=${at.lng.toFixed(5)}`);
      if (number !== this.pinned) return;
      if (found.distance_km > BASE_PIN_KM) {
        hint.textContent = `No base within ${BASE_PIN_KM} km of there: the nearest is ${found.name}, ${fmt.km(found.distance_km * 1000)} away.`;
        return;
      }
      hint.textContent = `${found.name} is the nearest base, ${fmt.km(found.distance_km * 1000)} from there.`;
      this.choose(found);
    } catch (error) {
      if (number === this.pinned) sayFailed(hint, error, () => this.pin(at));
    }
  },

  // The town box: names starting with what is typed, as a listbox under it.
  search() {
    const input = $("#base-search"), list = $("#base-suggestions");
    let found = [], active = -1, timer = null, asking = null;
    const close = () => {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      active = -1;
    };
    const show = () => {
      list.innerHTML = found.length
        ? found.map((base, i) => `<li role="option" id="base-option-${i}" aria-selected="${i === active}" data-index="${i}">${escapeHtml(base.name)}<span class="meta">${escapeHtml(placeLine(base))}</span></li>`).join("")
        : `<li class="nothing">No town of that name has hikes in reach.</li>`;
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      if (active >= 0) input.setAttribute("aria-activedescendant", `base-option-${active}`);
    };
    const pick = (i) => {
      input.value = found[i].name;
      close();
      this.choose(found[i]);
    };
    input.addEventListener("input", () => {
      clearTimeout(timer);
      asking?.abort();
      found = [];
      close();
      const q = input.value.trim();
      if (q.length < 2) return;
      timer = setTimeout(async () => {
        asking = new AbortController();
        try {
          found = (await api(`/api/bases/search?q=${encodeURIComponent(q)}&limit=10`, { signal: asking.signal })).results;
          active = -1;
          show();
        } catch (error) {
          if (error.name !== "AbortError") close();
        }
      }, 200);
    });
    input.addEventListener("keydown", (event) => {
      if (list.hidden || !found.length) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (event.key === "ArrowDown") active = (active + 1) % found.length;
        else active = active <= 0 ? found.length - 1 : active - 1;
        show();
      } else if (event.key === "Enter" && active >= 0) {
        event.preventDefault();
        pick(active);
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

  async rank() {
    if (!this.seed) return;
    const number = ++this.asked, current = () => number === this.asked;
    const status = $("#base-status");
    status.classList.remove("error");
    status.textContent = "Finding bases…";
    $("#base-find").disabled = true;
    this.ranked = true;
    try {
      const request = { seed: this.seed.id, reach: this.reach }, filter = this.hikeFilter(), traits = this.traits();
      if (Object.keys(filter).length) request.hike_filter = filter;
      if (Object.keys(traits).length) request.traits = traits;
      const body = await postRank(request, current, "/api/bases/rank", (text) => { status.textContent = text; });
      if (!current()) return;
      status.textContent = "";
      this.show(body);
    } catch (error) {
      if (current()) sayFailed(status, error, () => this.rank());
    } finally {
      if (current()) $("#base-find").disabled = false;
    }
  },

  show(body) {
    const seed = body.seed;
    if (seed.id === this.seed.id && this.seed.lat == null) { // from a link: the town is known now
      this.seed = seed;
      this.say(seed);
      if (!$("#base-search").value) $("#base-search").value = seed.name;
    }
    this.rows = body.too_few ? [] : body.results;
    $("#base-results").hidden = Boolean(this.opened);
    $("#base-title").textContent = `Bases with hikes like ${seed.name}'s`;
    if (body.too_few) {
      $("#base-note").textContent = `Too few of your hikes are within about ${body.reach} min of ${seed.name} to compare towns on them. Try a longer drive.`;
      $("#base-table").innerHTML = "";
      if (!this.opened) this.mark([]);
      return;
    }
    $("#base-note").textContent = `${body.results.length ? "Best first" : "No town is alike"}: hikes within about ${body.reach} min by car, ${seed.hike_groups} of them near ${seed.name}. ${state.info.bases.score_help.overall}`;
    $("#base-table").innerHTML = `<thead><tr><th>#</th><th>Town</th><th>Score</th><th>Mild months</th></tr></thead>
      <tbody>${body.results.map((row, i) => baseRow(row, i + 1, seed)).join("")}</tbody>`;
    if (!this.opened) this.mark(body.results);
  },

  // A Base's hikes in Reach, nearest first, on the map at their Trailheads; ``base``: a row, or {id} from a link.
  async open(base) {
    const number = ++this.opening, note = $("#base-hikes-note"), filter = this.hikeFilter();
    this.opened = base;
    this.address();
    $("#base-results").hidden = true;
    $("#base-hikes").hidden = false;
    $("#base-hikes-title").textContent = base.name ? `Hikes within about ${this.reach} min of ${base.name}` : "Hikes in reach";
    $("#base-hike-list").innerHTML = "";
    note.classList.remove("error");
    note.textContent = "Finding its hikes…";
    try {
      const body = await api("/api/bases/detail", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.keys(filter).length ? { base: base.id, reach: this.reach, hike_filter: filter } : { base: base.id, reach: this.reach }),
      });
      if (number !== this.opening) return;
      this.opened = body.base;
      this.showBase(body);
    } catch (error) {
      if (number === this.opening) sayFailed(note, error, () => this.open(base));
    }
  },

  showBase(body) {
    const base = body.base, hikes = body.hikes; // nearest first
    $("#base-hikes-title").textContent = `Hikes within about ${body.reach} min of ${base.name}`;
    $("#base-hikes-note").textContent = `${hikes.length} hike${hikes.length === 1 ? "" : "s"}, nearest first. The minutes are an estimate of the drive to where each starts.`;
    $("#base-hike-list").innerHTML = hikes.map((hike) => `<li>
      <span class="hike-name">${escapeHtml(hike.name || "A hike without a name")}</span> <span class="minutes">about ${hike.minutes} min</span>
      <a href="#hike=${hike.id}">Find hikes like this</a></li>`).join("");
    this.layer.clearLayers();
    this.hikeMarkers = hikes.map((hike) => L.circleMarker(hike.trailhead, { radius: 5, color: "#fff", weight: 1, fillColor: "#1d4ed8", fillOpacity: 0.9 })
      .bindTooltip(`${escapeHtml(hike.name || "A hike")} · about ${hike.minutes} min`));
    const marked = [
      L.circleMarker([base.lat, base.lon], { radius: 8, color: "#fff", weight: 2, fillColor: "#2f7d4f", fillOpacity: 1 }).bindTooltip(escapeHtml(base.name)),
      ...this.hikeMarkers,
    ];
    const group = L.featureGroup(marked).addTo(this.layer);
    this.map.fitBounds(group.getBounds(), { padding: [24, 24], maxZoom: 12 });
  },

  // Back to the list from an opened Base.
  close() {
    this.opening++;
    this.opened = null;
    this.hikeMarkers = [];
    this.address();
    $("#base-hikes").hidden = true;
    $("#base-results").hidden = !this.rows;
    if (this.seed?.lat != null) this.mark(this.rows ?? []);
  },

  // Markers for the Hiker's Base and each row's, numbered as the list.
  mark(rows) {
    this.layer.clearLayers();
    const seed = this.seed;
    this.markers = [
      L.circleMarker([seed.lat, seed.lon], { radius: 9, color: "#fff", weight: 2, fillColor: "#c2410c", fillOpacity: 1 })
        .bindTooltip(`Your base: ${escapeHtml(seed.name)}`),
      ...rows.map((row, i) => L.circleMarker([row.lat, row.lon], { radius: 7, color: "#fff", weight: 1.5, fillColor: "#2f7d4f", fillOpacity: 0.9 })
        .bindTooltip(`${i + 1}. ${escapeHtml(row.name)} · ${row.score.toFixed(2)}`)),
    ];
    const group = L.featureGroup(this.markers).addTo(this.layer);
    if (rows.length) this.map.fitBounds(group.getBounds(), { padding: [24, 24], maxZoom: 11 });
    else this.map.setView([seed.lat, seed.lon], Math.max(this.map.getZoom(), 9));
  },
};

// A number box's value, or null when it is empty or no number of 0 or more.
function typedNumber(input) {
  const value = input.value.trim() === "" ? NaN : Number(input.value);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

// "GB · 4,658 people": a Base's country and size as a list names them.
function placeLine(base) {
  return [base.country, base.population != null ? `${base.population.toLocaleString("en")} people` : ""].filter(Boolean).join(" · ");
}

// One row of a BaseRanking, its score explained (ADR-0025): the hike score, and what each TownTrait took off.
function baseRow(row, number, seed) {
  const why = [`hikes ${row.hike_score.toFixed(2)}`];
  if (row.enough < 0.995) why[0] += ` (fewer hikes in reach than ${seed.name}: −${Math.round((1 - row.enough) * 100)} %)`;
  for (const [name, cut] of Object.entries(row.cuts)) {
    if (Math.round(cut * 100) > 0) why.push(`${TRAIT_NAMES[name] ?? name} −${Math.round(cut * 100)} %`);
  }
  const area = row.area_bases > 1 ? `<span class="hint">and ${row.area_bases - 1} town${row.area_bases > 2 ? "s" : ""} near it</span>` : "";
  return `<tr>
    <td><span class="number">${number}</span></td>
    <td class="name"><button type="button" class="base-name" data-base="${row.id}">${escapeHtml(row.name)}</button> <span class="hint">${escapeHtml(placeLine(row))}</span> ${area}
      <span class="figure-line">${row.hike_groups} hikes in reach</span></td>
    <td class="score">${row.score.toFixed(2)} ${helpButton("base")}<span class="why">${escapeHtml(why.join("; "))}</span></td>
    <td>${monthsStrip(row.months)}</td>
  </tr>`;
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
