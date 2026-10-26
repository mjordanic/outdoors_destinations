"use strict";
// DayHike Finder: plain JS over the JSON API in dayhike.web. Charts are hand-drawn SVG.

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
const LANDCOVER = [["tree_share", "Trees", "#2f6b3a"], ["shrub_share", "Shrub", "#8aa857"],
  ["grass_share", "Grass", "#c5d86d"], ["bare_share", "Bare", "#b7a68a"],
  ["snow_share", "Snow / ice", "#bcd7ea"], ["other_share", "Other", "#a7a9ac"]];
const SURFACE = [["paved_share", "Paved", "#6b7280"], ["gravel_share", "Gravel", "#c8a96a"],
  ["natural_share", "Natural", "#7f9d4f"], ["rock_share", "Rock", "#8d8d8d"]];

const PICK_ZOOM = 12; // the map shows hikes from this zoom on
const PICK_LINE = { color: "#6b2fa3", weight: 3, opacity: 0.85 };
const PICK_HOVER = { color: "#c2410c", weight: 5, opacity: 1 };

const state = {
  info: null,
  groups: [],
  measures: [],
  liked: null,
  query: null, // the weights and month of the last Ranking
  results: [],
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
    const detail = Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join("; ") : body.detail;
    throw new Error(detail || `Request failed (${response.status}).`);
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
  km: (m) => `${(m / 1000).toFixed(m < 10000 ? 1 : 0)} km`,
  m: (m) => `${(Math.round(m) + 0).toLocaleString("en")} m`, // + 0: no "-0 m"
  pct: (x) => `${Math.round(x * 100)} %`,
  yes: (x) => (x ? "yes" : "no"),
};

// Climb and descent in the drawn direction; the gain fact (the larger of the two) when they are missing.
function climbText(hike, gain) {
  if (hike.climb_m != null) return `↑ ${fmt.m(hike.climb_m)} ↓ ${fmt.m(hike.descent_m)}`;
  return gain != null ? `climb or descent ${fmt.m(gain)}` : null;
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
    case "elevation_gain": case "highest": case "lowest": case "tarmac_approach": return fmt.m(v);
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
    f.length_m != null && fmt.km(f.length_m),
    climbText(hike, f.elevation_gain_m),
    f.highest_m != null && `top ${fmt.m(f.highest_m)}`,
    f.lowest_m != null && `low ${fmt.m(f.lowest_m)}`,
    f.difficulty_rank != null && SAC[Math.round(f.difficulty_rank)],
    f.round_trip != null && (f.round_trip ? "round trip" : "one way / lollipop"),
  ].filter(Boolean);
  const scoreLine = result
    ? `<p class="hint">Similarity score ${result.score == null ? "– (no weighted measure has data for both)" : result.score.toFixed(3)}${result.drive_hours != null ? ` · about ${result.drive_hours.toFixed(1)} h drive` : ""}</p>`
    : "";
  card.innerHTML = `
    <div class="detail-head">
      <div><div class="role">${escapeHtml(role)}</div><h2>${escapeHtml(hike.name)}</h2></div>
      <a href="https://www.openstreetmap.org/relation/${hike.id}" target="_blank" rel="noopener">OpenStreetMap relation ${hike.id}</a>
    </div>
    ${scoreLine}
    <ul class="badges">${badges.map((b) => `<li>${escapeHtml(b)}</li>`).join("")}</ul>
    <div class="map"></div>
    <div class="legend">${LEGEND.map(([label, color]) => `<span style="--c:${color}">${label}</span>`).join("")}</div>
    <div class="slot-elevation"></div>
    <div class="charts"></div>
    <h3>${compare ? "Every measure: this hike against the liked one" : "The facts behind every measure"}</h3>
    <div class="table-wrap"><table class="facts"></table></div>`;
  const { map, hover } = drawMap(card.querySelector(".map"), hike);
  card._map = map;
  const slot = card.querySelector(".slot-elevation");
  slot.appendChild(elevationChart(hike, hover, slot.clientWidth));
  card.querySelector(".charts").append(
    stackedBar("Land cover along the route", shares(f, LANDCOVER), "No land-cover data for this area yet."),
    stackedBar("Trail surface", shares(f, SURFACE), "No surface tags on this route."),
    climateChart(f, month),
  );
  card.querySelector(".facts").replaceWith(factsTable(hike, compare, result, month, query?.weights));
}

