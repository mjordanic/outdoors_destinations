"use strict";
// "Find a base" (ADR-0084): a town the Hiker stayed in and a Reach give a BaseRanking, the towns whose hikes
// within that drive are alike. A mode of its own over app.js's helpers (api, postRank, fmt, helpButton,
// addBaseLayer), which loads first; it starts once app.js has /api/info.

const BASE_PIN_KM = 5; // a click on the map chooses the nearest Base this close to it
const BASE_REACH = 30; // the Reach the mode opens with, in minutes
const MONTH_LETTERS = "JFMAMJJASOND";
// Each "like mine" TownTrait as a row says what it took off (ADR-0025).
const TRAIT_NAMES = { size: "town size", stays: "places to stay", eats: "restaurants, cafes and pubs", months: "best months" };

const bases = {
  seed: null, // the Hiker's Base: {id, name, country, lat, lon, ...}
  reach: BASE_REACH,
  ranked: false, // a BaseRanking is shown, so a changed town or Reach asks again
  asked: 0, // the number of the latest BaseRanking asked for; an earlier answer is dropped
  map: null,
  layer: null,
  markers: [], // the Hiker's Base, then each row's, in the list's order

  start() {
    if (!state.info.bases) return; // the site has no Bases
    const reaches = state.info.bases.reaches;
    if (!reaches.includes(this.reach)) this.reach = reaches[Math.floor(reaches.length / 2)];
    $("#base-reaches").innerHTML = reaches
      .map((minutes) => `<label class="check"><input type="radio" name="base-reach" value="${minutes}"${minutes === this.reach ? " checked" : ""}> about ${minutes} min</label>`)
      .join("");
    $("#base-reaches").addEventListener("change", (event) => {
      this.reach = Number(event.target.value);
      if (this.ranked) this.rank();
    });
    $("#bases-on").hidden = false;
    $("#bases-on").addEventListener("click", () => this.toggle(true));
    $("#bases-off").addEventListener("click", () => this.toggle(false));
    $("#base-find").addEventListener("click", () => this.rank());
    this.search();
  },

  // On: the base panel and card take the place of everything that finds hikes.
  toggle(on) {
    document.body.classList.toggle("finding-bases", on);
    $("#base-panel").hidden = $("#base-card").hidden = !on;
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
    $("#base-chosen").innerHTML = `Chosen: <strong>${escapeHtml(base.name)}</strong>${base.country ? `, ${escapeHtml(base.country)}` : ""}`;
    $("#base-find").disabled = false;
    if (this.ranked) this.rank();
    else this.mark([]);
  },

  // A click on the map: the nearest Base, when one is within BASE_PIN_KM.
  async pin(at) {
    const hint = $("#base-pin-hint");
    hint.classList.remove("error");
    hint.textContent = "Looking for the nearest town…";
    try {
      const found = await api(`/api/bases/nearest?lat=${at.lat.toFixed(5)}&lon=${at.lng.toFixed(5)}`);
      if (found.distance_km > BASE_PIN_KM) {
        hint.textContent = `No base within ${BASE_PIN_KM} km of there: the nearest is ${found.name}, ${fmt.km(found.distance_km * 1000)} away.`;
        return;
      }
      hint.textContent = `${found.name} is the nearest base, ${fmt.km(found.distance_km * 1000)} from there.`;
      this.choose(found);
    } catch (error) {
      sayFailed(hint, error, () => this.pin(at));
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
    try {
      const body = await postRank({ seed: this.seed.id, reach: this.reach }, current, "/api/bases/rank", (text) => { status.textContent = text; });
      if (!current()) return;
      status.textContent = "";
      this.ranked = true;
      this.show(body);
    } catch (error) {
      if (current()) sayFailed(status, error, () => this.rank());
    } finally {
      if (current()) $("#base-find").disabled = false;
    }
  },

  show(body) {
    const seed = body.seed;
    $("#base-results").hidden = false;
    $("#base-title").textContent = `Bases with hikes like ${seed.name}'s`;
    if (body.too_few) {
      $("#base-note").textContent = `Too few of your hikes are within about ${body.reach} min of ${seed.name} to compare towns on them. Try a longer drive.`;
      $("#base-table").innerHTML = "";
      this.mark([]);
      return;
    }
    $("#base-note").textContent = `${body.results.length ? "Best first" : "No town is alike"}: hikes within about ${body.reach} min by car, ${seed.hike_groups} of them near ${seed.name}. ${state.info.bases.score_help.overall}`;
    $("#base-table").innerHTML = `<thead><tr><th>#</th><th>Town</th><th>Score</th><th>Mild months</th></tr></thead>
      <tbody>${body.results.map((row, i) => baseRow(row, i + 1, seed)).join("")}</tbody>`;
    this.mark(body.results);
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
    <td class="name"><span class="base-name">${escapeHtml(row.name)}</span> <span class="hint">${escapeHtml(placeLine(row))}</span> ${area}
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
