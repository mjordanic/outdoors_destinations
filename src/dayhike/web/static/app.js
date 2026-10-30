"use strict";
// GoFindMeAHike: plain JS over the JSON API in dayhike.web. Charts are hand-drawn SVG.

const $ = (selector) => document.querySelector(selector);
const SVG_NS = "http://www.w3.org/2000/svg";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WATER = "#2b7bb9";
const KINDS = {
  drinking_water: ["Drinking water", WATER],
  spring: ["Spring", WATER],
  water_tap: ["Water tap", WATER],
  water_point: ["Water point", WATER],
  hut: ["Hut", "#c0392b"],
  restaurant_cafe: ["Restaurant / cafe", "#e67e22"],
  shelter: ["Shelter", "#8e5b3a"],
  viewpoint: ["Viewpoint", "#8e44ad"],
  peak: ["Peak", "#3b3b3b"],
  parking: ["Parking", "#6b7280"],
};
const LEGEND = [
  ["Water", WATER], ["Hut", "#c0392b"], ["Restaurant / cafe", "#e67e22"], ["Shelter", "#8e5b3a"],
  ["Viewpoint", "#8e44ad"], ["Peak", "#3b3b3b"], ["Parking", "#6b7280"], ["Trailhead", "#2f7d4f"],
];
const SAC = ["", "T1 hiking", "T2 mountain hiking", "T3 demanding mountain", "T4 alpine hiking",
  "T5 demanding alpine", "T6 difficult alpine"];
const SAC_RANK = { hiking: 1, mountain_hiking: 2, demanding_mountain_hiking: 3, alpine_hiking: 4,
  demanding_alpine_hiking: 5, difficult_alpine_hiking: 6 };
const LANDCOVER = [["tree_share", "Trees", "#2f6b3a"], ["shrub_share", "Shrub", "#8aa857"],
  ["grass_share", "Grass", "#c5d86d"], ["bare_share", "Bare", "#b7a68a"],
  ["snow_share", "Snow / ice", "#bcd7ea"], ["other_share", "Other", "#a7a9ac"]];
const SURFACE = [["paved_share", "Paved", "#6b7280"], ["gravel_share", "Gravel", "#c8a96a"],
  ["natural_share", "Natural", "#7f9d4f"], ["rock_share", "Rock", "#8d8d8d"]];

const PICK_ZOOM = 12; // the map shows hikes from this zoom on
const PICK_LINE = { color: "#6b2fa3", weight: 3, opacity: 0.85 };
const PICK_HOVER = { color: "#c2410c", weight: 5, opacity: 1 };
const COMPOSE_ZOOM = 13; // designing a hike shows the trail segments from this zoom on
const MAX_COMPOSED_M = 100000; // the longest hike the server measures (trailgraph.MAX_WALK_M)
const SHORTEST_HIKE_M = 1000; // the shortest catalog hike
const NAME_LENGTH = 120; // the longest name of a composed hike (web.NAME_LENGTH)
const SEGMENT_LINE = { color: "#2563eb", weight: 3, opacity: 0.75 };
const SEGMENT_GAP = { ...SEGMENT_LINE, dashArray: "4 6" };
const SEGMENT_HOVER = { color: "#c2410c", weight: 5, opacity: 1, dashArray: null };
const ROUTE_LINE = { color: "#c2410c", weight: 6, opacity: 0.85 };
const AUTO_LINE = { color: "#d97706", weight: 6, opacity: 0.9, dashArray: "10 6" }; // added to reach a click
const LINK_LINE = { color: "#8e44ad", weight: 4, opacity: 0.9, dashArray: "2 7" };
const LOOP_LINE = { color: "#15803d", weight: 4, opacity: 0.9, dashArray: "8 6" }; // a suggested loop
const VERSION_LINE = { color: "#0e7490", weight: 4, opacity: 0.9, dashArray: "3 6" }; // another version of a row
const RESULT_LINE = { color: "#1d4ed8", weight: 4, opacity: 0.9 }; // a catalog hike leading the row shown

const state = {
  info: null,
  groups: [],
  measures: [],
  scoreHelp: null, // what a score means, from the server (ADR-0025)
  liked: null,
  wishing: false, // the Hiker describes the hike they want instead of naming a liked one
  kept: {}, // the weights and off ticks of the mode not shown: { seed, wish }
  query: null, // the weights, month and return to start of the last Ranking
  asked: null, // what the shown Ranking asked the server, and of which liked hike or wish: { query, basis }
  results: [],
  expanded: null, // the result row whose versions (var 1, 2, 3) are shown
  selected: null, // the hike shown against the liked one: { key } of a result, or { pair } a DayHike id
  origin: null,
  geocoded: new Map(),
};

// ---------- helpers ----------

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function svg(tag, attrs = {}, parent = null) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (parent) parent.appendChild(node);
  return node;
}

function setStatus(text, isError = false) {
  const status = $("#status");
  status.textContent = text;
  status.classList.toggle("error", isError);
}

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 429) throw new Error("Too many requests; wait a minute and try again.");
    // A validation message without the "Value error, " pydantic puts before it.
    const detail = Array.isArray(body.detail) ? body.detail.map((d) => d.msg.replace(/^Value error, /, "")).join("; ") : body.detail;
    throw Object.assign(new Error(detail || `Request failed (${response.status}).`), { status: response.status });
  }
  return body;
}

function decodePolyline(text) {
  const points = [];
  let index = 0, lat = 0, lon = 0;
  while (index < text.length) {
    for (const axis of [0, 1]) {
      let result = 0, shift = 0, byte;
      do {
        byte = text.charCodeAt(index++) - 63;
        result |= (byte & 0x1f) << shift;
        shift += 5;
      } while (byte >= 0x20);
      const delta = result & 1 ? ~(result >> 1) : result >> 1;
      if (axis === 0) lat += delta; else lon += delta;
    }
    points.push([lat / 1e5, lon / 1e5]);
  }
  return points;
}

function metresBetween(a, b) {
  const rad = Math.PI / 180;
  const dLat = (b[0] - a[0]) * rad, dLon = (b[1] - a[1]) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * rad) * Math.cos(b[0] * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(h));
}

function niceTicks(low, high, count) {
  const raw = (high - low) / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((f) => f * power).find((s) => s >= raw);
  const ticks = [];
  for (let t = Math.ceil(low / step) * step; t <= high + 1e-9; t += step) ticks.push(Math.round(t * 1e6) / 1e6 + 0);
  return ticks;
}

const fmt = {
  km: (m) => (m < 50 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(m < 10000 ? 1 : 0)} km`), // no "0.0 km"
  m: (m) => `${(Math.round(m) + 0).toLocaleString("en")} m`, // + 0: no "-0 m"
  pct: (x) => `${Math.round(x * 100)} %`,
  yes: (x) => (x ? "yes" : "no"),
};

// Climb and descent in the drawn direction; "flat" when neither reaches the 10 m a climb needs to count.
function upDown(climb, descent) {
  return climb === 0 && descent === 0 ? "flat (no 10 m climb or descent)" : `↑ ${fmt.m(climb)} ↓ ${fmt.m(descent)}`;
}

// Climb and descent in the drawn direction; the gain fact (the larger of the two) when they are missing.
function climbText(hike, gain) {
  if (hike.climb_m != null) return upDown(hike.climb_m, hike.descent_m);
  return gain != null ? `climb or descent ${fmt.m(gain)}` : null;
}

// A hike's length as drawn, and with its way back when it is measured with one:
// "3.3 km one way (6.6 km with the way back)".
function lengthText(hike) {
  const back = hike.way_back;
  if (!back || back.kind === "none") return fmt.km(hike.facts.length_m ?? hike.walk_m);
  return `${fmt.km(hike.walk_m)} one way (${fmt.km(hike.facts.length_m ?? hike.walk_m + back.m)} with the way back)`;
}

function factColumn(column, month) {
  return month ? `${column}_${String(month).padStart(2, "0")}` : column;
}

function shares(facts, parts) {
  if (facts[parts[0][0]] == null) return null;
  return parts.map(([column, label, color]) => ({ label, color, value: facts[column] ?? 0 }));
}

// The fact behind one Measure, as text (null: no data).
function factText(measure, facts, month) {
  const value = (column) => facts[factColumn(column, measure.monthly ? month : null)];
  const v = measure.columns.length ? value(measure.columns[0]) : null;
  switch (measure.name) {
    case "profile": return null; // drawn as a sparkline
    case "landcover":
    case "surface": {
      const parts = shares(facts, measure.name === "landcover" ? LANDCOVER : SURFACE);
      if (!parts) return null;
      return parts.filter((p) => p.value >= 0.05).sort((a, b) => b.value - a.value)
        .map((p) => `${p.label} ${fmt.pct(p.value)}`).join(", ") || "—";
    }
  }
  if (v == null) return null;
  switch (measure.name) {
    case "length": case "dry_stretch": return fmt.km(v);
    case "elevation_gain": return `${fmt.m(v)} (the larger of up and down)`;
    case "highest": case "lowest": case "tarmac_approach": return fmt.m(v);
    case "steepest_km": return `${fmt.m(v)} in 1 km`;
    case "difficulty": return SAC[Math.round(v)] || String(v);
    case "peaks_around": return `${v}`;
    case "water_count": return v >= 3 ? "3 or more" : `${v}`;
    case "temperature": return `${v.toFixed(1)} °C`;
    case "precipitation": return `${Math.round(v)} mm / month`;
    case "solar_radiation": return `${Math.round(v).toLocaleString("en")} kJ/m²/day`;
    case "southness": return `${v.toFixed(2)} (${v >= 0.6 ? "south-facing" : v <= 0.4 ? "north-facing" : "mixed"})`;
    case "climb_grade": return `${(v * 100).toFixed(1)} %`;
    case "waterside": return `${fmt.pct(v)} of the walk`;
    default: return fmt.yes(v);
  }
}

function nodata(text) {
  const p = document.createElement("p");
  p.className = "nodata";
  p.textContent = text;
  return p;
}

// What a request names as the liked hike: a DayHike id, or a composed hike's route.
function likedSeed(liked, key) {
  return liked.route ? { composed: { route: liked.route.text } } : { [key]: liked.id };
}

// A composed hike's shareable address: its route by OSM node and way ids, and the
// Hiker's name for it (a proposed name is proposed again).
function routeHash(hike) {
  const name = hike.name_origin === "hiker" ? `&name=${encodeURIComponent(hike.name)}` : "";
  return `#compose=${hike.route.text}${name}`;
}

// The address of the page showing ``hike`` as the liked one.
function likedHash(hike) {
  return `${hike.route ? routeHash(hike) : `#hike=${hike.id}`}${modeHash()}`;
}

// Whether every hike is measured with its way back to its start; an address ends in
// "&oneway" when every hike is compared one way.
function returnToStart() {
  return $("#return-on").checked;
}

function modeHash() {
  return returnToStart() ? "" : "&oneway";
}

const WAY_BACK = { trail: "along the trails", retrace: "the same way", step: "a short step", none: "" };

