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

  say(base) {
    $("#base-chosen").innerHTML = `Chosen: <strong>${escapeHtml(base.name)}</strong>${base.country ? `, ${escapeHtml(base.country)}` : ""}`;
  },

  // "#base=1&reach=60&open=2": the mode, the Hiker's Base, the Reach and the Base opened.
  hash() {
    return `#base${this.seed ? `=${this.seed.id}` : ""}&reach=${this.reach}${this.opened ? `&open=${this.opened.id}` : ""}`;
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
      const body = await postRank({ seed: this.seed.id, reach: this.reach }, current, "/api/bases/rank", (text) => { status.textContent = text; });
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
    const number = ++this.opening, note = $("#base-hikes-note");
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
        body: JSON.stringify({ base: base.id, reach: this.reach }),
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