function factsTable(hike, compare, result, month, weights) {
  const table = document.createElement("table");
  table.className = "facts";
  const head = compare
    ? "<tr><th>Measure</th><th>This hike</th><th>Liked hike</th><th>Score</th></tr>"
    : "<tr><th>Measure</th><th>Value</th></tr>";
  table.innerHTML = `<thead>${head}</thead>`;
  for (const group of state.groups) {
    const body = document.createElement("tbody");
    body.innerHTML = `<tr><th scope="rowgroup" colspan="${compare ? 4 : 2}">${escapeHtml(group)}</th></tr>`;
    for (const measure of state.measures.filter((m) => m.group === group)) {
      const row = document.createElement("tr");
      if (compare && weights && !weights[measure.name]) row.className = "excluded";
      const cells = [`<td title="${escapeHtml(measure.description)}">${escapeHtml(measure.label)}</td>`];
      const values = compare ? [hike, compare] : [hike];
      for (const _ of values) cells.push('<td class="num"></td>');
      if (compare) cells.push('<td class="num score"></td>');
      row.innerHTML = cells.join("");
      values.forEach((h, i) => {
        const cell = row.children[i + 1];
        if (measure.name === "profile") {
          const line = sparkline(h.profile_shape);
          if (line) cell.appendChild(line); else cell.innerHTML = '<span class="nodata">no data</span>';
        } else {
          const text = factText(measure, h.facts, month);
          cell.innerHTML = text == null ? '<span class="nodata">no data</span>' : escapeHtml(text);
        }
      });
      if (compare) {
        const score = result?.measures?.[measure.name]?.score;
        row.lastElementChild.innerHTML = score == null
          ? '<span class="nodata">no data</span>'
          : `<span class="bar"><i style="width:${Math.round(score * 100)}%"></i></span>${score.toFixed(2)}`;
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
    for (const measure of state.measures.filter((m) => m.group === group)) {
      const row = document.createElement("div");
      row.className = "weight";
      row.dataset.measure = measure.name;
      const id = `w-${measure.name}`;
      row.innerHTML = `
        <label class="name" for="${id}" title="${escapeHtml(measure.description)}">${escapeHtml(measure.label)}</label>
        <input id="${id}" type="number" min="0" max="100" step="1" value="${measure.default_weight}" inputmode="numeric" aria-label="${escapeHtml(measure.label)} weight, 0 to 100">
        <label class="off"><input type="checkbox" aria-label="Leave ${escapeHtml(measure.label)} out"> off</label>`;
      const [number, off] = row.querySelectorAll("input");
      off.addEventListener("change", () => {
        number.disabled = off.checked;
        row.classList.toggle("excluded", off.checked);
      });
      number.addEventListener("input", () => number.setCustomValidity(""));
      set.appendChild(row);
    }
    host.appendChild(set);
  }
}

function readWeights() {
  const weights = {}, excluded = [];
  for (const row of document.querySelectorAll(".weight")) {
    const [number, off] = row.querySelectorAll("input");
    if (off.checked) { excluded.push(row.dataset.measure); continue; }
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

// ---------- search ----------

// A hike-name box with a suggestion list; ``onPick(id)`` loads the chosen hike.
function nameSearch(input, list, onPick) {
  let timer = null, abort = null, items = [], active = -1;
  const show = (found) => {
    items = found;
    active = -1;
    list.innerHTML = "";
    found.forEach((item, i) => {
      const li = document.createElement("li");
      li.role = "option";
      li.id = `${list.id}-${i}`;
      const meta = [
        item.length_m != null && fmt.km(item.length_m),
        climbText(item, item.elevation_gain_m),
        item.highest_m != null && `top ${fmt.m(item.highest_m)}`,
        `${item.trailhead[0].toFixed(2)}, ${item.trailhead[1].toFixed(2)}`,
      ].filter(Boolean).join(" · ");
      li.innerHTML = `${escapeHtml(item.name)}<span class="meta">${escapeHtml(meta)}</span>`;
      li.addEventListener("mousedown", (event) => { event.preventDefault(); pick(item.id); });
      list.appendChild(li);
    });
    list.hidden = found.length === 0;
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
      if ($("#status").textContent.startsWith("No hike name has")) setStatus("");
      return;
    }
    timer = setTimeout(async () => {
      abort?.abort();
      abort = new AbortController();
      try {
        const body = await api(`/api/search?q=${encodeURIComponent(query)}&limit=20`, { signal: abort.signal });
        show(body.results);
        if (!body.results.length) setStatus(`No hike name has every word of “${query}”.`);
        else setStatus("");
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
  return { pick };
}

let chosen = 0, shown = 0, ranked = 0, paired = 0, boxed = 0; // the latest of each request; an older response that lands later is dropped

async function choose(id) {
  const request = ++chosen;
  setStatus("Loading the hike…");
  try {
    const hike = await api(`/api/hikes/${id}`);
    if (request !== chosen) return;
    state.liked = hike;
    $("#search").value = hike.name;
    $("#liked-name").textContent = `Chosen: ${hike.name}`;
    $("#find").disabled = false;
    $("#pair-search").disabled = false;
    history.replaceState(null, "", `#hike=${id}`);
    renderDetail($("#liked"), hike, { role: "Liked hike" });
    $("#results").hidden = true;
    $("#selected").hidden = true;
    pick.restyle();
    setStatus("");
  } catch (error) {
    if (request === chosen) setStatus(error.message, true);
  }
}

// ---------- select on map ----------

// A map of the hikes in view (from PICK_ZOOM on, and only while the view holds
// at most the server's limit); clicking a line or an end chooses that hike.
const pick = {
  map: null,
  layer: null,
  lines: new Map(), // hike id -> its polyline
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

  load() {
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
      const box = this.map.getBounds();
      const params = new URLSearchParams({
        south: Math.max(-90, box.getSouth()), west: Math.max(-180, box.getWest()),
        north: Math.min(90, box.getNorth()), east: Math.min(180, box.getEast()),
      });
      this.abort = new AbortController();
      this.hint("Loading the hikes in view…");
      try {
        const body = await api(`/api/hikes?${params}`, { signal: this.abort.signal });
        if (request !== boxed) return;
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
};

// ---------- pair score ----------

// The score of the liked hike and any other one, under the weights and month on the left.
async function comparePair(id) {
  if (!state.liked) return;
  let query;
  try {
    query = { a: state.liked.id, b: id, ...readWeights(), month: Number($("#month").value) || null };
  } catch (error) {
    setStatus(error.message, true);
    return;
  }
  const request = ++paired, liked = state.liked;
  setStatus("Scoring the pair…");
  try {
    const [pair, hike] = await Promise.all([
      api("/api/pair", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(query) }),
      api(`/api/hikes/${id}`),
    ]);
    if (request !== paired || liked !== state.liked) return;
    $("#pair-search").value = hike.name;
    document.querySelectorAll("#results-table tr.active").forEach((r) => r.classList.remove("active"));
    const card = $("#selected");
    renderDetail(card, hike, {
      role: "Compared with the liked hike", compare: liked, result: { ...pair, drive_hours: null },
      query: { weights: pair.weights, month: pair.month },
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

async function findSimilar() {
  if (!state.liked) return;
  let query;
  try {
    query = { seed: state.liked.id, ...readWeights(), month: Number($("#month").value) || null };
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
  const request = ++ranked;
  $("#find").disabled = true;
  setStatus(query.origin ? "Finding hikes within the drive, then ranking…" : "Ranking every hike…");
  try {
    const body = await api("/api/rank", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(query),
    });
    if (request !== ranked || query.seed !== state.liked.id) return;
    state.query = { weights: body.weights, month: body.month, drive: Boolean(query.origin) };
    state.results = body.results;
    renderResults(body);
    setStatus(`Done in ${(body.elapsed_ms / 1000).toFixed(1)} s.`);
  } catch (error) {
    if (request !== ranked || query.seed !== state.liked.id) return;
    markStale();
    setStatus(error.message, true);
  } finally {
    if (request === ranked) $("#find").disabled = false;
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
  $("#results-note").textContent = body.results.length
    ? `Like “${state.liked.name}”, out of ${considered}. Click a row to see it.`
    : `No hike matched, out of ${considered}. Try a longer drive or other weights.`;
  const table = $("#results-table");
  const measureHeads = state.measures.map((m) =>
    `<th class="measure${body.weights[m.name] ? "" : " excluded"}" title="${escapeHtml(`${m.label}: ${m.description}`)}">${escapeHtml(m.label)}</th>`).join("");
  table.innerHTML = `<thead><tr><th>#</th><th class="name">Hike</th><th>Score</th>${state.query.drive ? "<th>Drive</th>" : ""}${measureHeads}</tr></thead>`;
  const tbody = document.createElement("tbody");
  body.results.forEach((result, i) => {
    const row = document.createElement("tr");
    row.tabIndex = 0;
    const cells = state.measures.map((m) => {
      const score = result.measures[m.name].score;
      if (score == null) return '<td class="cell none" title="no data">–</td>';
      if (!body.weights[m.name]) {
        return `<td class="cell left-out" title="${escapeHtml(m.label)}: ${score.toFixed(2)}, left out of this ranking">${Math.round(score * 100)}</td>`;
      }
      return `<td class="cell" style="background:${scoreColor(score)}" title="${escapeHtml(m.label)}: ${score.toFixed(2)}">${Math.round(score * 100)}</td>`;
    }).join("");
    row.innerHTML = `<td>${i + 1}</td><td class="name" title="${escapeHtml(result.name)}">${escapeHtml(result.name)}</td>
      <td class="score">${result.score.toFixed(3)}</td>
      ${state.query.drive ? `<td>${result.drive_hours.toFixed(1)} h</td>` : ""}${cells}`;
    const open = () => showResult(i, row);
    row.addEventListener("click", open);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); }
    });
    tbody.appendChild(row);
  });
  table.appendChild(tbody);
  section.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function showResult(index, row) {
  document.querySelectorAll("#results-table tr.active").forEach((r) => r.classList.remove("active"));
  row.classList.add("active");
  const { results, liked } = state;
  const result = results[index];
  const request = ++shown;
  setStatus("Loading the hike…");
  try {
    const hike = await api(`/api/hikes/${result.id}`);
    if (request !== shown || results !== state.results || liked !== state.liked) return;
    const card = $("#selected");
    renderDetail(card, hike, { role: `Similar hike #${index + 1}`, compare: state.liked, result });
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    setStatus("");
  } catch (error) {
    if (request === shown && results === state.results && liked === state.liked) setStatus(error.message, true);
  }
}

// ---------- start ----------

async function start() {
  for (let m = 1; m <= 12; m++) $("#month").add(new Option(MONTHS[m - 1], String(m)));
  if (window.matchMedia("(max-width: 900px)").matches) $("#weights-panel").open = false;
  try {
    const [info, measures] = await Promise.all([api("/api/info"), api("/api/measures")]);
    state.info = info;
    state.groups = measures.groups;
    state.measures = measures.measures;
  } catch (error) {
    setStatus(`The server is not answering: ${error.message}`, true);
    return;
  }
  $("#count").textContent = state.info.dayhikes.toLocaleString("en");
  buildWeights();
  if (!state.info.drive_time) {
    $("#drive-on").disabled = true;
    $("#drive-off").hidden = false;
  }
  const likedSearch = nameSearch($("#search"), $("#suggestions"), choose);
  $("#map-pick-on").addEventListener("change", (event) => (event.target.checked ? pick.open() : pick.close()));
  nameSearch($("#pair-search"), $("#pair-suggestions"), comparePair);
  $("#reset-weights").addEventListener("click", () => { buildWeights(); markStale(); });
  $("#weights").addEventListener("input", markStale);
  $("#weights").addEventListener("change", markStale);
  $("#drive-on").addEventListener("change", (event) => { $("#drive").hidden = !event.target.checked; markStale(); });
  $("#origin").addEventListener("input", () => { state.origin = null; $("#origin-found").textContent = ""; markStale(); });
  $("#hours").addEventListener("input", markStale);
  $("#origin").addEventListener("keydown", (event) => { if (event.key === "Enter") findPlace(); });
  $("#origin-find").addEventListener("click", findPlace);
  $("#origin-here").addEventListener("click", useMyLocation);
  $("#month").addEventListener("change", () => {
    if (state.liked) renderDetail($("#liked"), state.liked, { role: "Liked hike" });
    markStale();
  });
  $("#find").addEventListener("click", findSimilar);
  const openLinked = () => {
    const linked = location.hash.match(/^#hike=(\d+)$/);
    if (linked && Number(linked[1]) !== state.liked?.id) likedSearch.pick(Number(linked[1]));
  };
  window.addEventListener("hashchange", openLinked);
  openLinked();
}

start();