// How a hike measured with its way back gets back to its start.
function wayBackText(back) {
  if (back.kind === "none") return "ends at its start";
  return `+ ${fmt.km(back.m)} back, ${WAY_BACK[back.kind]}`;
}

// ---------- charts ----------

function elevationChart(hike, onHover, width) {
  const wrap = document.createElement("div");
  wrap.className = "chart elevation";
  const title = document.createElement("h3");
  title.textContent = "Elevation profile (metres above sea level)";
  wrap.appendChild(title);
  if (!hike.elevation) {
    wrap.appendChild(nodata("No elevation data for this hike."));
    return wrap;
  }
  const metres = hike.elevation.metres, step = hike.elevation.step_m;
  const total = step * (metres.length - 1);
  // Drawn at the width it is shown, so the labels keep their size.
  const W = Math.max(300, Math.round(width || 640)), H = 210, L = 46, R = 10, T = 12, B = 26;
  let low = Math.min(...metres), high = Math.max(...metres);
  const span = Math.max(high - low, 100);
  low -= span * 0.08; high += span * 0.12;
  const x = (d) => L + (d / total) * (W - L - R);
  const y = (m) => T + (1 - (m - low) / (high - low)) * (H - T - B);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Elevation from ${Math.min(...metres)} to ${Math.max(...metres)} m` });
  for (const t of niceTicks(low, high, 4)) {
    svg("line", { x1: L, x2: W - R, y1: y(t), y2: y(t), stroke: "#eef0ec" }, root);
    svg("text", { x: L - 6, y: y(t) + 4, "text-anchor": "end" }, root).textContent = t.toLocaleString("en");
  }
  for (const t of niceTicks(0, total / 1000, Math.max(3, Math.floor(W / 110)))) {
    svg("text", { x: x(t * 1000), y: H - 8, "text-anchor": "middle" }, root).textContent = `${t} km`;
  }
  const line = metres.map((m, i) => `${x(i * step).toFixed(1)},${y(m).toFixed(1)}`).join(" ");
  svg("polygon", { points: `${x(0)},${y(low)} ${line} ${x(total)},${y(low)}`, fill: "#dcebdf" }, root);
  svg("polyline", { points: line, fill: "none", stroke: "#2f7d4f", "stroke-width": 2 }, root);
  const at = (d) => {
    const i = Math.min(metres.length - 2, Math.max(0, Math.floor(d / step)));
    const f = Math.min(1, Math.max(0, d / step - i));
    return metres[i] * (1 - f) + metres[i + 1] * f;
  };
  for (const p of hike.points) {
    if (p.where !== "line" || p.along_m == null || (p.kind === "peak" && p.off_m > 50)) continue;
    const [label, color] = KINDS[p.kind] || [p.kind, "#333"];
    const dot = svg("circle", { cx: x(p.along_m), cy: y(at(p.along_m)), r: 4.5, fill: color, stroke: "#fff", "stroke-width": 1.5 }, root);
    svg("title", {}, dot).textContent = `${label}${p.name ? `: ${p.name}` : ""} at ${fmt.km(p.along_m)}, ${fmt.m(at(p.along_m))}`;
  }
  const cursor = svg("line", { y1: T, y2: H - B, stroke: "#c2410c", "stroke-width": 1, visibility: "hidden" }, root);
  const readout = document.createElement("span");
  readout.className = "readout";
  const overlay = svg("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "transparent" }, root);
  const move = (event) => {
    const box = root.getBoundingClientRect();
    const px = ((event.touches ? event.touches[0].clientX : event.clientX) - box.left) * (W / box.width);
    const d = Math.min(total, Math.max(0, ((px - L) / (W - L - R)) * total));
    cursor.setAttribute("x1", x(d)); cursor.setAttribute("x2", x(d));
    cursor.setAttribute("visibility", "visible");
    readout.textContent = `${(d / 1000).toFixed(1)} km · ${Math.round(at(d))} m`;
    onHover(d / total);
  };
  overlay.addEventListener("mousemove", move);
  overlay.addEventListener("touchmove", move, { passive: true });
  overlay.addEventListener("mouseleave", () => { cursor.setAttribute("visibility", "hidden"); readout.textContent = ""; onHover(null); });
  wrap.appendChild(root);
  wrap.appendChild(readout);
  return wrap;
}

function stackedBar(titleText, parts, emptyText) {
  const wrap = document.createElement("div");
  wrap.className = "chart";
  const title = document.createElement("h3");
  title.textContent = titleText;
  wrap.appendChild(title);
  if (!parts) {
    wrap.appendChild(nodata(emptyText));
    return wrap;
  }
  const W = 300, H = 22;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H + 44}`, role: "img", "aria-label": titleText });
  let left = 0;
  const sum = parts.reduce((s, p) => s + p.value, 0) || 1;
  parts.forEach((p) => {
    const width = (p.value / sum) * W;
    const rect = svg("rect", { x: left, y: 0, width: Math.max(width, 0), height: H, fill: p.color }, root);
    svg("title", {}, rect).textContent = `${p.label} ${fmt.pct(p.value)}`;
    left += width;
  });
  parts.forEach((p, i) => {
    const col = i % 3, row = Math.floor(i / 3);
    svg("rect", { x: col * 100, y: H + 8 + row * 16, width: 9, height: 9, fill: p.color }, root);
    svg("text", { x: col * 100 + 13, y: H + 16 + row * 16 }, root).textContent = `${p.label} ${fmt.pct(p.value)}`;
  });
  wrap.appendChild(root);
  return wrap;
}

function monthBars(titleText, values, { color, unit, month, decimals = 0 }) {
  const wrap = document.createElement("div");
  const title = document.createElement("h3");
  const max = Math.max(...values), min = Math.min(...values);
  title.textContent = `${titleText} (${min.toFixed(decimals)} to ${max.toFixed(decimals)} ${unit})`;
  wrap.appendChild(title);
  const W = 300, H = 74, T = 6, B = 16;
  const low = Math.min(0, ...values), high = Math.max(0, ...values) || 1;
  const y = (v) => T + (1 - (v - low) / (high - low)) * (H - T - B);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": titleText });
  const bw = W / 12;
  values.forEach((v, i) => {
    const rect = svg("rect", {
      x: i * bw + 2, width: bw - 4, y: Math.min(y(v), y(0)), height: Math.max(1, Math.abs(y(v) - y(0))),
      fill: color, opacity: month && month !== i + 1 ? 0.45 : 1,
    }, root);
    svg("title", {}, rect).textContent = `${MONTHS[i]}: ${v.toFixed(decimals)} ${unit}`;
    svg("text", { x: i * bw + bw / 2, y: H - 3, "text-anchor": "middle" }, root).textContent = MONTHS[i][0];
  });
  svg("line", { x1: 0, x2: W, y1: y(0), y2: y(0), stroke: "#9aa39c" }, root);
  wrap.appendChild(root);
  return wrap;
}

function climateChart(facts, month) {
  const wrap = document.createElement("div");
  wrap.className = "chart";
  const series = (name) => MONTHS.map((_, i) => facts[`${name}_${String(i + 1).padStart(2, "0")}`]);
  const temperature = series("temperature_c");
  if (temperature.some((v) => v == null)) {
    const title = document.createElement("h3");
    title.textContent = "Climate by month";
    wrap.append(title, nodata("No climate data for this area yet."));
    return wrap;
  }
  wrap.append(
    monthBars("Temperature by month", temperature, { color: "#d9534f", unit: "°C", month, decimals: 1 }),
    monthBars("Precipitation by month", series("precipitation_mm"), { color: WATER, unit: "mm", month }),
    monthBars("Solar radiation by month", series("solar_radiation"), { color: "#e9b949", unit: "kJ/m²/day", month }),
  );
  return wrap;
}

function sparkline(values) {
  if (!values || values.some((v) => v == null)) return null;
  const root = svg("svg", { viewBox: "0 0 80 20", width: 80, height: 20, role: "img", "aria-label": "Profile shape" });
  svg("polyline", {
    points: values.map((v, i) => `${(i / (values.length - 1)) * 80},${18 - v * 16}`).join(" "),
    fill: "none", stroke: "#2f7d4f", "stroke-width": 1.5,
  }, root);
  return root;
}

// Light to mid green, dark enough at most for the dark cell text to keep 4.5:1 contrast.
function scoreColor(score) {
  return `hsl(145, 42%, ${Math.round(90 - 46 * score)}%)`;
}

// ---------- score help (ADR-0025) ----------

// A small "?" beside a score: hover, focus, click or tap opens what the score means.
// ``key``: "overall", "measures", or a Measure's name.
function helpButton(key) {
  const measure = state.measures.find((m) => m.name === key);
  const label = measure ? `What the ${measure.label} score means` : `What the ${key === "overall" ? "similarity" : "measure"} score means`;
  return `<button type="button" class="help" data-help="${escapeHtml(key)}" aria-label="${escapeHtml(label)}" aria-haspopup="dialog" aria-expanded="false" aria-controls="score-help">?</button>`;
}

function helpHtml(key) {
  const help = state.scoreHelp;
  const measure = state.measures.find((m) => m.name === key);
  const head = measure
    ? `<h3 id="score-help-title">${escapeHtml(measure.label)}</h3><p>${escapeHtml(measure.description)}</p>
       <p><strong>Data:</strong> ${escapeHtml(measure.source)}</p><p>${escapeHtml(help.measure)}</p>`
    : `<h3 id="score-help-title">${key === "overall" ? "Similarity score" : "Measure scores"}</h3>
       <p>${escapeHtml(key === "overall" ? help.overall : help.measure)}</p>`;
  const sections = help.sections.map((s) => `<h4>${escapeHtml(s.title)}</h4><p>${escapeHtml(s.text)}</p>`).join("");
  return `<button type="button" class="help-close" aria-label="Close">×</button>${head}${sections}`;
}

// One explanation box for every "?": shown while a "?" is hovered or focused, and kept
// (pinned) after a click or tap until it is closed, Escape is pressed, or the focus leaves it.
const scoreHelp = {
  button: null,
  pinned: false,
  timer: null,
  pressed: null, // the "?" under a pressed pointer, whose click toggles the box
  returning: false, // focus going back to the "?" on close, which must not open it again
  open(button, pinned) {
    clearTimeout(this.timer);
    const box = $("#score-help");
    if (this.button !== button) {
      this.button?.setAttribute("aria-expanded", "false");
      box.innerHTML = helpHtml(button.dataset.help);
      box.scrollTop = 0;
      this.button = button;
      this.pinned = false;
      button.setAttribute("aria-expanded", "true");
    }
    this.pinned ||= pinned;
    box.hidden = false;
    this.place();
    if (pinned) box.focus();
  },
  close(returnFocus = false) {
    clearTimeout(this.timer);
    if (!this.button) return;
    const button = this.button;
    $("#score-help").hidden = true;
    button.setAttribute("aria-expanded", "false");
    this.button = null;
    this.pinned = false;
    if (returnFocus) {
      this.returning = true;
      button.focus(); // focusin runs before focus() returns
      this.returning = false;
    }
  },
  later() {
    clearTimeout(this.timer);
    if (!this.pinned) this.timer = setTimeout(() => this.close(), 200);
  },
  // Under the "?" (above it when there is no room below), inside the window.
  place() {
    const box = $("#score-help"), rect = this.button.getBoundingClientRect();
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - box.offsetWidth - 8));
    const below = rect.bottom + 6 + box.offsetHeight <= window.innerHeight || rect.top < box.offsetHeight + 6;
    box.style.left = `${left + window.scrollX}px`;
    box.style.top = `${(below ? rect.bottom + 6 : rect.top - box.offsetHeight - 6) + window.scrollY}px`;
  },
  // Whether ``node`` is the open "?" or inside the box.
  owns(node) {
    return node instanceof Node && (node === this.button || $("#score-help").contains(node));
  },
  listen() {
    document.addEventListener("click", (event) => {
      if (event.target.closest(".help-close")) { this.close(true); return; }
      const button = event.target.closest(".help");
      if (button) {
        if (button === this.button && this.pinned) this.close(); else this.open(button, true);
      } else if (this.pinned && !this.owns(event.target)) {
        this.close();
      }
    });
    document.addEventListener("mouseover", (event) => {
      const button = event.target.closest(".help");
      if (button && !this.pinned) this.open(button, false);
      else if (this.owns(event.target)) clearTimeout(this.timer);
    });
    document.addEventListener("mouseout", (event) => {
      if (this.owns(event.target) && !this.owns(event.relatedTarget)) this.later();
    });
    document.addEventListener("focusin", (event) => {
      const button = event.target.closest?.(".help");
      if (button && !this.pinned && !this.returning) this.open(button, false);
    });
    document.addEventListener("pointerdown", (event) => { this.pressed = event.target.closest(".help"); });
    document.addEventListener("pointerup", () => { this.pressed = null; });
    document.addEventListener("focusout", (event) => {
      if (this.owns(event.target) && !this.owns(event.relatedTarget) && this.pressed !== this.button) this.close();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && this.button) this.close(this.owns(document.activeElement));
    });
    window.addEventListener("resize", () => { if (this.button) this.place(); });
  },
};

// ---------- detail view (liked hike and clicked result share it) ----------

function pointText(p) {
  const [label] = KINDS[p.kind] || [p.kind];
  const parts = [`<strong>${escapeHtml(label)}</strong>`];
  if (p.name) parts.push(escapeHtml(p.name));
  if (p.ele != null) parts.push(`${Math.round(p.ele)} m`);
  if (p.where === "trailhead") parts.push(`${p.off_m} m from the trailhead`);
  else if (p.kind === "peak" && p.off_m > 50) parts.push(`${p.off_m} m off the route`);
  else parts.push(`at ${fmt.km(p.along_m)}`);
  return parts.join("<br>");
}

function drawMap(node, hike) {
  const map = L.map(node, { scrollWheelZoom: false });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  const latlngs = hike.line ? decodePolyline(hike.line) : [];
  if (latlngs.length) L.polyline(latlngs, { color: "#c2410c", weight: 4, opacity: 0.9 }).addTo(map);
  for (const p of hike.points) {
    const [, color] = KINDS[p.kind] || ["", "#333"];
    const around = p.kind === "peak" && p.off_m > 50;
    L.circleMarker([p.lat, p.lon], {
      radius: around ? 4 : 6, color: "#fff", weight: 1.5, fillColor: color, fillOpacity: around ? 0.6 : 0.95,
    }).bindTooltip(pointText(p)).addTo(map);
  }
  L.circleMarker(hike.trailhead, { radius: 8, color: "#fff", weight: 2, fillColor: "#2f7d4f", fillOpacity: 1 })
    .bindTooltip("<strong>Trailhead</strong>").addTo(map);
  if (latlngs.length > 1) map.fitBounds(L.latLngBounds(latlngs).pad(0.08));
  else map.setView(hike.trailhead, 14);
  const cumulative = [0];
  for (let i = 1; i < latlngs.length; i++) cumulative.push(cumulative[i - 1] + metresBetween(latlngs[i - 1], latlngs[i]));
  const cursor = L.circleMarker(hike.trailhead, { radius: 7, color: "#c2410c", weight: 3, fillOpacity: 0 });
  const hover = (fraction) => {
    if (fraction == null || latlngs.length < 2) { cursor.remove(); return; }
    const d = fraction * cumulative.at(-1);
    let i = cumulative.findIndex((c) => c >= d);
    i = Math.max(1, i < 0 ? latlngs.length - 1 : i);
    const f = (d - cumulative[i - 1]) / ((cumulative[i] - cumulative[i - 1]) || 1);
    cursor.setLatLng([
      latlngs[i - 1][0] + f * (latlngs[i][0] - latlngs[i - 1][0]),
      latlngs[i - 1][1] + f * (latlngs[i][1] - latlngs[i - 1][1]),
    ]).addTo(map);
  };
  return { map, hover };
}

// ``query``: the weights and month the compare scores were made with (a Ranking's, or a pair's).
function renderDetail(card, hike, { role, compare = null, result = null, query = state.query }) {
  if (card._map) card._map.remove();
  card.hidden = false;
  card.classList.remove("empty");
  const month = compare ? query?.month : Number($("#month").value) || null;
  const f = hike.facts;
  const badges = [
    f.length_m != null && lengthText(hike),
    climbText(hike, f.elevation_gain_m),
    f.highest_m != null && `top ${fmt.m(f.highest_m)}`,
    f.lowest_m != null && `low ${fmt.m(f.lowest_m)}`,
    f.difficulty_rank != null && SAC[Math.round(f.difficulty_rank)],
    hike.way_back ? wayBackText(hike.way_back)
      : f.round_trip != null && (f.round_trip ? "round trip" : "one way / lollipop"),
  ].filter(Boolean);
  const scoreLine = result
    ? `<p class="hint">Similarity score ${result.score == null ? "– (no weighted measure has data for both)" : result.score.toFixed(3)} ${helpButton("overall")}${result.drive_hours != null ? ` · about ${result.drive_hours.toFixed(1)} h drive` : ""}</p>
       <p class="hint">${escapeHtml(state.scoreHelp.overall)}</p>`
    : "";
  card.innerHTML = `
    <div class="detail-head">
      <div><div class="role">${escapeHtml(role)}</div><h2>${escapeHtml(hike.name)}</h2></div>
      ${hike.route
        ? `<span><a href="${routeHash(hike)}${modeHash()}" title="This address opens this hike as your hike">Link to this hike</a>${compare ? ' <button type="button" class="secondary edit-composed">Edit on the map</button>' : ""}</span>`
        : `<a href="https://www.openstreetmap.org/relation/${hike.id}" target="_blank" rel="noopener">OpenStreetMap relation ${hike.id}</a>`}
    </div>
    ${scoreLine}
    ${hike.route ? `<p class="hint">Drawn from marked trail segments${hike.routes?.length ? ` along ${escapeHtml(hike.routes.slice(0, 5).join(", "))}${hike.routes.length > 5 ? " and more" : ""}` : ""}; measured as drawn${hike.way_back ? ", with its way back" : ""}.</p>` : ""}
    ${hike.way_back && hike.way_back.kind !== "none" ? `<p class="hint">Length, climb, descent and every measure include the way back to the start (${escapeHtml(wayBackText(hike.way_back))}); the map and profile show the hike itself.</p>` : ""}
    <ul class="badges">${badges.map((b) => `<li>${escapeHtml(b)}</li>`).join("")}</ul>
    <div class="map"></div>
    <div class="legend">${LEGEND.map(([label, color]) => `<span style="--c:${color}">${label}</span>`).join("")}</div>
    <div class="slot-elevation"></div>
    <div class="charts"></div>
    <h3>${compare ? `Every measure: this hike against the ${compare.wish ? "one you want" : "liked one"}` : "The facts behind every measure"}</h3>
    <div class="table-wrap"><table class="facts"></table></div>`;
  card.querySelector(".edit-composed")?.addEventListener("click", () => editComposed(hike));
  const { map, hover } = drawMap(card.querySelector(".map"), hike);
  card._map = map;
  const slot = card.querySelector(".slot-elevation");
  slot.appendChild(elevationChart(hike, hover, slot.clientWidth));
  card.querySelector(".charts").append(
    stackedBar("Land cover along the route", shares(f, LANDCOVER), "No land-cover data for this area yet."),
    stackedBar("Trail surface", shares(f, SURFACE), "No surface tags on this route."),
    climateChart(f, month),
  );
  card.querySelector(".facts").replaceWith(factsTable(hike, compare, result, month, query?.weights, query?.return_to_start));
}

// The Measures a comparison shows: with the way back every hike is a round trip, so not Round trip.
function comparedMeasures(returnToStart) {
  return state.measures.filter((m) => !(returnToStart && m.name === "round_trip"));
}

function factsTable(hike, compare, result, month, weights, returnToStart) {
  const table = document.createElement("table");
  table.className = "facts";
  const head = compare
    ? `<tr><th>Measure</th><th>This hike</th><th>${compare.wish ? "You want" : "Liked hike"}</th><th>Score ${helpButton("measures")}</th></tr>`
    : "<tr><th>Measure</th><th>Value</th></tr>";
  table.innerHTML = `<thead>${head}</thead>`;
  for (const group of state.groups) {
    const body = document.createElement("tbody");
    body.innerHTML = `<tr><th scope="rowgroup" colspan="${compare ? 4 : 2}">${escapeHtml(group)}</th></tr>`;
    for (const measure of (compare ? comparedMeasures(returnToStart) : state.measures).filter((m) => m.group === group)) {
      const row = document.createElement("tr");
      if (compare && weights && !weights[measure.name]) row.className = "excluded";
      const cells = [`<td title="${escapeHtml(measure.description)}">${escapeHtml(measure.label)}</td>`];
      const values = compare ? [hike, compare] : [hike];
      for (const _ of values) cells.push('<td class="num"></td>');
      if (compare) cells.push('<td class="num score"></td>');
      row.innerHTML = cells.join("");
      values.forEach((h, i) => {
        const cell = row.children[i + 1];
        if (h.wish) {
          const wanted = h.wish[measure.name];
          cell.innerHTML = wanted == null ? '<span class="nodata">left out</span>' : escapeHtml(wishText(measure, wanted));
        } else if (measure.name === "profile") {
          const line = sparkline(h.profile_shape);
          if (line) cell.appendChild(line); else cell.innerHTML = '<span class="nodata">no data</span>';
        } else {
          const text = factText(measure, h.facts, month);
          cell.innerHTML = text == null ? '<span class="nodata">no data</span>' : escapeHtml(text);
        }
      });
      if (compare) {
        const { score, note } = result?.measures?.[measure.name] ?? {};
        row.lastElementChild.innerHTML = score == null
          ? (note ? `– ${helpButton(measure.name)}<small class="note">${escapeHtml(note)}</small>` : '<span class="nodata">no data</span>')
          : `<span class="bar"><i style="width:${Math.round(score * 100)}%"></i></span>${score.toFixed(2)} ${helpButton(measure.name)}${note ? `<small class="note">${escapeHtml(note)}</small>` : ""}`;
        if (weights && !weights[measure.name]) row.lastElementChild.title = "Left out of this score";
      }
      body.appendChild(row);
    }
    table.appendChild(body);
  }
  return table;
}

// ---------- weights ----------

function buildWeights() {
  const host = $("#weights");
  host.innerHTML = "";
  for (const group of state.groups) {
    const set = document.createElement("fieldset");
    set.className = "weights-group";
    set.innerHTML = `<legend>${escapeHtml(group)}</legend>`;
    const measures = state.measures.filter((m) => m.group === group);
    set.classList.toggle("unwishable", !measures.some(wishable));
    for (const measure of measures) {
      const row = document.createElement("div");
      row.className = wishable(measure) ? "weight" : "weight unwishable";
      row.dataset.measure = measure.name;
      const id = `w-${measure.name}`;
      row.innerHTML = `
        <label class="name" for="${id}" title="${escapeHtml(measure.description)}">${escapeHtml(measure.label)}</label>
        ${wishable(measure) ? wishInput(measure) : ""}
        <input id="${id}" type="number" min="0" max="100" step="1" value="${measure.default_weight}" inputmode="numeric" aria-label="${escapeHtml(measure.label)} weight, 0 to 100">
        <label class="off"><input type="checkbox" aria-label="Leave ${escapeHtml(measure.label)} out"> off</label>`;
      if (measure.name === "round_trip") {
        row.insertAdjacentHTML("beforeend", '<p class="hint weight-note">Left out with Return to start: every hike is then a round trip.</p>');
      }
      const [number, off] = row.querySelectorAll("input:not(.wish)");
      off.addEventListener("change", () => {
        number.disabled = off.checked || row.classList.contains("unused");
        row.classList.toggle("excluded", off.checked);
      });
      number.addEventListener("input", () => number.setCustomValidity(""));
      // Asking a value of a Measure left at 0 asks it in full.
      row.querySelector("input.wish")?.addEventListener("input", () => {
        if (!off.checked && Number(number.value) === 0) number.value = 100;
      });
      set.appendChild(row);
    }
    host.appendChild(set);
  }
  syncRoundTrip();
}

// Round trip tells no hike from another while every hike returns to its start: greyed out then.
function syncRoundTrip() {
  const row = document.querySelector('.weight[data-measure="round_trip"]');
  if (!row) return;
  const [number, off] = row.querySelectorAll("input:not(.wish)");
  row.classList.toggle("unused", returnToStart());
  number.disabled = returnToStart() || off.checked;
  off.disabled = returnToStart();
}

function readWeights() {
  const weights = {}, excluded = [];
  for (const row of document.querySelectorAll(".weight")) {
    const [number, off] = row.querySelectorAll("input:not(.wish)");
    if (off.checked || row.classList.contains("unused") || (state.wishing && row.classList.contains("unwishable"))) {
      excluded.push(row.dataset.measure);
      continue;
    }
    const value = Number(number.value);
    if (number.value === "" || !Number.isFinite(value) || value < 0 || value > 100) {
      number.setCustomValidity("0 to 100");
      number.reportValidity();
      throw new Error("Each weight is a number from 0 to 100.");
    }
    weights[row.dataset.measure] = value;
  }
  if (!Object.values(weights).some((w) => w > 0)) throw new Error("Give at least one measure a weight above 0.");
  return { weights, excluded };
}

// ---------- the hike you want (no Seed) ----------

// A Measure the Hiker can ask a value of: its "wish" input (ADR-0024); a server without wishes gives none.
const wishable = (measure) => measure.wish != null;
const yesNo = (measure) => measure.wish.kind === "yes/no";

// The value input of a weight row: a yes/no toggle, or a number in the Measure's unit and range.
function wishInput(measure) {
  const label = escapeHtml(measure.label), wish = measure.wish;
  if (yesNo(measure)) {
    return `<label class="wish-value"><input class="wish" type="checkbox"${wish.default ? " checked" : ""} aria-label="${label} you want"> yes</label>`;
  }
  const range = ["min", "max", "step"].filter((key) => wish[key] != null).map((key) => ` ${key}="${escapeHtml(wish[key])}"`).join("");
  const unit = wish.unit ? escapeHtml(wish.unit) : "";
  return `<span class="wish-value"><input class="wish" type="number"${range} value="${escapeHtml(wish.default)}" inputmode="decimal"
    aria-label="${label} you want${unit ? `, in ${unit}` : ""}">${unit ? `<span class="unit">${unit}</span>` : ""}</span>`;
}

// What the Hiker wants of each Measure they weight (a measure weighted 0 or left out asks nothing).
function readWish(weights) {
  const wish = {};
  for (const measure of state.measures.filter(wishable)) {
    if (!(weights[measure.name] > 0)) continue;
    const input = document.querySelector(`.weight[data-measure="${measure.name}"] input.wish`);
    if (yesNo(measure)) { wish[measure.name] = input.checked; continue; }
    const value = Number(input.value), { min, max, unit } = measure.wish;
    if (input.value === "" || !Number.isFinite(value) || value < (min ?? -Infinity) || value > (max ?? Infinity)) {
      const range = `${min} to ${max}${unit ? ` ${unit}` : ""}`;
      input.setCustomValidity(range);
      input.reportValidity();
      throw new Error(`${measure.label}: you want a value from ${range}.`);
    }
    wish[measure.name] = value;
  }
  return wish;
}

function wishText(measure, value) {
  if (typeof value === "boolean") return fmt.yes(value);
  return `${value.toLocaleString("en")}${measure.wish?.unit ? ` ${measure.wish.unit}` : ""}`;
}

// The wish as an address: "#wish=elevation_gain:800:100,hut_along:1:50", each Measure the Hiker
// weights with its value and weight, as typed.
function wishHash() {
  const parts = [];
  for (const row of document.querySelectorAll(".weight:not(.unwishable)")) {
    const [value, number, off] = row.querySelectorAll("input");
    if (off.checked || !(Number(number.value) > 0)) continue;
    parts.push(`${row.dataset.measure}:${value.type === "checkbox" ? Number(value.checked) : value.value}:${number.value}`);
  }
  return `#wish=${parts.join(",")}${modeHash()}`;
}

// Fills the form from a wish address: every wishable Measure it does not name weighs 0.
function applyWish(text) {
  const asked = new Map(text.split(",").filter(Boolean).map((part) => [part.split(":")[0], part.split(":")]));
  for (const row of document.querySelectorAll(".weight:not(.unwishable)")) {
    const [value, number, off] = row.querySelectorAll("input");
    const [, wanted, weight] = asked.get(row.dataset.measure) ?? [];
    off.checked = false;
    row.classList.remove("excluded");
    number.value = weight ?? 0;
    if (wanted == null) continue;
    if (value.type === "checkbox") value.checked = wanted === "1";
    else value.value = wanted;
  }
  syncRoundTrip();
}

// Each mode keeps its own weights: a wish starts asking nothing, and a liked hike's weights
// are as they were when the Hiker comes back.
function swapWeights(on) {
  const rows = [...document.querySelectorAll(".weight")].map((row) => row.querySelectorAll("input:not(.wish)"));
  state.kept[on ? "seed" : "wish"] = rows.map(([number, off]) => [number.value, off.checked]);
  const back = state.kept[on ? "wish" : "seed"] ?? rows.map(() => ["0", false]);
  rows.forEach(([number, off], i) => {
    [number.value, off.checked] = back[i];
    off.dispatchEvent(new Event("change"));
  });
  syncRoundTrip();
}

// The page asks for a hike the Hiker describes (on), or for hikes like a Seed (off).
function setWishing(on) {
  if (on !== state.wishing) swapWeights(on);
  state.wishing = on;
  document.body.classList.toggle("wishing", on);
  $("#seed-pick").hidden = on;
  $("#wish-intro").hidden = !on;
  $("#weights-title").textContent = on ? "2. What you want, and how much it matters" : "2. What “similar” means";
  $("#weights-hint").hidden = on;
  $("#wish-head").hidden = !on;
  $("#pair-panel").hidden = on;
  $("#liked").hidden = on;
  $("#wish-card").hidden = !on;
  $("#results").hidden = $("#selected").hidden = true;
  $("#find").textContent = on ? "Find hikes" : "Find similar";
  $("#find").disabled = !on && !state.liked;
  $("#return-bar").hidden = !on && !state.liked;
  if (on) {
    $("#weights-panel").open = true;
    $("#map-pick-on").checked = false;
    pick.close();
    setStatus("");
  }
  history.replaceState(null, "", on ? wishHash() : state.liked ? likedHash(state.liked) : location.pathname + location.search);
  const liked = state.liked;
  if (!on && liked && (liked.way_back != null) !== returnToStart()) {
    $("#find").disabled = true;
    if (liked.route) chooseComposed(liked.route.text, liked.name_origin === "hiker" ? liked.name : null);
    else choose(liked.id);
  }
}

// ---------- search ----------

// A hike-name box with a suggestion list; ``onPick(id)`` loads the chosen hike.
// A hit is listed one way, as walked: its length says so unless it ends at its start.
function nameSearch(input, list, onPick) {
  let timer = null, abort = null, items = [], active = -1;
  const show = (found, nothingFor = null) => {
    items = found;
    active = -1;
    list.innerHTML = "";
    if (nothingFor) {
      list.innerHTML = `<li class="nothing">No hike name has every word of “${escapeHtml(nothingFor)}”.<span class="meta">Only hike names are searched, not places; try a word of a trail's name, or select a hike on the map.</span></li>`;
    }
    found.forEach((item, i) => {
      const li = document.createElement("li");
      li.role = "option";
      li.id = `${list.id}-${i}`;
      const meta = [
        item.length_m != null && `${fmt.km(item.length_m)} ${item.round_trip ? "loop" : "one way"}`,
        climbText(item, item.elevation_gain_m),
        item.highest_m != null && `top ${fmt.m(item.highest_m)}`,
        `${item.trailhead[0].toFixed(2)}, ${item.trailhead[1].toFixed(2)}`,
      ].filter(Boolean).join(" · ");
      li.innerHTML = `${escapeHtml(item.name)}<span class="meta">${escapeHtml(meta)}</span>`;
      li.addEventListener("mousedown", (event) => { event.preventDefault(); pick(item.id); });
      list.appendChild(li);
    });
    list.hidden = found.length === 0 && !nothingFor;
    input.setAttribute("aria-expanded", String(found.length > 0));
  };
  const move = (step) => {
    if (!items.length) return;
    active = (active + step + items.length) % items.length;
    list.querySelectorAll("li").forEach((li, i) => li.setAttribute("aria-selected", String(i === active)));
    input.setAttribute("aria-activedescendant", `${list.id}-${active}`);
    document.getElementById(`${list.id}-${active}`).scrollIntoView({ block: "nearest" });
  };
  const pick = (id) => {
    clearTimeout(timer);
    abort?.abort();
    show([]);
    onPick(id);
  };
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const query = input.value.trim();
    if (query.length < 2) {
      abort?.abort();
      show([]);
      return;
    }
    timer = setTimeout(async () => {
      abort?.abort();
      abort = new AbortController();
      try {
        const body = await api(`/api/search?q=${encodeURIComponent(query)}&limit=20`, { signal: abort.signal });
        show(body.results, body.results.length ? null : query);
      } catch (error) {
        if (error.name !== "AbortError") setStatus(error.message, true);
      }
    }, 200);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") { event.preventDefault(); move(1); }
    else if (event.key === "ArrowUp") { event.preventDefault(); move(-1); }
    else if (event.key === "Enter" && items.length) { event.preventDefault(); pick(items[Math.max(active, 0)].id); }
    else if (event.key === "Escape") show([]);
  });
  input.addEventListener("blur", () => setTimeout(() => show([]), 150));
}

let chosen = 0, shown = 0, ranked = 0, paired = 0, boxed = 0; // the latest of each request; an older response that lands later is dropped

// ``fromLink``: the hike an address names; when it cannot be opened, the page shows no hike.
async function choose(id, { fromLink = false } = {}) {
  const request = ++chosen;
  setStatus("Loading the hike…");
  try {
    const hike = await api(`/api/hikes/${id}?return_to_start=${returnToStart()}`);
    if (request !== chosen) return;
    state.liked = hike;
    if (state.wishing) setWishing(false);
    $("#search").value = hike.name;
    $("#liked-name").textContent = `Chosen: ${hike.name}, ${lengthText(hike)}`;
    $("#find").disabled = false;
    $("#pair-search").disabled = false;
    $("#return-bar").hidden = false;
    history.replaceState(null, "", `#hike=${id}${modeHash()}`);
    renderDetail($("#liked"), hike, { role: "Liked hike" });
    $("#results").hidden = true;
    $("#selected").hidden = true;
    pick.restyle();
    pick.drawLoops();
    setStatus("");
  } catch (error) {
    if (request !== chosen) return;
    if (fromLink && namesNothing(error)) brokenLink(error); else setStatus(error.message, true);
  }
}

// The server read the request and found no such hike; not a dropped connection or a busy server.
const namesNothing = (error) => [400, 404, 422].includes(error.status);

// An address names no hike of this data: say so where the hike would be, and show none,
// so the page and its address agree.
function brokenLink(error) {
  console.warn("This link opens no hike:", error.message);
  state.liked = null;
  const card = $("#liked");
  if (card._map) { card._map.remove(); card._map = null; }
  card.classList.add("empty");
  card.innerHTML = `<p class="error-text"><strong>This link doesn't match the current trail data, so it shows no hike.</strong></p>
    <p>The trails may have changed since it was shared. Search for a hike on the left, or select one on the map.</p>`;
  $("#liked-name").textContent = "No hike chosen yet.";
  $("#search").value = "";
  $("#find").disabled = $("#pair-search").disabled = true;
  $("#return-bar").hidden = true;
  $("#results").hidden = $("#selected").hidden = true;
  pick.restyle();
  pick.drawLoops();
  setStatus("");
}

// ``from``: "link" (an address), "compose" (the composer's Use this hike) or null.
async function chooseComposed(route, name, from = null) {
  const request = ++chosen;
  setStatus("Measuring your hike…");
  try {
    const hike = await api("/api/compose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ route, name: name || null, return_to_start: returnToStart() }),
    });
    if (request !== chosen) return;
    state.liked = hike;
    if (state.wishing) setWishing(false);
    $("#search").value = "";
    $("#liked-name").textContent = `Chosen: ${hike.name}, ${lengthText(hike)}`;
    $("#compose-name").value = hike.name;
    compose.named = hike.name_origin === "hiker";
    $("#find").disabled = false;
    $("#pair-search").disabled = false;
    $("#return-bar").hidden = false;
    history.replaceState(null, "", routeHash(hike) + modeHash());
    renderDetail($("#liked"), hike, { role: "Your hike" });
    $("#results").hidden = true;
    $("#selected").hidden = true;
    pick.restyle();
    pick.drawLoops();
    setStatus("");
  } catch (error) {
    if (request !== chosen) return;
    if (from === "link" && namesNothing(error)) brokenLink(error);
    else {
      setStatus(error.message, true);
      if (from === "compose") pick.hint(`Not measured: ${error.message}`); // beside the button pressed
    }
  }
}

// A suggested loop becomes your hike, drawn on the map to go on from.
async function editComposed(hike) {
  await chooseComposed(hike.route.text, hike.name);
  if (state.liked?.route?.text !== hike.route.text) return;
  $("#map-pick-on").checked = true;
  pick.open();
  if (compose.on) compose.toggle(false);
  compose.start = compose.startKey = null;
  compose.route = [];
  compose.toggle(true);
}

// ---------- select on map ----------

// A map of the hikes in view (from PICK_ZOOM on, and only while the view holds
// at most the server's limit); clicking a line or an end chooses that hike.
const pick = {
  map: null,
  layer: null,
  lines: new Map(), // hike id -> its polyline
  loops: null, // the suggested loops and the shown row's versions of the Ranking
  timer: null,
  abort: null,

  open() {
    const card = $("#map-pick");
    card.hidden = false;
    if (!this.map) {
      // Canvas with a tolerance: a tap a few pixels off a thin line still hits it.
      this.map = L.map(card.querySelector(".pick-map"), { renderer: L.canvas({ tolerance: 10 }) });
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      }).addTo(this.map);
      this.layer = L.layerGroup().addTo(this.map);
      if (state.liked) this.map.setView(state.liked.trailhead, 13);
      else {
        const [south, west, north, east] = state.info.coverage;
        this.map.fitBounds([[south, west], [north, east]]);
      }
      this.map.on("moveend", () => this.load());
      this.drawLoops();
    }
    this.map.invalidateSize();
    this.load();
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  },

  close() {
    $("#map-pick").hidden = true;
    clearTimeout(this.timer);
    this.abort?.abort();
  },

  hint(text) {
    $("#map-pick-hint").textContent = text;
  },

  // The map view as the south, west, north and east of a box query.
  box() {
    const box = this.map.getBounds();
    return new URLSearchParams({
      south: Math.max(-90, box.getSouth()), west: Math.max(-180, box.getWest()),
      north: Math.min(90, box.getNorth()), east: Math.min(180, box.getEast()),
    });
  },

  load() {
    if (compose.on) { compose.load(); return; }
    clearTimeout(this.timer);
    this.abort?.abort();
    const request = ++boxed;
    if (this.map.getZoom() < PICK_ZOOM) {
      this.layer.clearLayers();
      this.lines.clear();
      this.hint("Zoom in on the map to see the hikes there as lines.");
      return;
    }
    this.timer = setTimeout(async () => {
      this.abort = new AbortController();
      this.hint("Loading the hikes in view…");
      try {
        const body = await api(`/api/hikes?${this.box()}`, { signal: this.abort.signal });
        if (request !== boxed || compose.on) return;
        this.draw(body.results);
        const count = body.results.length;
        if (body.too_many) this.hint("Too many hikes in view; zoom in further to see them as lines.");
        else if (!count) this.hint("No hikes in view; move the map.");
        else this.hint(`${count} hike${count === 1 ? "" : "s"} in view. Click a line to choose it.`);
      } catch (error) {
        if (error.name !== "AbortError" && request === boxed) this.hint(error.message);
      }
    }, 250);
  },

  draw(hikes) {
    this.layer.clearLayers();
    this.lines.clear();
    for (const hike of hikes) {
      const latlngs = decodePolyline(hike.line);
      const line = L.polyline(latlngs, PICK_LINE);
      const ends = [latlngs[0], latlngs.at(-1)].map((at, i) => L.circleMarker(at, {
        radius: 5, color: "#fff", weight: 1.5, fillColor: i === 0 ? "#2f7d4f" : "#1f2a24", fillOpacity: 1,
      }));
      const group = L.featureGroup([line, ...ends]).addTo(this.layer);
      group.bindTooltip(`<strong>${escapeHtml(hike.name)}</strong><br>${fmt.km(hike.walk_m)}`, { sticky: true });
      group.on("mouseover", () => { line.setStyle(PICK_HOVER); line.bringToFront(); });
      group.on("mouseout", () => line.setStyle(this.style(hike.id)));
      group.on("click", () => choose(hike.id));
      this.lines.set(hike.id, line);
    }
    this.restyle();
  },

  // The chosen hike stands out.
  style(id) {
    return id === state.liked?.id ? PICK_HOVER : PICK_LINE;
  },

  restyle() {
    for (const [id, line] of this.lines) line.setStyle(this.style(id));
    this.lines.get(state.liked?.id)?.bringToFront();
  },

  // The loops composed from segments in the shown Ranking (a catalog hike is on the map already),
  // and every version of the row shown, catalog or composed.
  drawLoops() {
    if (!this.map) return;
    if (!this.loops) this.loops = L.layerGroup().addTo(this.map);
    this.loops.clearLayers();
    if ($("#results").hidden) return;
    state.results.forEach((result, i) => {
      const versions = state.expanded === i ? [result, ...result.versions] : result.route ? [result] : [];
      versions.forEach((version, k) => {
        const label = versions.length > 1 ? `, var ${k + 1}` : "";
        const kind = version.route ? ", composed" : "";
        L.polyline(decodePolyline(version.line), k ? VERSION_LINE : version.route ? LOOP_LINE : RESULT_LINE)
          .bindTooltip(`<strong>Similar hike #${i + 1}${kind}${label}</strong><br>${escapeHtml(version.name)}<br>${fmt.km(version.length_m)}`, { sticky: true })
          .on("click", () => showResult(i, k))
          .addTo(this.loops);
      });
    });
  },
};

// ---------- design your own from segments ----------

// The marked trails in view as segments, junction to junction (from COMPOSE_ZOOM on).
// Click a junction or a segment to start, then segments to walk on; one away from the
// route's end is reached the shortest way over the trail segments loaded so far (and
// gaps or links the Hiker took themselves), drawn apart so they can see it. A dotted
// "connect here" link is offered where two trails pass close with no junction.
const compose = {
  on: false,
  layer: null,
  start: null, // junction id
  startKey: null, // its key in a shared link
  route: [], // { id, step, auto } in walking order; step names it in a shared link, auto when added to reach a click
  named: false, // the Hiker typed the name
  segments: new Map(), // every segment loaded so far, by id
  junctions: new Map(),
  shown: [], // segment ids in the last view
  timer: null,
  abort: null,

  toggle(on) {
    this.on = on;
    const button = $("#compose-on");
    button.setAttribute("aria-pressed", String(on));
    button.textContent = on ? "Back to whole hikes" : "Design your own from segments";
    $("#compose-tools").hidden = !on;
    $("#compose-readout").hidden = !on;
    if (!on) $("#compose-links").innerHTML = "";
    if (!this.layer) this.layer = L.layerGroup().addTo(pick.map);
    clearTimeout(pick.timer);
    pick.abort?.abort();
    pick.layer.clearLayers();
    pick.lines.clear();
    this.layer.clearLayers();
    if (on && state.liked?.route?.segments && this.start == null) {
      // Go on from the composed hike shown below (unless its link cuts a Segment of this data).
      const { start, segments, steps, text } = state.liked.route;
      this.start = start;
      this.startKey = text.split(":")[0];
      this.route = segments.map((id, at) => ({ id, step: steps[at], auto: false }));
      $("#compose-done").disabled = !this.route.length;
      if (state.liked.line) pick.map.fitBounds(L.latLngBounds(decodePolyline(state.liked.line)).pad(0.1));
    }
    pick.load();
  },

  load() {
    clearTimeout(this.timer);
    this.abort?.abort();
    const request = ++boxed;
    if (pick.map.getZoom() < COMPOSE_ZOOM) {
      this.shown = [];
      this.draw();
      pick.hint("Zoom in on the map to see the marked trails there as segments.");
      return;
    }
    this.timer = setTimeout(async () => {
      this.abort = new AbortController();
      pick.hint("Loading the trail segments in view…");
      try {
        const body = await api(`/api/segments?${pick.box()}`, { signal: this.abort.signal });
        if (request !== boxed || !this.on) return;
        for (const [id, lat, lon, degree, key] of body.junctions) this.junctions.set(id, { id, latlng: [lat, lon], degree, key });
        for (const [id, kind, a, b, length, climbAB, climbBA, line, sac, routes, stepAB, stepBA] of body.segments) {
          if (!this.segments.has(id)) {
            this.segments.set(id, { id, kind, a, b, length, climbAB, climbBA, latlngs: decodePolyline(line), sac, routes, stepAB, stepBA });
          }
        }
        this.shown = body.segments.map((segment) => segment[0]);
        this.draw();
        if (body.too_many) pick.hint("Too many trail segments in view; zoom in further to see them.");
        else if (!this.shown.length) pick.hint("No marked trails in view; move the map.");
        else this.hintNext();
      } catch (error) {
        if (error.name !== "AbortError" && request === boxed) pick.hint(error.message);
      }
    }, 250);
  },

  hintNext() {
    pick.hint(this.start == null
      ? "Click a junction or a trail segment to start your hike there. Only marked trails are segments, and a hike starts and ends at a junction."
      : "Click a segment to walk on it; Undo takes the last one back. Use this hike measures it.");
  },

  // The junction the route ends at so far (null while a segment of it is not loaded).
  end() {
    let here = this.start;
    for (const { id } of this.route) {
      const segment = this.segments.get(id);
      if (!segment) return null;
      here = segment.a === here ? segment.b : segment.a;
    }
    return here;
  },

  label(segment) {
    if (segment.kind === "link") {
      return `<strong>Connect here</strong><br>${fmt.m(segment.length)} between two trails that pass close, not on a marked trail`;
    }
    if (segment.kind === "gap") return `<strong>Gap</strong><br>${fmt.m(segment.length)} where two trails almost meet`;
    const parts = [`<strong>${escapeHtml(segment.routes.join(" · ") || "Marked trail")}</strong>`, fmt.km(segment.length)];
    if (segment.climbAB != null) parts.push(`↑ ${fmt.m(segment.climbAB)} / ${fmt.m(segment.climbBA)} the other way`);
    if (segment.sac) parts.push(escapeHtml(SAC[SAC_RANK[segment.sac]] || segment.sac));
    return parts.join("<br>");
  },

  draw() {
    this.layer.clearLayers();
    const end = this.end();
    const onRoute = new Set(this.route.map((step) => step.id));
    const picked = new Set(this.route.filter((step) => !step.auto).map((step) => step.id));
    const ends = new Set();
    for (const id of this.shown) {
      const segment = this.segments.get(id);
      if (segment.kind === "link" && end !== segment.a && end !== segment.b) continue; // offered at the route's end only
      if (segment.kind !== "link") ends.add(segment.a).add(segment.b);
      if (onRoute.has(id)) continue;
      const style = segment.kind === "link" ? LINK_LINE : segment.kind === "gap" ? SEGMENT_GAP : SEGMENT_LINE;
      this.line(segment, style);
    }
    const added = new Set(this.route.filter((step) => step.auto).map((step) => step.id));
    for (const id of onRoute) {
      const segment = this.segments.get(id);
      if (!segment) continue;
      if (picked.has(id)) this.line(segment, segment.kind === "trail" ? ROUTE_LINE : { ...ROUTE_LINE, dashArray: "4 8" });
      // Dashed over red where the way added walks a segment clicked too, so both show.
      if (added.has(id)) this.line(segment, AUTO_LINE, "Added to reach the segment you clicked");
    }
    for (const id of ends) {
      const junction = this.junctions.get(id);
      if (!junction || id === this.start || id === end) continue;
      const marker = L.circleMarker(junction.latlng, { radius: 4, color: "#fff", weight: 1, fillColor: "#1e3a8a", fillOpacity: 0.9 })
        .bindTooltip(this.start == null ? "Start here" : "Junction").addTo(this.layer);
      marker.on("click", () => this.startAt(id));
    }
    const at = (id, fill, text) => {
      const junction = this.junctions.get(id);
      if (junction) L.circleMarker(junction.latlng, { radius: 8, color: "#fff", weight: 2, fillColor: fill, fillOpacity: 1 }).bindTooltip(text).addTo(this.layer);
    };
    if (this.start != null) at(this.start, "#2f7d4f", "<strong>Start</strong>");
    if (this.route.length && end != null && end !== this.start) at(end, "#c2410c", "<strong>End so far</strong>");
    this.readout();
    this.offerLinks(end);
  },

  // A link is a few metres, often hidden under the end marker: offer each one at the
  // route's end as a button too.
  offerLinks(end) {
    const host = $("#compose-links");
    host.innerHTML = "";
    if (!this.on || end == null) return;
    for (const id of this.shown) {
      const link = this.segments.get(id);
      if (link.kind !== "link" || (link.a !== end && link.b !== end)) continue;
      const across = link.a === end ? link.b : link.a;
      const names = new Set();
      for (const segment of this.segments.values()) {
        if (segment.kind !== "link" && (segment.a === across || segment.b === across)) segment.routes.forEach((name) => names.add(name));
      }
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `Connect here: ${fmt.m(link.length)} across to ${[...names].slice(0, 2).join(", ") || "another trail"}`;
      button.title = "The two trails pass this close with no marked junction; cross only where you can.";
      button.addEventListener("click", () => this.add(link));
      host.appendChild(button);
    }
  },

  line(segment, style, note) {
    const line = L.polyline(segment.latlngs, style).addTo(this.layer);
    line.bindTooltip(this.label(segment) + (note ? `<br><em>${note}</em>` : ""), { sticky: true });
    line.on("mouseover", () => { line.setStyle(SEGMENT_HOVER); line.bringToFront(); });
    line.on("mouseout", () => line.setStyle(style));
    line.on("click", (event) => this.add(segment, event.latlng));
  },

  startAt(junction) {
    if (this.start != null) return;
    this.start = junction;
    this.startKey = this.junctions.get(junction).key;
    this.changed();
  },

  add(segment, latlng) {
    let started = false;
    if (this.start == null) {
      // Start at the end of the segment nearer the click.
      const click = [latlng.lat, latlng.lng];
      const [a, b] = [segment.a, segment.b].map((id) => this.junctions.get(id));
      this.start = a && b && metresBetween(click, b.latlng) < metresBetween(click, a.latlng) ? segment.b : segment.a;
      this.startKey = this.junctions.get(this.start).key;
      started = true;
    }
    const end = this.end();
    if (end == null) return;
    let way = [];
    if (end !== segment.a && end !== segment.b) {
      way = segment.kind === "link" ? null : this.shortest(end, segment);
      if (!way) {
        pick.hint("That segment is not connected to your route's end over the trails loaded so far. Pan the map along the trails between them, zoomed in so they load, then click it again; or click the segments in between.");
        return;
      }
    }
    const walked = new Set(this.route.map((step) => step.id));
    let here = end;
    for (const [step, auto] of [...way.map((s) => [s, true]), [segment, false]]) {
      this.route.push({ id: step.id, step: here === step.a ? step.stepAB : step.stepBA, auto });
      here = here === step.a ? step.b : step.a;
    }
    this.changed();
    if (started) {
      pick.hint(`Your hike starts at the end of ${segment.routes?.[0] ? `the ${segment.routes[0]} segment` : "that segment"} nearer your click (the green dot) and walks it to the other end; Clear, then click a junction, to start elsewhere.`);
    } else if (way.length) {
      const back = way.filter((s) => walked.has(s.id)).length;
      pick.hint(`Added ${way.length} segment${way.length === 1 ? "" : "s"} (amber, dashed) to reach it${back ? `, ${back} of them back along your own route` : ""}; Undo takes them back too, or click the segments you want in between.`);
    }
  },

  // The shortest way from a junction to either end of ``target`` over the trail
  // segments loaded so far, as segments, or null. A gap or link is crossed only
  // where the Hiker took it themselves.
  shortest(from, target) {
    const taken = new Set(this.route.map((step) => step.id));
    const touching = new Map();
    for (const segment of this.segments.values()) {
      if (segment.kind !== "trail" && !taken.has(segment.id)) continue;
      for (const id of [segment.a, segment.b]) {
        if (!touching.has(id)) touching.set(id, []);
        touching.get(id).push(segment);
      }
    }
    const best = new Map([[from, 0]]), via = new Map(), done = new Set();
    const queue = [[0, from]];
    while (queue.length) {
      queue.sort((x, y) => y[0] - x[0]); // a few hundred junctions: sorting is fast enough
      const [metres, here] = queue.pop();
      if (done.has(here)) continue;
      done.add(here);
      if (here === target.a || here === target.b) {
        const way = [];
        for (let at = here; at !== from;) {
          const segment = via.get(at);
          way.unshift(segment);
          at = segment.a === at ? segment.b : segment.a;
        }
        return way;
      }
      for (const segment of touching.get(here) || []) {
        const next = segment.a === here ? segment.b : segment.a, total = metres + segment.length;
        if (total < (best.get(next) ?? Infinity)) {
          best.set(next, total);
          via.set(next, segment);
          queue.push([total, next]);
        }
      }
    }
    return null;
  },

  // Takes back the last click: its segment and those added to reach it.
  undo() {
    if (this.route.length) {
      this.route.pop();
      while (this.route.at(-1)?.auto) this.route.pop();
      if (!this.route.length) this.named = false;
    } else this.start = this.startKey = null;
    this.changed();
  },

  clear() {
    this.start = this.startKey = null;
    this.route = [];
    this.changed();
  },

  changed() {
    if (this.start == null) this.named = false; // a new hike: its name is proposed again
    if (!this.named) $("#compose-name").value = "";
    this.draw();
    this.hintNext();
  },

  // Length and an estimate of the climb from the segments; "Use this hike" measures it exactly,
  // with its way back when Return to start is on. Over MAX_COMPOSED_M it cannot be used.
  readout() {
    const out = $("#compose-readout");
    out.classList.remove("over");
    $("#compose-done").disabled = !this.route.length;
    if (this.start == null) { out.textContent = "No hike drawn yet."; return; }
    let metres = 0, up = 0, down = 0, here = this.start, reused = false;
    const seen = new Set();
    for (const { id } of this.route) {
      const segment = this.segments.get(id);
      if (!segment) { out.textContent = `${this.route.length} segments; move the map over them to see the totals.`; return; }
      const forward = segment.a === here;
      metres += segment.length;
      up += (forward ? segment.climbAB : segment.climbBA) ?? 0;
      down += (forward ? segment.climbBA : segment.climbAB) ?? 0;
      reused ||= seen.has(id);
      seen.add(id);
      here = forward ? segment.b : segment.a;
    }
    const loop = this.route.length && here === this.start;
    const parts = [
      `${this.route.length} segment${this.route.length === 1 ? "" : "s"}`,
      `${fmt.km(metres)}${loop ? "" : " one way"}`,
      `about ↑ ${fmt.m(up)} ↓ ${fmt.m(down)}`,
    ];
    if (loop) parts.push("back at the start");
    else if (this.route.length && returnToStart()) parts.push("the way back to the start is added when you use it");
    if (reused) parts.push("walks a segment twice");
    const added = this.route.filter((step) => step.auto).length;
    if (added) parts.push(`${added} added to connect (amber)`);
    if (metres > MAX_COMPOSED_M) {
      parts.push(`over the ${fmt.km(MAX_COMPOSED_M)} a hike may be: Undo some of it`);
      out.classList.add("over");
      $("#compose-done").disabled = true;
    } else if (this.route.length && metres < SHORTEST_HIKE_M) {
      parts.push(`shorter than the ${fmt.km(SHORTEST_HIKE_M)} the catalog hikes start at`);
    }
    out.textContent = parts.join(" · ");
  },

  done() {
    if (this.start == null || !this.route.length) return;
    const route = `${this.startKey}:${this.route.map((step) => step.step).join(",")}`;
    chooseComposed(route, this.named ? $("#compose-name").value.trim() : null, "compose");
  },
};

// ---------- pair score ----------

// The score of the liked hike and any other one, under the weights and month on the left.
async function comparePair(id) {
  if (!state.liked) return;
  let query;
  try {
    query = {
      ...likedSeed(state.liked, "a"), b: id, ...readWeights(), month: Number($("#month").value) || null,
      return_to_start: state.liked.way_back != null,
    };
  } catch (error) {
    setStatus(error.message, true);
    return;
  }
  const request = ++paired, liked = state.liked;
  setStatus("Scoring the pair…");
  try {
    const [pair, hike] = await Promise.all([
      api("/api/pair", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(query) }),
      api(`/api/hikes/${id}?return_to_start=${query.return_to_start}`),
    ]);
    if (request !== paired || liked !== state.liked) return;
    $("#pair-search").value = hike.name;
    document.querySelectorAll("#results-table tr.active").forEach((r) => r.classList.remove("active"));
    const card = $("#selected");
    state.selected = { pair: id };
    renderDetail(card, hike, {
      role: "Compared with the liked hike", compare: liked, result: { ...pair, drive_hours: null },
      query: { weights: pair.weights, month: pair.month, return_to_start: pair.return_to_start },
    });
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    setStatus("");
  } catch (error) {
    if (request === paired && liked === state.liked) setStatus(error.message, true);
  }
}

// ---------- drive time ----------

function parseLatLon(text) {
  const match = text.trim().match(/^(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)$/);
  if (!match) return null;
  const lat = Number(match[1]), lon = Number(match[2]);
  return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? { lat, lon, label: `${lat}, ${lon}` } : null;
}

// A typed "lat, lon" outside the hikes' box whose swap is inside was most likely typed "lon, lat".
function swappedWarning(origin) {
  const [south, west, north, east] = state.info.coverage;
  const inside = (lat, lon) => lat >= south && lat <= north && lon >= west && lon <= east;
  if (inside(origin.lat, origin.lon) || !inside(origin.lon, origin.lat)) return null;
  return `${origin.lat}, ${origin.lon} is outside the area with hikes; did you mean ${origin.lon}, ${origin.lat}? Type latitude first.`;
}

async function findPlace() {
  const text = $("#origin").value.trim();
  if (!text) return;
  const parsed = parseLatLon(text);
  if (parsed) {
    const warning = swappedWarning(parsed);
    if (warning) $("#origin-found").textContent = warning; else setOrigin(parsed);
    return;
  }
  if (state.geocoded.has(text)) { setOrigin(state.geocoded.get(text)); return; }
  $("#origin-found").textContent = "Searching…";
  try {
    // One request per click (Nominatim usage policy: no autocomplete, cache results).
    const response = await fetch(`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q=${encodeURIComponent(text)}`);
    const [place] = await response.json();
    if (!place) { $("#origin-found").textContent = "Place not found; try a town name or lat, lon."; return; }
    const origin = { lat: Number(place.lat), lon: Number(place.lon), label: place.display_name };
    state.geocoded.set(text, origin);
    setOrigin(origin);
  } catch {
    $("#origin-found").textContent = "Place search failed; type lat, lon instead.";
  }
}

function setOrigin(origin) {
  state.origin = origin;
  $("#origin-found").textContent = `Start: ${origin.label} (${origin.lat.toFixed(4)}, ${origin.lon.toFixed(4)})`;
}

function useMyLocation() {
  if (!navigator.geolocation) { $("#origin-found").textContent = "This browser cannot share a location."; return; }
  $("#origin-found").textContent = "Asking the browser…";
  navigator.geolocation.getCurrentPosition(
    (position) => {
      const { latitude: lat, longitude: lon } = position.coords;
      $("#origin").value = `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
      setOrigin({ lat, lon, label: "your location" });
    },
    () => { $("#origin-found").textContent = "Location not shared; type a place or lat, lon."; },
  );
}

// ---------- ranking ----------

// What a Ranking is like: the liked hike, or the wish while the Hiker describes the hike they want.
function rankBasis() {
  return state.wishing ? "wish" : state.liked;
}

async function findSimilar() {
  const liked = rankBasis();
  if (!liked) return;
  let query;
  try {
    const month = Number($("#month").value) || null;
    if (state.wishing) {
      // Only the Measures given a value weigh (ADR-0024).
      const { weights: all } = readWeights(), wish = readWish(all);
      const weights = Object.fromEntries(Object.keys(wish).map((name) => [name, all[name]]));
      query = { wish, weights, month, return_to_start: returnToStart() };
    } else {
      query = { ...likedSeed(liked, "seed"), ...readWeights(), month, return_to_start: liked.way_back != null };
    }
    if ($("#drive-on").checked) {
      const typed = parseLatLon($("#origin").value);
      const warning = typed && swappedWarning(typed);
      if (warning) throw new Error(warning);
      const origin = typed || state.origin;
      if (!origin) throw new Error("Set a starting place for the drive-time filter.");
      const hours = Number($("#hours").value);
      if (!(hours >= 0.5 && hours <= state.info.max_drive_hours)) {
        throw new Error(`Max hours is from 0.5 to ${state.info.max_drive_hours}.`);
      }
      query.origin = { lat: origin.lat, lon: origin.lon };
      query.max_hours = hours;
    }
  } catch (error) {
    setStatus(error.message, true);
    return;
  }
  await rank(query, liked);
}

// Ask for a Ranking and show it, unless a later one was asked or the liked hike changed meanwhile;
// ``stale``: the query on the left is no longer this one.
async function rank(query, liked, stale = false) {
  const request = ++ranked;
  $("#find").disabled = $("#suggest-loops").disabled = true;
  setStatus(query.loops ? "Suggesting loops near these hikes…"
    : query.origin ? "Finding hikes within the drive, then ranking…" : "Ranking every hike…");
  try {
    const body = await api("/api/rank", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(query),
    });
    if (request !== ranked || liked !== rankBasis()) return;
    state.query = {
      weights: body.weights, month: body.month, drive: Boolean(query.origin), return_to_start: body.return_to_start,
      wish: query.wish ?? null,
    };
    state.asked = { query, basis: liked };
    state.results = body.results;
    renderResults(body);
    if (stale) markStale();
    setStatus(`Done in ${(body.elapsed_ms / 1000).toFixed(1)} s.`);
  } catch (error) {
    if (request !== ranked || liked !== rankBasis()) return;
    markStale();
    setStatus(error.message, true);
  } finally {
    if (request === ranked) $("#find").disabled = $("#suggest-loops").disabled = false;
  }
}

// The shown results no longer match the query on the left.
function markStale() {
  if ($("#results").hidden) return;
  $("#results").classList.add("stale");
  $("#results-stale").hidden = false;
}

function renderResults(body) {
  const section = $("#results");
  section.hidden = false;
  section.classList.remove("stale");
  $("#results-stale").hidden = true;
  $("#selected").hidden = true;
  const considered = body.reachable == null
    ? `${state.info.dayhikes.toLocaleString("en")} hikes`
    : `${body.reachable.toLocaleString("en")} hikes within the drive`;
  const mode = body.return_to_start ? "each with its way back to the start" : "each one way";
  const composed = body.results.some((result) => result.route)
    ? " Rows marked composed are loops drawn from marked trail segments, not catalog hikes; with Select hike on map ticked, that map shows them dashed green."
    : "";
  const versions = body.results.some((result) => result.versions.length)
    ? " Hikes starting within 10 km of a better one are its versions: click a row with versions to list the others (var 2, 3) under it and see each on the map."
    : "";
  $("#wish-card").hidden = true;
  $("#results-title").textContent = state.query.wish ? "Hikes closest to the one you want" : "Most similar hikes";
  $("#results-note").textContent = body.results.length
    ? `${state.query.wish ? "Closest to the hike you want" : `Like “${state.liked.name}”`}, out of ${considered}${body.loops ? " and loops composed near the best of them" : ""}, ${mode}. Click a row to see it.${composed}${versions}`
    : `No hike matched, out of ${considered}. Try a longer drive or other weights.`;
  $("#results-loops").hidden = !state.info.compose || body.loops; // SuggestedLoops on request (ADR-0027)
  const table = $("#results-table");
  const measureHeads = comparedMeasures(body.return_to_start).map((m) =>
    `<th class="measure${body.weights[m.name] ? "" : " excluded"}" title="${escapeHtml(`${m.label}: ${m.description}`)}">${escapeHtml(m.label)} ${helpButton(m.name)}</th>`).join("");
  const figures = body.return_to_start ? "with the way back" : "one way";
  table.innerHTML = `<thead><tr><th>#</th><th class="name">Hike</th><th>Score ${helpButton("overall")}</th>${state.query.drive ? "<th>Drive</th>" : ""}<th title="Length, ${figures}">Length${body.return_to_start ? " with way back" : ""}</th><th title="Climb and descent, ${figures}">Up / down</th>${measureHeads}</tr></thead>`;
  const tbody = document.createElement("tbody");
  state.expanded = null;
  body.results.forEach((result, i) => {
    const row = resultRow(result, i + 1, () => showResult(i));
    row.dataset.index = i;
    tbody.appendChild(row);
  });
  table.appendChild(tbody);
  pick.drawLoops();
  section.scrollIntoView({ behavior: "smooth", block: "start" });
}

// One row of the results table: the hike's figures and every Measure's score.
function resultRow(result, number, open) {
  const { weights, drive, return_to_start: back } = state.query;
  const row = document.createElement("tr");
  row.tabIndex = 0;
  const cells = comparedMeasures(back).map((m) => {
    const { score, note } = result.measures[m.name];
    if (score == null) return `<td class="cell none" title="${escapeHtml(note ?? "no data")}">–</td>`;
    if (!weights[m.name]) {
      return `<td class="cell left-out" title="${escapeHtml(m.label)}: ${score.toFixed(2)}, left out of this ranking">${Math.round(score * 100)}</td>`;
    }
    return `<td class="cell" style="background:${scoreColor(score)}" title="${escapeHtml(m.label)}: ${score.toFixed(2)}">${Math.round(score * 100)}</td>`;
  }).join("");
  const tag = result.route ? '<span class="composed-tag" title="A loop drawn from marked trail segments, not a catalog hike">composed</span> ' : "";
  const versions = result.versions?.length
    ? ` <span class="versions" title="Click to see each on the map and in this table">${result.versions.length + 1} versions</span>`
    : "";
  row.innerHTML = `<td>${number}</td><td class="name" title="${escapeHtml(result.name)}">${tag}${escapeHtml(result.name)}${versions}</td>
    <td class="score">${result.score.toFixed(3)}</td>
    ${drive ? `<td>${result.drive_hours.toFixed(1)} h</td>` : ""}
    <td class="figure">${result.length_m != null ? fmt.km(result.length_m) : "–"}</td>
    <td class="figure">${result.climb_m != null ? upDown(result.climb_m, result.descent_m) : "–"}</td>${cells}`;
  row.addEventListener("click", open);
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); }
  });
  return row;
}

// The versions of a row, catalog or composed, on the map, and under it as rows var 2 and 3:
// the row itself is var 1.
function expandVersions(index) {
  document.querySelectorAll("#results-table tr.version").forEach((row) => row.remove());
  document.querySelectorAll("#results-table tr[data-version]").forEach((row) => {
    delete row.dataset.version;
    row.firstElementChild.textContent = Number(row.dataset.index) + 1;
  });
  state.expanded = index;
  const result = state.results[index];
  let after = document.querySelector(`#results-table tr[data-index="${index}"]`);
  after.dataset.version = `${index}-0`;
  after.firstElementChild.textContent = `${index + 1}, var 1`;
  result.versions.forEach((version, k) => {
    const row = resultRow(version, `var ${k + 2}`, () => showResult(index, k + 1));
    row.classList.add("version");
    row.dataset.version = `${index}-${k + 1}`;
    after.after(row);
    after = row;
  });
  pick.drawLoops();
}

// What names a result across Rankings: a DayHike's id, a composed loop's route.
function resultKey(result) {
  return result.id ?? result.route.text;
}

// Show again the hike that was shown against the liked one, when the new Ranking still lists it.
function reopenSelected(selected) {
  if (selected?.pair != null) { comparePair(selected.pair); return; }
  state.results.forEach((result, index) => {
    const k = [result, ...result.versions].findIndex((version) => resultKey(version) === selected?.key);
    if (k >= 0) showResult(index, k);
  });
}

// Show result ``index`` (its version ``version`` when it has several) against the liked hike.
async function showResult(index, version = 0) {
  const { results } = state, liked = rankBasis();
  const versions = results[index].versions?.length ? [results[index], ...results[index].versions] : null;
  if (versions && state.expanded !== index) expandVersions(index);
  const result = versions ? versions[version] : results[index];
  document.querySelectorAll("#results-table tr.active").forEach((r) => r.classList.remove("active"));
  const selector = versions ? `tr[data-version="${index}-${version}"]` : `tr[data-index="${index}"]`;
  document.querySelector(`#results-table ${selector}`)?.classList.add("active");
  const request = ++shown;
  setStatus("Loading the hike…");
  try {
    const hike = result.route
      ? await api("/api/compose", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ route: result.route.text, name: result.name, return_to_start: state.query.return_to_start }),
      })
      : await api(`/api/hikes/${result.id}?return_to_start=${state.query.return_to_start}`);
    if (request !== shown || results !== state.results || liked !== rankBasis()) return;
    const card = $("#selected");
    state.selected = { key: resultKey(result) };
    const which = versions ? `, var ${version + 1} of ${versions.length}` : "";
    const role = `${state.query.wish ? "Hike" : "Similar hike"} #${index + 1}${result.route ? ", composed from trail segments" : ""}${which}`;
    renderDetail(card, hike, { role, compare: state.query.wish ? { wish: state.query.wish } : state.liked, result });
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    setStatus("");
  } catch (error) {
    if (request === shown && results === state.results && liked === rankBasis()) setStatus(error.message, true);
  }
}

// ---------- start ----------

async function start() {
  for (let m = 1; m <= 12; m++) $("#month").add(new Option(MONTHS[m - 1], String(m)));
  // The weights fold away on a narrow screen, also when the window narrows.
  const narrow = window.matchMedia("(max-width: 900px)");
  $("#weights-panel").open = !narrow.matches;
  narrow.addEventListener("change", () => { $("#weights-panel").open = !narrow.matches; });
  try {
    const [info, measures] = await Promise.all([api("/api/info"), api("/api/measures")]);
    state.info = info;
    state.groups = measures.groups;
    state.measures = measures.measures;
    state.scoreHelp = measures.score_help;
  } catch (error) {
    setStatus(`The server is not answering: ${error.message}`, true);
    return;
  }
  $("#count").textContent = state.info.dayhikes.toLocaleString("en");
  $("#results-scores").innerHTML = `<strong>Score:</strong> ${escapeHtml(state.scoreHelp.overall)}
    <strong>Measure cells:</strong> ${escapeHtml(state.scoreHelp.measure)} ${helpButton("measures")}`;
  scoreHelp.listen();
  buildWeights();
  $("#wish-on").hidden = !state.measures.some(wishable);
  $("#wish-on").addEventListener("click", () => {
    chosen++; // drops a hike still loading
    setWishing(true);
  });
  $("#wish-off").addEventListener("click", () => setWishing(false));
  if (!state.info.drive_time) {
    $("#drive-on").disabled = true;
    $("#drive-off").hidden = false;
  }
  nameSearch($("#search"), $("#suggestions"), choose);
  $("#map-pick-on").addEventListener("change", (event) => (event.target.checked ? pick.open() : pick.close()));
  $("#compose-bar").hidden = !state.info.compose;
  $("#compose-on").addEventListener("click", () => compose.toggle(!compose.on));
  $("#compose-undo").addEventListener("click", () => compose.undo());
  $("#compose-clear").addEventListener("click", () => compose.clear());
  $("#compose-done").addEventListener("click", () => compose.done());
  $("#compose-name").addEventListener("input", (event) => { compose.named = event.target.value.trim() !== ""; });
  nameSearch($("#pair-search"), $("#pair-suggestions"), comparePair);
  const wishToHash = () => { if (state.wishing) history.replaceState(null, "", wishHash()); };
  $("#reset-weights").addEventListener("click", () => {
    buildWeights();
    if (state.wishing) applyWish(""); // a wish asks nothing until the Hiker sets it
    markStale();
    wishToHash();
  });
  $("#weights").addEventListener("input", markStale);
  $("#weights").addEventListener("change", markStale);
  // The address keeps the wish as it is typed, so a reload or a shared link opens it again.
  $("#weights").addEventListener("input", wishToHash);
  $("#drive-on").addEventListener("change", (event) => { $("#drive").hidden = !event.target.checked; markStale(); });
  $("#origin").addEventListener("input", () => { state.origin = null; $("#origin-found").textContent = ""; markStale(); });
  $("#hours").addEventListener("input", markStale);
  $("#origin").addEventListener("keydown", (event) => { if (event.key === "Enter") findPlace(); });
  $("#origin-find").addEventListener("click", findPlace);
  $("#origin-here").addEventListener("click", useMyLocation);
  $("#month").addEventListener("change", () => {
    if (state.liked && !state.wishing) renderDetail($("#liked"), state.liked, { role: state.liked.route ? "Your hike" : "Liked hike" });
    markStale();
  });
  $("#find").addEventListener("click", findSimilar);
  // The shown Ranking again, with loops near its best hikes, though the query on the left changed since.
  $("#suggest-loops").addEventListener("click", () =>
    rank({ ...state.asked.query, loops: true }, state.asked.basis, $("#results").classList.contains("stale")));
  // Remeasure the liked hike in the other mode, and rank again when a Ranking is shown,
  // keeping the hike shown against it open.
  $("#return-on").addEventListener("change", async () => {
    syncRoundTrip();
    if (compose.on) compose.readout();
    const liked = state.liked;
    if (!liked && !state.wishing) return;
    const shown = !$("#results").hidden;
    const selected = $("#selected").hidden ? null : state.selected;
    if (state.wishing) history.replaceState(null, "", wishHash());
    else {
      if (liked.route) await chooseComposed(liked.route.text, liked.name_origin === "hiker" ? liked.name : null);
      else await choose(liked.id);
      if (state.liked === liked) return;
    }
    if (shown) await findSimilar();
    if (selected && (!shown || !$("#results").hidden)) reopenSelected(selected);
  });
  const openLinked = () => {
    const oneWay = location.hash.endsWith("&oneway");
    const hash = oneWay ? location.hash.slice(0, -"&oneway".length) : location.hash;
    const modeChanged = returnToStart() === oneWay;
    $("#return-on").checked = !oneWay;
    syncRoundTrip();
    const linked = hash.match(/^#hike=(\d+)$/);
    if (linked && (modeChanged || Number(linked[1]) !== state.liked?.id)) choose(Number(linked[1]), { fromLink: true });
    const drawn = hash.match(/^#compose=([^&]+)(?:&name=(.*))?$/);
    if (drawn && state.info.compose && (modeChanged || !(state.liked?.route && routeHash(state.liked) === hash))) {
      let name = null;
      try { name = drawn[2] ? decodeURIComponent(drawn[2]) : null; } catch { /* a broken name: propose one */ }
      chooseComposed(drawn[1], name?.slice(0, NAME_LENGTH), "link"); // a hand-made long name is cut, not refused
    }
    const wished = hash.match(/^#wish=([\w:.,-]*)$/);
    if (wished && state.measures.some(wishable)) {
      chosen++; // drops a hike still loading
      setWishing(true);
      applyWish(wished[1]);
      history.replaceState(null, "", wishHash());
    } else if (!linked && !drawn && /^#(hike|compose)=/.test(hash)) {
      chosen++; // drops a hike still loading
      brokenLink(new Error(`not a hike link: ${hash}`));
    }
  };
  window.addEventListener("hashchange", openLinked);
  openLinked();
}

start();
