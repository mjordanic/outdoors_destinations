"use strict";
// HikesAlike: plain JS over the JSON API in dayhike.web. Charts are hand-drawn SVG.

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
// A Landmark's kind as the hike search says it (serving.export_landmarks).
const LANDMARK_KINDS = { peak: "peak", hut: "hut", shelter: "shelter", viewpoint: "viewpoint", lake: "lake",
  pass: "pass", ridge: "ridge", town: "town", village: "village" };
const PEAK_SUMMITED_M = 50; // a hike this near a peak reaches its top (similarity.PEAK_SUMMITED_M)
const SAC = ["", "T1 hiking", "T2 mountain hiking", "T3 demanding mountain", "T4 alpine hiking",
  "T5 demanding alpine", "T6 difficult alpine"];
const SAC_RANK = { hiking: 1, mountain_hiking: 2, demanding_mountain_hiking: 3, alpine_hiking: 4,
  demanding_alpine_hiking: 5, difficult_alpine_hiking: 6 };
const LANDCOVER = [["tree_share", "Trees", "#2f6b3a"], ["shrub_share", "Shrub", "#8aa857"],
  ["grass_share", "Grass", "#c5d86d"], ["bare_share", "Bare", "#b7a68a"],
  ["snow_share", "Snow / ice", "#bcd7ea"], ["other_share", "Other", "#a7a9ac"]];
const SURFACE = [["paved_share", "Paved", "#6b7280"], ["gravel_share", "Gravel", "#c8a96a"],
  ["natural_share", "Natural", "#7f9d4f"], ["rock_share", "Rock", "#8d8d8d"]];

// The API's origin: the page's own, unless the page names another (a loading shell, docs/deploy.md).
const API_BASE = ($('meta[name="api-base"]')?.content ?? "").replace(/\/+$/, "");
const WAKE_AFTER_MS = 1500; // a first answer slower than this is a server starting from zero

const PICK_ZOOM = 12; // the map shows hikes from this zoom on
const PICK_LINE = { color: "#6b2fa3", weight: 3, opacity: 0.85 };
const PICK_HOVER = { color: "#c2410c", weight: 5, opacity: 1 };
const PICK_TOLERANCE_PX = 10; // a click this far off a hike's line or dot still chooses it
const START_PANE = "starts"; // the map pane of the hikes' start and end dots, above every line (overlayPane is 400)
const COMPOSE_ZOOM = 13; // designing a hike shows the trail segments from this zoom on
const JUNCTION_PX = 8; // a click this near a junction's dot is for the junction, not the segment under it
const MAX_COMPOSED_M = 100000; // the longest hike the server measures (trailgraph.MAX_WALK_M)
const SHORTEST_HIKE_M = 1000; // the shortest catalog hike
const ROUND_TRIP_M = 50; // a hike ending this near its start is a round trip, its way back a short step (catalog.GAP_JOIN_M)
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
  kept: {}, // the weights of the mode not shown: { seed, wish }
  query: null, // the weights, month and return to start of the last Ranking
  asked: null, // what the shown Ranking asked the server, and of which liked hike or wish: { query, basis }
  results: [], // the rows shown, every page's
  answer: null, // the answer of the shown Ranking's first page
  pools: [], // per page shown, the rows the server answered with, best first: its rows are drawn from them
  picked: new Set(), // the rows shown that Strict order would not list on their page
  draw: 0, // the draw number of the rows shown: a random one per Find, one more per Redraw (ADR-0047)
  strict: false, // Strict order: each page lists its best rows as ranked, not drawn
  expanded: null, // the result row whose versions (var 1, 2, 3) are shown
  selected: null, // the hike shown against the liked one: { key } of a result, or { pair } a DayHike id
  origin: null,
  mapAt: null, // where the hike map opens next: the last Landmark or start place found while it was closed
  missed: null, // the place text the last lookup found nothing for
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

// A request that failed, said in ``host``, the line beside the button that asked it, so a phone shows it
// where the Hiker looks; with a Try again button (``again``) when the server was busy or did not answer
// (ADR-0055).
function sayFailed(host, error, again) {
  host.textContent = error.message;
  host.classList.add("error");
  if (error.status && ![409, 503].includes(error.status)) return;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "secondary retry";
  button.textContent = "Try again";
  button.addEventListener("click", () => {
    host.textContent = "";
    host.classList.remove("error");
    again();
  });
  host.append(" ", button);
}

// A request ends after this long, so a server that does not answer never leaves the page waiting:
// a Ranking takes up to about 15 s (a 4 h drive), anything else a few.
const TIMEOUT_S = 60;
// This tab's name for the server's Ranking queue: one search of its own at a time, so people behind one
// address (a home, an office) do not wait on each other's searches (web.TAB_HEADER, ADR-0054).
const TAB_HEADER = "X-HikesAlike-Tab";
const TAB = Array.from(crypto.getRandomValues(new Uint32Array(4)), (n) => n.toString(36)).join("-");
const RANK_RETRY_MS = 1500; // a Ranking refused while this tab's previous one runs is asked again this often

async function api(path, options = {}) {
  const timer = new AbortController(), timeout = setTimeout(() => timer.abort(), TIMEOUT_S * 1000);
  const stop = () => timer.abort();
  options.signal?.addEventListener("abort", stop);
  let response;
  try {
    if (options.signal?.aborted) throw options.signal.reason;
    response = await fetch(API_BASE + path, { ...options, signal: timer.signal });
  } catch (error) {
    if (options.signal?.aborted) throw error; // a newer request took its place
    // ``reason``: what went wrong, to go into a longer message.
    throw timer.signal.aborted
      ? Object.assign(new Error(`The server did not answer within ${TIMEOUT_S} s; try again in a minute.`), { reason: `the server did not answer within ${TIMEOUT_S} s` })
      : Object.assign(new Error("The server could not be reached; check your connection, or try again in a minute."), { reason: "the server could not be reached" });
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener("abort", stop);
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 429) throw Object.assign(new Error("Too many requests; wait a minute and try again."), { status: 429 });
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

// Climb and descent in the drawn direction, said with the way back or one way when the hike is
// measured either way (``way``); the gain fact (the larger of the two) when they are missing.
function climbText(hike, gain, way = "") {
  if (hike.climb_m != null) return `${upDown(hike.climb_m, hike.descent_m)}${way && ` ${way}`}`;
  return gain != null ? `climb or descent ${fmt.m(gain)}` : null;
}

// A hike's length as drawn, and with its way back when it is measured with one:
// "3.3 km one way (6.6 km with the way back)".
function lengthText(hike) {
  const back = hike.way_back;
  if (!back || back.kind === "none") return fmt.km(hike.facts.length_m ?? hike.walk_m);
  return `${fmt.km(hike.walk_m)} one way (${fmt.km(hike.facts.length_m ?? hike.walk_m + back.m)} with the way back)`;
}

// A multi-day route's mark, with its length one way: "multi-day, 1,073 km" (ADR-0056).
function multiDayText(hike) {
  return `multi-day, ${Math.round(hike.walk_m / 1000).toLocaleString("en")} km`;
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
    case "southness": return `${v.toFixed(2)} (${v >= 0.6 ? "south-facing" : v <= 0.4 ? "north-facing" : "mixed or flat"})`;
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

// The address of the page showing ``hike`` as the liked one, with the search settings on the left.
function likedHash(hike) {
  return `${hike.route ? routeHash(hike) : `#hike=${hike.id}`}${modeHash()}`;
}

// The full address that opens ``hike`` as the liked one, to send to a friend.
function hikeAddress(hike) {
  return `${location.origin}${location.pathname}${likedHash(hike)}`;
}

// Heights at ``points`` along a hike's line, from its ElevationProfile (heights at equal steps along
// the walk) by the share of the line walked to each point; null for each without a profile.
function heightsAlong(points, elevation) {
  if (!elevation) return points.map(() => null);
  const along = [0];
  for (let i = 1; i < points.length; i++) along.push(along[i - 1] + metresBetween(points[i - 1], points[i]));
  const metres = elevation.metres, last = metres.length - 1, total = along.at(-1) || 1;
  return along.map((m) => {
    const x = (m / total) * last, i = Math.min(Math.floor(x), last - 1);
    return last < 1 ? metres[0] : Math.round(metres[i] + (metres[i + 1] - metres[i]) * (x - i));
  });
}

// A GPX track of ``points`` with their ``heights`` (a height of null is left out).
function gpxTrack(name, points, heights) {
  const trkpts = points.map(([lat, lon], i) => (heights[i] == null
    ? `<trkpt lat="${lat}" lon="${lon}"/>`
    : `<trkpt lat="${lat}" lon="${lon}"><ele>${heights[i]}</ele></trkpt>`));
  return `<trk><name>${escapeHtml(name)}</name><trkseg>
${trkpts.join("\n")}
</trkseg></trk>`;
}

// The way back of ``hike`` as GPX ``points`` and ``heights``, the hike's own drawn the other way, a
// straight step, or a trail way back the server finds again (ADR-0019); null when it has none.
async function wayBackTrack(hike, points, heights) {
  const back = hike.way_back;
  if (!back || back.kind === "none") return null;
  if (back.kind === "retrace") return { points: [...points].reverse(), heights: [...heights].reverse() };
  if (back.kind === "step") return { points: [points.at(-1), points[0]], heights: [heights.at(-1), heights[0]] };
  // A designed hike carries its trail way back; a catalog hike's is asked for once.
  hike.wayBackLine ??= hike.route ? back : await api(`/api/hikes/${hike.id}/way-back`);
  if (!hike.wayBackLine.line) return null;
  return { points: decodePolyline(hike.wayBackLine.line), heights: hike.wayBackLine.ele };
}

// ``hike`` as a GPX 1.1 file for hiking and GPS apps: its line as served with heights from its
// profile, credited to OpenStreetMap and the elevation map; measured with its way back, the way
// back as a second track named so.
async function gpxText(hike) {
  const name = escapeHtml(hike.name), url = escapeHtml(hikeAddress(hike));
  const points = decodePolyline(hike.line), heights = heightsAlong(points, hike.elevation);
  const tracks = [gpxTrack(hike.name, points, heights)];
  const back = await wayBackTrack(hike, points, heights);
  if (back) tracks.push(gpxTrack(`${hike.name}: way back to the start (${fmt.km(hike.way_back.m)}, ${WAY_BACK[hike.way_back.kind]})`, back.points, back.heights));
  const { fabdem, copernicus } = state.info?.attribution ?? {};
  const dem = hike.elevation && (fabdem ?? copernicus);
  return `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="HikesAlike" xmlns="http://www.topografix.com/GPX/1/1">
<metadata><name>${name}</name><desc>Route data © OpenStreetMap contributors, ODbL 1.0.${dem ? ` Heights: ${escapeHtml(dem)}` : ""}</desc>
<copyright author="OpenStreetMap contributors"><license>https://opendatacommons.org/licenses/odbl/</license></copyright>
<link href="${url}"><text>${name}</text></link></metadata>
${tracks.join("\n")}
</gpx>
`;
}

// Saves ``hike`` as a GPX file named after it; ``button`` says while the way back is fetched.
async function downloadGpx(hike, button) {
  const base = hike.name.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  button.disabled = true;
  button.textContent = "Preparing GPX…";
  try {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([await gpxText(hike)], { type: "application/gpx+xml" }));
    link.download = `${base || "hike"}.gpx`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  } catch (error) {
    setStatus(`The GPX could not be made: ${error.message}`, true);
  } finally {
    button.disabled = false;
    button.textContent = "Download GPX";
  }
}

// Sends ``hike``'s address to a friend: through a phone's share sheet, else copied for pasting (a
// computer's share sheet may never answer). ``note`` beside the button says what happened.
async function shareHike(hike, button, note) {
  const url = hikeAddress(hike);
  const say = (html) => {
    note.innerHTML = html;
    note.hidden = false;
    clearTimeout(note.timer);
    note.timer = setTimeout(() => { note.hidden = true; }, 6000);
  };
  if (navigator.share && matchMedia("(pointer: coarse)").matches) {
    say("Opening your phone's share menu…");
    try {
      await navigator.share({ title: hike.name, url });
      say("Shared.");
      return;
    } catch (error) {
      if (error.name === "AbortError") return; // the Hiker closed the share sheet
      // No share sheet here after all: copy the link instead.
    }
  }
  try {
    await navigator.clipboard.writeText(url);
    button.textContent = "Link copied";
    setTimeout(() => { button.textContent = "Share"; }, 2000);
    say("Link copied: paste it in a message or a note to share this hike.");
  } catch {
    clearTimeout(note.timer); // the link stays until the Hiker copies it
    note.innerHTML = `Copy this link to share the hike: <input type="text" readonly value="${escapeHtml(url)}" aria-label="Link to share">`;
    note.hidden = false;
    note.querySelector("input").select();
  }
}

// Whether every hike is measured with its way back to its start; an address has
// "&oneway" when every hike is compared one way.
function returnToStart() {
  return $("#return-on").checked;
}

// The search settings on the left as an address's parts, so a shared link or a reload opens the same
// search (ADR-0054): "&oneway", "&strict", "&month=1", "&w=length:50,hut_along:100" (the weights moved
// from their defaults; a wish carries its own), "&adv" (Advanced search open), "&from=46.05,14.5",
// "&place=Ljubljana%2C%20SI", "&hours=2", "&km=50" (only hikes near a starting place) and "&group=3".
function modeHash() {
  const parts = [];
  if (!returnToStart()) parts.push("oneway");
  if (state.strict) parts.push("strict");
  if ($("#month").value) parts.push(`month=${$("#month").value}`);
  if (!state.wishing) {
    const moved = [...document.querySelectorAll(".weight")].filter((row) => {
      const measure = state.measures.find((m) => m.name === row.dataset.measure);
      return Number(row.querySelector("input:not(.wish)").value) !== measure.default_weight;
    });
    if (moved.length) parts.push(`w=${moved.map((row) => `${row.dataset.measure}:${row.querySelector("input:not(.wish)").value}`).join(",")}`);
  }
  if ($("#advanced").open) parts.push("adv");
  if ($("#drive-on").checked) {
    const typed = parseLatLon($("#origin").value), origin = typed || state.origin;
    if (origin) {
      parts.push(`from=${+origin.lat.toFixed(5)},${+origin.lon.toFixed(5)}`);
      if (!typed) parts.push(`place=${encodeURIComponent(origin.label)}`);
    }
    if ($("#hours-on").checked && !$("#hours-on").disabled) parts.push(`hours=${encodeURIComponent($("#hours").value)}`);
    if ($("#km-on").checked) parts.push(`km=${encodeURIComponent($("#km").value)}`);
  }
  const group = $("#group-km").value.trim();
  if (group) parts.push(`group=${encodeURIComponent(group)}`);
  return parts.map((part) => `&${part}`).join("");
}

// An address's parts as names and values, in any order: "#hike=12&month=1&strict" is hike 12, month 1
// and strict (""). Names are read in lower case; a part the page does not know is left alone.
function readLink(hash) {
  const parts = new Map();
  for (const part of hash.replace(/^#/, "").split("&")) {
    if (!part) continue;
    const at = part.indexOf("=");
    parts.set((at < 0 ? part : part.slice(0, at)).toLowerCase(), at < 0 ? "" : part.slice(at + 1));
  }
  return parts;
}

// ``text`` decoded from an address, or null where it is not valid percent-encoding.
function decoded(text) {
  try { return decodeURIComponent(text); } catch { return null; }
}

// The hike or wish an address opens, without its settings: "#hike=12", "#compose=…&name=…" or "#wish=…".
function linkBase(hash) {
  const parts = readLink(hash);
  if (parts.has("hike")) return `#hike=${parts.get("hike")}`;
  if (parts.has("compose")) return `#compose=${parts.get("compose")}${parts.has("name") ? `&name=${parts.get("name")}` : ""}`;
  if (parts.has("wish")) return `#wish=${parts.get("wish")}`;
  return "";
}

// The address keeps the search settings as they change, so a reload or a shared link opens them again.
function syncAddress() {
  refreshHikeLinks();
  if (state.wishing) { history.replaceState(null, "", wishHash()); return; }
  const base = linkBase(location.hash);
  if (base) history.replaceState(null, "", base + modeHash());
}

// Every "Link to this hike" shown carries the search settings as they are now, the same address Share
// sends (ADR-0055).
function refreshHikeLinks() {
  for (const card of [$("#liked"), $("#selected")]) {
    for (const link of card.querySelectorAll("a.hike-link")) link.setAttribute("href", likedHash(card._hike));
  }
}

// The address of what the page shows now: the wish, or the liked hike, with the search settings.
function shownAddress() {
  return state.wishing ? wishHash() : state.liked ? likedHash(state.liked) : "";
}

// Fills the search settings an address's ``parts`` (readLink) name; those it does not name stay as they
// are (on a page just opened, their defaults). ``weights``: also the weights (not a wish's).
function applySettings(parts, weights) {
  const month = parts.get("month");
  if (month != null) $("#month").value = /^([1-9]|1[0-2])$/.test(month) ? month : "";
  if (weights && parts.has("w")) {
    const moved = new Map((parts.get("w") ?? "").split(",").map((part) => part.split(":")));
    for (const row of document.querySelectorAll(".weight")) {
      const measure = state.measures.find((m) => m.name === row.dataset.measure);
      const weight = Number(moved.get(row.dataset.measure));
      setWeight(row, moved.get(row.dataset.measure) && Number.isFinite(weight)
        ? Math.min(100, Math.max(0, Math.round(weight / 10) * 10)) : measure.default_weight);
    }
  }
  if (parts.has("adv")) $("#advanced").open = true;
  const from = parseLatLon(decoded(parts.get("from") ?? "") ?? "");
  if (from || parts.has("hours") || parts.has("km")) {
    $("#drive-on").checked = true;
    $("#drive").hidden = false;
    const hours = Number(parts.get("hours")), km = Number(parts.get("km"));
    if (parts.has("hours") || parts.has("km")) {
      $("#hours-on").checked = parts.has("hours") && !$("#hours-on").disabled;
      $("#km-on").checked = parts.has("km");
    }
    if (hours > 0) $("#hours").value = String(hours);
    if (km > 0) $("#km").value = String(km);
    if (from) {
      const label = decoded(parts.get("place") ?? "")?.trim().slice(0, 200) || from.label;
      $("#origin").value = label;
      if (label !== from.label) state.geocoded.set(label, { ...from, label });
      setOrigin({ ...from, label });
    }
  }
  const group = Number(parts.get("group"));
  if (parts.has("group")) $("#group-km").value = group >= 0.1 && group <= 50 ? String(group) : "";
  showGroupDefault();
  syncRoundTrip();
}

// The limits as the page opens, before an address fills them (``start``), for ``resetSettings``.
const OPENED = {};

// The settings an address can name, as the page opens: the home page's address, gone back to, names none.
function resetSettings() {
  $("#month").value = "";
  for (const row of document.querySelectorAll(".weight")) {
    setWeight(row, state.measures.find((m) => m.name === row.dataset.measure).default_weight);
  }
  $("#advanced").open = false;
  $("#drive-on").checked = false;
  $("#drive").hidden = true;
  placed++; // drops a place lookup still running
  $("#origin").value = $("#origin-found").textContent = "";
  state.origin = state.missed = null;
  for (const [id, { value, checked }] of Object.entries(OPENED)) {
    $(id).value = value;
    $(id).checked = checked;
  }
  $("#group-km").value = "";
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
  // A cue while more of the text is below the box's edge (ADR-0054).
  return `<button type="button" class="help-close" aria-label="Close">×</button>${head}${sections}<p class="help-more" aria-hidden="true">More below ↓ scroll</p>`;
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
    this.cue();
    if (pinned) box.focus();
  },
  // "More below" shows while the text goes on under the box's edge.
  cue() {
    const box = $("#score-help");
    box.classList.toggle("more", box.scrollTop + box.clientHeight < box.scrollHeight - 4);
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
    window.addEventListener("resize", () => { if (this.button) { this.place(); this.cue(); } });
    $("#score-help").addEventListener("scroll", () => this.cue());
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
  card._hike = hike; // its "Link to this hike" follows the settings (refreshHikeLinks)
  card.hidden = false;
  card.classList.remove("empty");
  const month = compare ? query?.month : Number($("#month").value) || null;
  const f = hike.facts;
  const badges = [
    hike.multi_day && multiDayText(hike),
    hike.region && `near ${hike.region}`,
    f.length_m != null && lengthText(hike),
    climbText(hike, f.elevation_gain_m, hike.way_back ? (hike.way_back.kind === "none" ? "" : "with the way back") : f.round_trip ? "" : "one way"),
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
        ? `<span><a class="hike-link" href="${likedHash(hike)}" title="This address opens this hike as your hike">Link to this hike</a>${compare ? ' <button type="button" class="secondary edit-composed">Edit on the map</button>' : ""}</span>`
        : hike.built ? builtLinks(hike, compare)
        : `<span><a href="https://www.openstreetmap.org/relation/${hike.id}" target="_blank" rel="noopener">OpenStreetMap relation ${hike.id}</a> · <a class="hike-link" href="${likedHash(hike)}" title="This address opens this hike">Link to this hike</a></span>`}
    </div>
    ${hike.line ? '<p class="hike-actions"><button type="button" class="secondary share-hike">Share</button> <button type="button" class="secondary download-gpx">Download GPX</button> <span class="share-note" role="status" hidden></span></p>' : ""}
    ${scoreLine}
    ${sourceLine(hike)}
    ${hike.route ? `<p class="hint">Drawn from trail segments${hike.routes?.length ? ` along ${escapeHtml(hike.routes.slice(0, 5).join(", "))}${hike.routes.length > 5 ? " and more" : ""}` : ""}; measured as drawn${hike.way_back ? ", with its way back" : ""}.</p>` : ""}
    ${hike.way_back && hike.way_back.kind !== "none" ? `<p class="hint">Length, climb, descent and every measure include the way back to the start (${escapeHtml(wayBackText(hike.way_back))}); the map and profile show the hike itself.</p>` : ""}
    <ul class="badges">${badges.map((b) => `<li>${escapeHtml(b)}</li>`).join("")}</ul>
    <div class="map"></div>
    <div class="legend">${LEGEND.map(([label, color]) => `<span style="--c:${color}">${label}</span>`).join("")}</div>
    <div class="slot-elevation"></div>
    <div class="charts"></div>
    <h3>${compare ? `Every measure: this hike against the ${compare.wish ? "one you want" : "liked one"}` : "The facts behind every measure"}</h3>
    <p class="hint uncounted-note" hidden></p>
    <div class="table-wrap"><table class="facts"></table></div>`;
  card.querySelector(".share-hike")?.addEventListener("click", (event) => shareHike(hike, event.currentTarget, card.querySelector(".share-note")));
  card.querySelector(".download-gpx")?.addEventListener("click", (event) => downloadGpx(hike, event.currentTarget));
  card.querySelector(".edit-composed")?.addEventListener("click", () => editComposed(
    hike.built ? { route: { text: hike.built.route }, name: hike.name } : hike,
  ));
  const { map, hover } = drawMap(card.querySelector(".map"), hike);
  card._map = map;
  const slot = card.querySelector(".slot-elevation");
  slot.appendChild(elevationChart(hike, hover, slot.clientWidth));
  card.querySelector(".charts").append(
    stackedBar("Land cover along the route", shares(f, LANDCOVER), "No land-cover data for this area yet."),
    stackedBar("Trail surface", shares(f, SURFACE), "No surface tags on this route."),
    climateChart(f, month),
  );
  card.querySelector(".facts").replaceWith(factsTable(hike, compare, result, month, query, uncountedOf(compare, query)));
  const note = card.querySelector(".uncounted-note"), line = compare && uncountedLine(query);
  note.hidden = !line;
  note.textContent = line || "";
}

// Why a Measure a query weighs 0 is not counted, as the detail table says it: weighted 0, behind a
// closed Advanced search, or, while the Hiker describes a hike, one no value can be asked of.
const UNCOUNTED = {
  weight: { cell: "not counted (weight 0)", why: "have weight 0", how: "set their weight" },
  advanced: { cell: "not counted (Advanced search closed)", why: "are behind a closed Advanced search", how: "open Advanced search" },
  wish: { cell: "not counted (cannot be asked)", why: "cannot be asked of a hike you describe" },
};

// Each weight row's reason, kept with a query so its hikes say why a Measure did not count.
function uncountedReasons() {
  const reasons = {};
  for (const row of document.querySelectorAll(".weight")) {
    // Opening Advanced search counts a row only if it has a weight above 0: weight 0 wins over folded.
    const held = folded(row) && Number(row.querySelector("input:not(.wish)").value) > 0;
    reasons[row.dataset.measure] = state.wishing && row.classList.contains("unwishable") ? "wish" : held ? "advanced" : "weight";
  }
  return reasons;
}

// The reason of each Measure a compared hike's table shows that the query does not count, by name.
function uncountedOf(compare, query) {
  if (!compare || !query?.weights) return {};
  return Object.fromEntries(comparedMeasures(query.return_to_start).filter((m) => !query.weights[m.name])
    .map((m) => [m.name, UNCOUNTED[query.uncounted?.[m.name]] ?? UNCOUNTED.weight]));
}

// The line above a compared hike's table while some of its rows are greyed: why, and how to count them.
function uncountedLine(query) {
  const reasons = [...new Set(Object.values(uncountedOf(true, query)))];
  if (!reasons.length) return "";
  const hows = reasons.map((r) => r.how).filter(Boolean);
  return `Greyed measures ${reasons.map((r) => r.why).join(" or ")} and are not part of the score.${hows.length ? ` To count them, ${hows.join(" or ")}${hows.length > 1 ? "," : ""} in step 2.` : ""}`;
}

// Where a hike not from OpenStreetMap comes from (ADR-0059): its sources register id and its record
// there; an OSM hike's head already links its relation.
function sourceLine(hike) {
  const source = hike.source;
  if (!source || source.id === "osm") return "";
  const ref = source.url
    ? `<a href="${escapeHtml(source.url)}" target="_blank" rel="noopener">${escapeHtml(source.ref)}</a>`
    : escapeHtml(source.ref);
  return `<p class="hint source-line">Source: ${escapeHtml(source.id)}, record ${ref}</p>`;
}

// A built hike's head (ADR-0038): the OSM relations it is built from, a link to it,
// and, beside a liked hike when the server can compose, its Route to edit on the map.
function builtLinks(hike, compare) {
  const parts = hike.built.parts.map((id) => `<a href="https://www.openstreetmap.org/relation/${id}" target="_blank" rel="noopener">${id}</a>`);
  const edit = compare && state.info.compose ? ' <button type="button" class="secondary edit-composed">Edit on the map</button>' : "";
  return `<span>Built from OSM relation${parts.length === 1 ? "" : "s"} ${parts.join(", ")} · <a class="hike-link" href="${likedHash(hike)}" title="This address opens this hike">Link to this hike</a>${edit}</span>`;
}

// The Measures a comparison shows: with the way back every hike is a round trip, so not Round trip.
function comparedMeasures(returnToStart) {
  return state.measures.filter((m) => !(returnToStart && m.name === "round_trip"));
}

function factsTable(hike, compare, result, month, query, uncounted) {
  const table = document.createElement("table");
  table.className = "facts";
  const head = compare
    ? `<tr><th>Measure</th><th>This hike</th><th>${compare.wish ? "You want" : "Liked hike"}</th><th>Score ${helpButton("measures")}</th></tr>`
    : "<tr><th>Measure</th><th>Value</th></tr>";
  table.innerHTML = `<thead>${head}</thead>`;
  for (const group of state.groups) {
    const body = document.createElement("tbody");
    body.innerHTML = `<tr><th scope="rowgroup" colspan="${compare ? 4 : 2}">${escapeHtml(group.label)}</th></tr>`;
    for (const measure of (compare ? comparedMeasures(query?.return_to_start) : state.measures).filter((m) => m.group === group.name)) {
      const row = document.createElement("tr");
      const reason = uncounted[measure.name], fade = reason ? ' class="faded"' : "";
      if (reason) row.className = "excluded";
      const cells = [`<td${fade} title="${escapeHtml(measure.description)}">${escapeHtml(measure.label)}</td>`];
      const values = compare ? [hike, compare] : [hike];
      for (const h of values) cells.push(`<td class="num${reason && !h.wish ? " faded" : ""}"></td>`);
      if (compare) cells.push('<td class="num score"></td>');
      row.innerHTML = cells.join("");
      values.forEach((h, i) => {
        const cell = row.children[i + 1];
        if (h.wish) {
          const wanted = h.wish[measure.name];
          cell.innerHTML = reason ? `<span class="uncounted">${reason.cell}</span>`
            : wanted == null ? '<span class="nodata">left out</span>' : escapeHtml(wishText(measure, wanted));
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
        const unasked = compare.wish && compare.wish[measure.name] == null; // a wish asks nothing of it
        const shown = score == null
          ? (note ? `– ${helpButton(measure.name)}<small class="note">${escapeHtml(note)}</small>`
            : reason && unasked ? "" : `<span class="nodata">${unasked ? "left out" : "no data"}</span>`)
          : `<span class="bar"><i style="width:${Math.round(score * 100)}%"></i></span>${score.toFixed(2)} ${helpButton(measure.name)}${note ? `<small class="note">${escapeHtml(note)}</small>` : ""}`;
        // A Measure not counted keeps its score, faded, with why in plain text, not only in colour.
        row.lastElementChild.innerHTML = reason
          ? `${shown && `<span class="faded">${shown}</span>`}<small class="uncounted">${reason.cell}</small>` : shown;
      }
      body.appendChild(row);
    }
    table.appendChild(body);
  }
  return table;
}

// ---------- weights ----------

// The basic group shows; the others fold away behind Advanced search, and count only while it is open (ADR-0031).
function buildWeights() {
  $("#weights").innerHTML = $("#advanced-weights").innerHTML = "";
  for (const group of state.groups) {
    const basic = group.name === "basic";
    const set = document.createElement("fieldset");
    set.className = "weights-group";
    set.innerHTML = basic ? "" : `<legend>${escapeHtml(group.label)}</legend>`;
    const measures = state.measures.filter((m) => m.group === group.name);
    set.classList.toggle("unwishable", !measures.some(wishable));
    for (const measure of measures) {
      const row = document.createElement("div");
      row.className = wishable(measure) ? "weight" : "weight unwishable";
      row.dataset.measure = measure.name;
      const id = `w-${measure.name}`;
      row.innerHTML = `
        <label class="name" for="${id}" title="${escapeHtml(measure.description)}">${escapeHtml(measure.label)}</label>
        ${wishable(measure) ? wishInput(measure) : ""}
        <input id="${id}" type="range" min="0" max="100" step="10" value="${measure.default_weight}" aria-label="${escapeHtml(measure.label)} weight, 0 to 100">
        <output for="${id}"></output>`;
      if (measure.name === "length") {
        row.insertAdjacentHTML("beforeend", '<p class="hint length-note">Over about 25 km is usually more than a day. With Return to start a line counts there and back.</p>');
      }
      if (measure.name === "round_trip") {
        row.insertAdjacentHTML("beforeend", '<p class="hint weight-note">Left out with Return to start: every hike is then a round trip.</p>');
      }
      const slider = row.querySelector("input:not(.wish)"), value = row.querySelector("input.wish");
      slider.addEventListener("input", () => {
        showWeight(row);
        if (state.wishing) askDefault(row);
      });
      // Typing a value of a Measure left at 0 asks it in full. A yes/no box at 0 shows "yes" already,
      // so its first click asks that "yes"; a second click asks "no".
      for (const event of ["input", "change"]) {
        value?.addEventListener(event, () => {
          value.setCustomValidity(""); // a value typed again is checked again at Find
          if (Number(slider.value) !== 0 || (value.type !== "checkbox" && value.value === "")) return;
          setWeight(row, 100);
          if (value.type === "checkbox") value.checked = true;
        });
      }
      showWeight(row);
      set.appendChild(row);
    }
    $(basic ? "#weights" : "#advanced-weights").appendChild(set);
  }
  syncRoundTrip();
}

// A weight row's slider value, shown beside it; at 0 the Measure is off and its name struck through.
function showWeight(row) {
  const slider = row.querySelector("input:not(.wish)"), text = Number(slider.value) === 0 ? "off" : slider.value;
  row.querySelector("output").textContent = text;
  slider.setAttribute("aria-valuetext", text);
  row.classList.toggle("excluded", text === "off");
}

function setWeight(row, weight) {
  row.querySelector("input:not(.wish)").value = weight;
  showWeight(row);
}

// A wish weight given to a Measure with no value yet asks its default value.
function askDefault(row) {
  const value = row.querySelector("input.wish");
  if (value?.value === "" && Number(row.querySelector("input:not(.wish)").value) > 0) value.value = value.placeholder;
}

// Round trip tells no hike from another while every hike returns to its start: greyed out then.
function syncRoundTrip() {
  const row = document.querySelector('.weight[data-measure="round_trip"]');
  if (!row) return;
  row.classList.toggle("unused", returnToStart());
  row.querySelector("input:not(.wish)").disabled = returnToStart();
}

// A weight row the query leaves out: unused, or not wishable while wishing.
function leftOut(row) {
  return row.classList.contains("unused") || (state.wishing && row.classList.contains("unwishable"));
}

function readWeights() {
  const weights = {}, excluded = [];
  for (const row of document.querySelectorAll(".weight")) {
    if (leftOut(row)) {
      excluded.push(row.dataset.measure);
      continue;
    }
    weights[row.dataset.measure] = folded(row) ? 0 : Number(row.querySelector("input:not(.wish)").value);
  }
  if (!Object.values(weights).some((w) => w > 0)) throw new Error("Give at least one measure a weight above 0.");
  return { weights, excluded };
}

// A weight row behind a closed Advanced search: it counts nothing.
function folded(row) {
  return row.closest("#advanced") != null && !$("#advanced").open;
}

// ---------- the hike you want (no Seed) ----------

// A Measure the Hiker can ask a value of: its "wish" input (ADR-0024); a server without wishes gives none.
const wishable = (measure) => measure.wish != null;
const yesNo = (measure) => measure.wish.kind === "yes/no";
// A number wish is sent in the Measure's fact units; the page may show it in another, such as km for metres (ADR-0035).
const shownUnit = (measure) => measure.wish.display?.unit ?? measure.wish.unit;
const perShown = (measure) => measure.wish.display?.per ?? 1;
const toShown = (measure, value) => Math.round((value / perShown(measure)) * 1e6) / 1e6;
const toFact = (measure, value) => Math.round(value * perShown(measure) * 1e6) / 1e6;

// The value input of a weight row: a yes/no toggle, or a number in the Measure's unit and range.
function wishInput(measure) {
  const label = escapeHtml(measure.label), wish = measure.wish;
  if (yesNo(measure)) {
    return `<label class="wish-value"><input class="wish" type="checkbox"${wish.default ? " checked" : ""} aria-label="${label} you want"> yes</label>`;
  }
  const range = ["min", "max", "step"].filter((key) => wish[key] != null).map((key) => ` ${key}="${escapeHtml(toShown(measure, wish[key]))}"`).join("");
  const unit = shownUnit(measure) ? escapeHtml(shownUnit(measure)) : "";
  // Empty until the Hiker asks it, so no value looks asked while its weight is off; the default shows as a hint.
  return `<span class="wish-value"><input class="wish" type="number"${range} value="" placeholder="${escapeHtml(toShown(measure, wish.default))}" inputmode="decimal"
    aria-label="${label} you want${unit ? `, in ${unit}` : ""}">${unit ? `<span class="unit">${unit.replaceAll("/", "/<wbr>")}</span>` : ""}</span>`;
}

// What the Hiker wants of each Measure they weight (a measure weighted 0 or left out asks nothing).
function readWish(weights) {
  const wish = {};
  for (const measure of state.measures.filter(wishable)) {
    if (!(weights[measure.name] > 0)) continue;
    const input = document.querySelector(`.weight[data-measure="${measure.name}"] input.wish`);
    if (yesNo(measure)) { wish[measure.name] = input.checked; continue; }
    input.setCustomValidity(""); // valid unless it is out of range again
    const value = toFact(measure, Number(input.value)), { min, max } = measure.wish, unit = shownUnit(measure);
    if (input.value === "" || !Number.isFinite(value) || value < (min ?? -Infinity) || value > (max ?? Infinity)) {
      const range = `${toShown(measure, min)} to ${toShown(measure, max)}${unit ? ` ${unit}` : ""}`;
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
  if (!measure.wish) return value.toLocaleString("en");
  return `${toShown(measure, value).toLocaleString("en")}${shownUnit(measure) ? ` ${shownUnit(measure)}` : ""}`;
}

// The wish as an address: "#wish=elevation_gain:800:100,hut_along:1:50", each Measure the Hiker
// weights with its value and weight, as typed; a value shown in another unit, such as km, in the
// Measure's fact units, so an address reads the same whatever the page shows.
function wishHash() {
  const parts = [];
  for (const row of document.querySelectorAll(".weight:not(.unwishable)")) {
    const [value, number] = row.querySelectorAll("input");
    if (folded(row) || !(Number(number.value) > 0)) continue;
    parts.push(`${row.dataset.measure}:${value.type === "checkbox" ? Number(value.checked) : typedFact(row, value.value)}:${number.value}`);
  }
  return `#wish=${parts.join(",")}${modeHash()}`;
}

// A typed number in its Measure's fact units, or the text as typed when it is no number.
function typedFact(row, typed) {
  const measure = state.measures.find((m) => m.name === row.dataset.measure), value = Number(typed);
  return typed === "" || !Number.isFinite(value) ? typed : String(toFact(measure, value));
}

// The weight a wish starts a Measure at: the liked-hike form's default, but Lake or sea off, as no
// share it could ask is neutral (0 % ranks lakes down, 50 % up); the Hiker turns it on (ADR-0057).
function wishDefaultWeight(row) {
  return row.dataset.measure === "waterside" ? 0
    : state.measures.find((m) => m.name === row.dataset.measure).default_weight;
}

// Fills the form from a wish address: every wishable Measure it does not name weighs 0. An address
// naming none (a wish just opened, or reset) starts from the default weights, as the liked-hike form does.
function applyWish(text) {
  const asked = new Map(text.split(",").filter(Boolean).map((part) => [part.split(":")[0], part.split(":")]));
  for (const row of document.querySelectorAll(".weight:not(.unwishable)")) {
    if (!asked.size) {
      setWeight(row, wishDefaultWeight(row));
      askDefault(row);
      continue;
    }
    const value = row.querySelector("input.wish");
    const [, wanted, weight] = asked.get(row.dataset.measure) ?? [];
    if (wanted != null) {
      const measure = state.measures.find((m) => m.name === row.dataset.measure);
      if (value.type === "checkbox") value.checked = wanted === "1";
      else value.value = wanted === "" || !Number.isFinite(Number(wanted)) ? wanted : String(toShown(measure, Number(wanted)));
      if (row.closest("#advanced")) $("#advanced").open = true;
    }
    setWeight(row, Number(weight) > 0 ? Math.max(Number(weight), 10) : 0);
    askDefault(row);
  }
  syncRoundTrip();
}

// Each mode keeps its own weights: a wish starts from the default weights, each Measure on asking
// its shown default value, and a liked hike's weights are as they were when the Hiker comes back.
function swapWeights(on) {
  const rows = [...document.querySelectorAll(".weight")];
  state.kept[on ? "seed" : "wish"] = rows.map((row) => row.querySelector("input:not(.wish)").value);
  const back = state.kept[on ? "wish" : "seed"]
    ?? rows.map(wishDefaultWeight);
  rows.forEach((row, i) => {
    setWeight(row, back[i]);
    if (on) askDefault(row);
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
  syncFind();
  $("#return-bar").hidden = !on && !state.liked;
  if (on) {
    $("#weights-panel").open = true;
    $("#map-pick-on").checked = false;
    pick.close();
    setStatus("");
  }
  history.replaceState(null, "", on ? wishHash() : state.liked ? likedHash(state.liked) : location.pathname + location.search);
  const liked = state.liked;
  if (!on && liked && (liked.way_back != null) !== returnToStart()) remeasure(liked);
}

// ---------- search ----------

// How near a Landmark's hike comes to it: a town's hike starts near it, a lake's is measured from its GeoNames point.
function nearText(landmark, offM) {
  if (landmark.kind === "town" || landmark.kind === "village") return `starts ${fmt.km(offM)} from it`;
  if (landmark.kind === "peak" && offM <= PEAK_SUMMITED_M) return "reaches the top";
  if (landmark.kind === "lake") return `passes ${fmt.km(offM)} from its middle`;
  return offM <= PEAK_SUMMITED_M ? "passes it" : `passes ${fmt.km(offM)} from it`;
}

// A name as Enter compares it with the one typed: no accents, case or punctuation.
function foldName(text) {
  return text.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

// The suggestion Enter picks with none chosen by the keys: the one hike named exactly as typed, or the
// only hike found; else none, and the list stays to pick from (ADR-0054).
function enterPick(typed, found) {
  const exact = found.filter((item) => foldName(item.name) === foldName(typed));
  if (exact.length === 1) return { item: exact[0], why: "the hike with exactly that name" };
  if (found.length === 1 && found[0].match !== "similar") return { item: found[0], why: "the only hike found" };
  return null;
}

// A hike-name box with a suggestion list; ``onPick(id, note)`` loads the chosen hike, ``note`` saying
// why Enter picked it.
// A hit is listed one way, as walked: its length says so unless it ends at its start.
// With an export's Landmarks the list also has the hikes near a peak, hut, lake, pass or town named
// like the query (ADR-0053); ``onLandmark(landmark, open)`` then moves a map there, ``open`` asking to open it.
function nameSearch(input, list, onPick, onLandmark = null) {
  let timer = null, abort = null, items = [], active = -1, entered = false, listed = null, named = [];
  const option = (item, extra = null) => {
    const li = document.createElement("li");
    li.role = "option";
    li.id = `${list.id}-${items.length}`;
    li.tabIndex = -1; // focusable, so the accessibility tree lists each option to click
    li.setAttribute("aria-selected", "false");
    const meta = [
      extra,
      // A hit is walked one way, so its climb is too: the liked hike's panel adds the way back.
      [item.multi_day ? multiDayText(item) : item.length_m != null && `${fmt.km(item.length_m)} ${item.round_trip ? "loop" : "one way"}`,
        climbText(item, item.elevation_gain_m)].filter(Boolean).join(", "),
      item.highest_m != null && `top ${fmt.m(item.highest_m)}`,
      // Where it starts, so hikes of one name are told apart (ADR-0055).
      item.region && `near ${item.region}`,
      `${item.trailhead[0].toFixed(2)}, ${item.trailhead[1].toFixed(2)}`,
    ].filter(Boolean).join(" · ");
    const similar = item.match === "similar" ? ' <span class="similar-tag">similar name</span>' : "";
    li.innerHTML = `${escapeHtml(item.name)}${similar}<span class="meta">${escapeHtml(meta)}</span>`;
    li.addEventListener("mousedown", (event) => { event.preventDefault(); pick(item.id); });
    list.appendChild(li);
    items.push(item);
  };
  const note = (html, className = "nothing") => {
    const li = document.createElement("li");
    li.className = className;
    li.role = "presentation";
    li.innerHTML = html;
    list.appendChild(li);
    return li;
  };
  const show = (found, landmarks = [], nothingFor = null) => {
    items = [];
    named = found;
    listed = found.length || landmarks.length || nothingFor ? input.value.trim() : null;
    active = -1;
    list.innerHTML = "";
    const query = escapeHtml(input.value.trim());
    if (nothingFor) {
      list.innerHTML = state.info.landmark_search
        ? `<li class="nothing">No hike name, peak or place has every word of “${escapeHtml(nothingFor)}”.<span class="meta">Try a word of a trail's name, a peak, hut, lake or town, or select a hike on the map.</span></li>`
        : `<li class="nothing">No hike name has every word of “${escapeHtml(nothingFor)}”.<span class="meta">Only hike names are searched, not places; try a word of a trail's name, or select a hike on the map.</span></li>`;
    }
    found.filter((item) => item.match !== "similar").forEach((item) => option(item));
    for (const landmark of landmarks) {
      const where = [LANDMARK_KINDS[landmark.kind] ?? landmark.kind, landmark.town && `near ${landmark.town}`, landmark.country]
        .filter(Boolean).join(", ");
      const similar = landmark.match === "similar" ? ' <span class="similar-tag">similar name</span>' : "";
      const head = note(`Hikes near ${escapeHtml(landmark.name)} (${escapeHtml(where)})${similar}`, "landmark");
      if (onLandmark) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "link";
        button.textContent = "Show on map";
        button.addEventListener("mousedown", (event) => { event.preventDefault(); show([]); onLandmark(landmark, true); });
        head.appendChild(button);
      }
      landmark.hikes.forEach((item) => option(item, nearText(landmark, item.off_m)));
      const more = landmark.hike_count - landmark.hikes.length;
      if (more > 0) note(`<span class="meta">and ${more.toLocaleString("en")} more${onLandmark ? ": show them on the map" : ""}</span>`);
    }
    const similar = found.filter((item) => item.match === "similar");
    if (similar.length) {
      // Only names like the query: say so before the Hiker picks one for the hike they meant.
      note(items.length ? `Hike names like “${query}”:` : `No hike name has every word of “${query}” as typed. Names like it:`);
      similar.forEach((item) => option(item));
    }
    list.hidden = items.length === 0 && !nothingFor;
    input.setAttribute("aria-expanded", String(!list.hidden));
    input.removeAttribute("aria-activedescendant"); // its option is gone
    if (landmarks.length && onLandmark) onLandmark(landmarks[0], false);
    if (entered && items.length) decide();
  };
  const move = (step) => {
    if (!items.length) return;
    active = (active + step + items.length) % items.length;
    list.querySelectorAll('li[role="option"]').forEach((li, i) => li.setAttribute("aria-selected", String(i === active)));
    input.setAttribute("aria-activedescendant", `${list.id}-${active}`);
    document.getElementById(`${list.id}-${active}`).scrollIntoView({ block: "nearest" });
  };
  const pick = (id, note = null) => {
    clearTimeout(timer);
    abort?.abort();
    entered = false;
    show([]);
    onPick(id, note);
  };
  const search = async (query) => {
    abort?.abort();
    abort = new AbortController();
    try {
      const { signal } = abort;
      // An export's Landmarks come with the names; a failed Landmark search leaves the names alone.
      const [body, near] = await Promise.all([
        api(`/api/search?q=${encodeURIComponent(query)}&limit=20`, { signal }),
        state.info.landmark_search
          ? api(`/api/landmarks?q=${encodeURIComponent(query)}`, { signal }).catch((error) => {
            if (error.name === "AbortError") throw error;
            return { results: [] };
          })
          : { results: [] },
      ]);
      show(body.results, near.results, body.results.length || near.results.length ? null : query);
    } catch (error) {
      if (error.name !== "AbortError") setStatus(error.message, true);
    }
  };
  // Enter with no suggestion chosen by the keys: the hike named exactly as typed, or the only hike found;
  // else the list stays open and says so (ADR-0054).
  const decide = () => {
    entered = false;
    const picked = enterPick(input.value, named);
    if (picked) { pick(picked.item.id, `Enter picked ${picked.why}`); return; }
    list.insertAdjacentHTML("afterbegin", `<li class="nothing enter-note" role="presentation">${items.length} hikes match; pick one (or use ↑ ↓, then Enter).</li>`);
  };
  input.addEventListener("input", () => {
    clearTimeout(timer);
    entered = false;
    const query = input.value.trim();
    if (query.length < 2) {
      abort?.abort();
      show([]);
      return;
    }
    timer = setTimeout(() => search(query), 200);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") { event.preventDefault(); move(1); }
    else if (event.key === "ArrowUp") { event.preventDefault(); move(-1); }
    else if (event.key === "Enter") {
      event.preventDefault();
      if (active >= 0) { pick(items[active].id); return; }
      const query = input.value.trim();
      if (query.length < 2) return;
      entered = true;
      if (listed === query) { if (items.length) decide(); else entered = false; } // the list of what is typed is in: decide now
      else { clearTimeout(timer); search(query); } // else once it is
    } else if (event.key === "Escape") show([]);
  });
  // A search still pending would open the list again after the Hiker left the box.
  input.addEventListener("blur", () => {
    clearTimeout(timer);
    abort?.abort();
    setTimeout(() => show([]), 150);
  });
}

let LIKED_EMPTY = ""; // the liked-hike card's words while no hike is chosen, as the page opens

let chosen = 0, shown = 0, ranked = 0, paired = 0, boxed = 0, placed = 0; // the latest of each request; an older response that lands later is dropped
let selecting = 0; // the latest request to fill the hike card, a result or a compared hike: only it fills it
let loading = 0, held = 0; // the hike request in flight (0: none), and the one Find waits for
let ranking = false; // a Ranking runs: Find waits for it

// Find is greyed out while there is nothing to rank or the page is busy; each time, the line beside it
// says why: no hike chosen in its own words, a hike loading or a search running in the status line.
function syncFind() {
  const none = !state.wishing && !state.liked, busy = loading !== 0 && loading === chosen;
  $("#find").disabled = none || ranking || (busy && held === chosen);
  $("#find-why").textContent = none && !busy && !ranking
    ? `Choose a hike first: search for one by name or select one on the map${$("#wish-on").hidden ? "" : ", or describe the one you want"}.`
    : "";
}

// The liked hike measured again for the Return to start ticked now, Find greyed out until it is; when
// it cannot be, Find stays usable and measures it again when pressed.
async function remeasure(liked) {
  const measured = liked.route
    ? chooseComposed(liked.route.text, liked.name_origin === "hiker" ? liked.name : null)
    : choose(liked.id);
  held = chosen;
  syncFind();
  await measured;
}

// ``from``: "link" (the hike an address names; when it cannot be opened, the page shows no hike),
// "map" (clicked on the map, which stays where it is), "search" (the hike's card is then scrolled to)
// or null; ``note``: why Enter picked it, said beside its name.
async function choose(id, { from = null, note = null } = {}) {
  const request = loading = ++chosen;
  setStatus("Loading the hike…");
  syncFind();
  try {
    const hike = await api(`/api/hikes/${id}?return_to_start=${returnToStart()}`);
    if (request !== chosen) return;
    state.liked = hike;
    state.mapAt = null; // the map opens at the hike chosen, not at a place searched before
    if (state.wishing) setWishing(false);
    $("#search").value = hike.name;
    $("#liked-name").textContent = `Chosen: ${hike.name}, ${lengthText(hike)}${note ? ` (${note})` : ""}`;
    $("#liked-name").classList.remove("error-text");
    $("#pair-search").disabled = false;
    $("#return-bar").hidden = false;
    history.replaceState(null, "", `#hike=${id}${modeHash()}`);
    renderDetail($("#liked"), hike, { role: "Liked hike" });
    $("#results").hidden = true;
    $("#selected").hidden = true;
    pick.restyle();
    pick.drawLoops();
    if (from !== "map") pick.follow();
    else pick.hint(`Chosen: ${hike.name}, ${lengthText(hike)}.`); // under the map clicked, not only above it
    dropClearedHint();
    if (from === "search") $("#liked").scrollIntoView({ block: "start" }); // instant: a smooth scroll can be cut short
    setStatus("");
  } catch (error) {
    if (request !== chosen) return;
    if (from === "link" && namesNothing(error)) brokenLink(error); else setStatus(notMeasured(error, request), true);
  } finally {
    if (request === chosen) loading = 0;
    syncFind();
  }
}

// Why a hike was not loaded; for the liked hike measured again, that Find tries again.
const notMeasured = (error, request) => request === held
  ? `Your hike could not be measured again for Return to start: ${(error.reason ?? error.message).replace(/\.$/, "")}. Press Find similar to try again.`
  : error.message;

// The server read the request and found no such hike; not a dropped connection or a busy server.
const namesNothing = (error) => [400, 404, 422].includes(error.status);

// An address names no hike of this data, or is cut short or changed (``malformed``): say so beside the
// search and where the hike would be, and show none, so the page and its address agree.
function brokenLink(error, malformed = false) {
  console.warn("This link opens no hike:", error.message);
  unchoose();
  $("#liked").innerHTML = malformed
    ? `<p class="error-text broken-link"><strong>This link could not be opened: it looks cut short or changed, so it shows no hike.</strong></p>
    <p>Ask for the link again, or search for a hike by name, or select one on the map.</p>`
    : `<p class="error-text broken-link"><strong>This link could not be opened: it doesn't match the current trail data, so it shows no hike.</strong></p>
    <p>The trails may have changed since it was shared. Search for a hike by name, or select one on the map.</p>`;
  $("#liked-name").textContent = "This link could not be opened; no hike chosen yet.";
  $("#liked-name").classList.add("error-text");
}

const CLEARED_HINT = "Cleared: your hike is gone too. Click a junction or a segment to draw a new one, or search for a hike by name.";

// The design bar's "Cleared" hint is about the hike that was cleared: a new one, or none, replaces it.
function dropClearedHint() {
  if (compose.on && $("#map-pick-hint").textContent === CLEARED_HINT) compose.hintNext();
}

// No hike chosen: the page as it opens, with no list.
function unchoose() {
  state.liked = null;
  const card = $("#liked");
  if (card._map) { card._map.remove(); card._map = null; }
  card.classList.add("empty");
  card.innerHTML = LIKED_EMPTY;
  $("#liked-name").textContent = "No hike chosen yet.";
  $("#liked-name").classList.remove("error-text");
  $("#search").value = "";
  $("#pair-search").disabled = true;
  syncFind();
  $("#return-bar").hidden = true;
  $("#results").hidden = $("#selected").hidden = true;
  pick.restyle();
  pick.drawLoops();
  setStatus("");
}

// The Hiker looks for another hike: a broken link's message gives way to the empty card's words.
function forgetBrokenLink() {
  const card = $("#liked");
  if (card.querySelector(".broken-link")) {
    card.innerHTML = LIKED_EMPTY;
    $("#liked-name").textContent = "No hike chosen yet.";
    $("#liked-name").classList.remove("error-text");
  }
}

// ``from``: "link" (an address), "compose" (the composer's Use this hike) or null.
async function chooseComposed(route, name, from = null) {
  const request = loading = ++chosen;
  setStatus("Measuring your hike…");
  syncFind();
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
    $("#liked-name").classList.remove("error-text");
    $("#compose-name").value = hike.name;
    compose.named = hike.name_origin === "hiker";
    compose.source = hike; // the drawing is the liked hike now: Clear clears it
    $("#pair-search").disabled = false;
    $("#return-bar").hidden = false;
    history.replaceState(null, "", routeHash(hike) + modeHash());
    renderDetail($("#liked"), hike, { role: "Your hike" });
    if (compose.on) compose.readout(); // measured now: the same figures as the card
    // Said beside the button pressed: a change that keeps the length and name still shows it was used.
    if (from === "compose") pick.hint(`Your hike of ${hike.route.segments.length} segment${hike.route.segments.length === 1 ? "" : "s"}, as drawn here, is measured and shown below.`);
    $("#results").hidden = true;
    $("#selected").hidden = true;
    pick.restyle();
    pick.drawLoops();
    dropClearedHint();
    setStatus("");
  } catch (error) {
    if (request !== chosen) return;
    if (from === "link" && namesNothing(error)) brokenLink(error, error.status === 422);
    else {
      setStatus(notMeasured(error, request), true);
      if (from === "compose") pick.hint(`Not measured: ${error.message}`); // beside the button pressed
    }
  } finally {
    if (request === chosen) loading = 0;
    syncFind();
  }
}

// A suggested loop or a built hike, drawn on the map to change: the liked hike and the list stay until
// Use this hike measures the drawing and makes it your hike (ADR-0054).
async function editComposed(hike) {
  let drawn = hike;
  if (!hike.route.segments) { // a built hike: its Route as segments, measured without choosing it
    setStatus("Opening it on the map…");
    try {
      drawn = await api("/api/compose", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ route: hike.route.text, name: hike.name, return_to_start: returnToStart() }),
      });
    } catch (error) {
      setStatus(error.message, true);
      return;
    }
    setStatus("");
  }
  $("#map-pick-on").checked = true;
  pick.open();
  if (compose.on) compose.toggle(false);
  compose.start = compose.startKey = null;
  compose.route = [];
  compose.toggle(true, drawn);
  $("#map-pick").scrollIntoView({ block: "start" });
}

// ---------- select on map ----------

// The centre and zoom that show ``hike``'s line on ``map``, at least ``least`` zoomed in (else on its start).
function viewOf(map, hike, least) {
  const bounds = hike.line ? L.latLngBounds(decodePolyline(hike.line)).pad(0.1) : null;
  const zoom = bounds ? map.getBoundsZoom(bounds) : 0;
  return zoom >= least ? [bounds.getCenter(), zoom] : [hike.trailhead, least];
}

// A map of the hikes in view (from PICK_ZOOM on, and only while the view holds
// at most the server's limit); clicking a line or an end chooses that hike: the one
// nearest the pointer, so where hikes meet the line clicked wins, not the one drawn last.
const pick = {
  map: null,
  layer: null,
  lines: new Map(), // hike id -> its polyline
  shown: [], // { hike, line, dots } of every hike drawn
  hovered: null, // the hike id under the pointer
  tip: null, // its tooltip
  loops: null, // the suggested loops and the shown row's versions of the Ranking
  timer: null,
  abort: null,

  open() {
    const card = $("#map-pick");
    card.hidden = false;
    if (!this.map) {
      // Canvas with a tolerance: a tap a few pixels off a thin line still hits it.
      this.map = L.map(card.querySelector(".pick-map"), { renderer: L.canvas({ tolerance: PICK_TOLERANCE_PX }) });
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      }).addTo(this.map);
      this.map.createPane(START_PANE).style.zIndex = 450;
      this.layer = L.layerGroup().addTo(this.map);
      // Where most hikes are, until there is a hike or a starting place to show (ADR-0054).
      const [south, west, north, east] = state.info.core ?? state.info.coverage;
      this.map.fitBounds([[south, west], [north, east]]);
      this.map.on("moveend", () => this.load());
      this.map.on("click", (event) => {
        const near = this.near(event.layerPoint);
        if (near.length === 1) choose(near[0], { from: "map" });
        else if (near.length > 1) this.offer(near, event.latlng);
      });
      this.map.on("mousemove", (event) => this.hover(this.near(event.layerPoint)[0] ?? null, event.latlng));
      this.map.on("mouseout", () => this.hover(null));
      this.drawLoops();
    }
    this.map.invalidateSize();
    if (state.mapAt) this.focus(state.mapAt); else this.follow();
    this.load();
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  },

  // Show a place the Hiker searched (a Landmark, or the start place) close enough to see
  // its hikes as lines: a town's hikes start within 5 km, the others pass within 2 km.
  // With the map closed, it opens there.
  focus(at) {
    state.mapAt = at;
    if (!this.map || $("#map-pick").hidden) return;
    state.mapAt = null;
    this.map.setView(at.center, at.zoom ?? PICK_ZOOM);
  },

  // The map follows the liked hike, or else the starting place: it opens on them, and moves to a hike
  // chosen by name while it is open, zoomed in so the hikes there show.
  follow() {
    if (!this.map || $("#map-pick").hidden || compose.on) return;
    const liked = state.liked, origin = !liked && $("#drive-on").checked ? (parseLatLon($("#origin").value) || state.origin) : null;
    if (liked) this.map.setView(...viewOf(this.map, liked, PICK_ZOOM), { animate: false });
    else if (origin) this.map.setView([origin.lat, origin.lon], PICK_ZOOM, { animate: false });
  },

  // Hikes whose lines run together under a click, as a long tour's stage and a day hike on one trail
  // do: a list to choose from, nearest first, the shorter first where they run together.
  offer(ids, latlng) {
    const box = document.createElement("div");
    box.className = "pick-offer";
    box.innerHTML = `<strong>${ids.length} hikes run here; choose one:</strong>`;
    for (const id of ids) {
      const { hike } = this.shown.find((shown) => shown.hike.id === id);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary";
      button.textContent = `${hike.name} (${hike.multi_day ? multiDayText(hike) : fmt.km(hike.walk_m)})`;
      button.addEventListener("click", () => { this.map.closePopup(); choose(id, { from: "map" }); });
      box.appendChild(button);
    }
    L.popup({ maxWidth: 320 }).setLatLng(latlng).setContent(box).openOn(this.map);
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
      this.hover(null);
      this.layer.clearLayers();
      this.lines.clear();
      this.shown = [];
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
    this.hover(null);
    this.layer.clearLayers();
    this.lines.clear();
    this.shown = [];
    for (const hike of hikes) {
      const latlngs = decodePolyline(hike.line);
      // The map, not each line, takes the pointer (``nearest``).
      const line = L.polyline(latlngs, { ...PICK_LINE, interactive: false });
      // A hike that ends where it starts (a loop) draws its start only: its end dot would cover it.
      const [first, last] = [latlngs[0], latlngs.at(-1)];
      const dots = (L.latLng(first).equals(last) ? [first] : [first, last]).map((at, i) => L.circleMarker(at, {
        radius: 5, color: "#fff", weight: 1.5, fillColor: i === 0 ? "#2f7d4f" : "#1f2a24", fillOpacity: 1, pane: START_PANE,
        interactive: false,
      }));
      L.featureGroup([line, ...dots]).addTo(this.layer);
      this.lines.set(hike.id, line);
      this.shown.push({ hike, line, dots });
    }
    this.restyle();
  },

  // The ids of the hikes a click at a layer point is for, within the tolerance of a tap: where two or more
  // lines run through it (each within the tolerance, away from its own ends), all of them, the shorter
  // first, so every hike on a trail others share can be chosen (ADR-0055); else the nearest. A start or
  // end dot counts from its rim; at a tie (two hikes that meet) the one starting nearer wins.
  near(point) {
    if (compose.on) return [];
    const found = [];
    for (const { hike, line, dots } of this.shown) {
      const distance = (dot) => point.distanceTo(this.map.latLngToLayerPoint(dot.getLatLng())) - dot.getRadius();
      const closest = line.closestLayerPoint(point), along = closest?.distance ?? Infinity;
      const latlngs = line.getLatLngs();
      const inside = closest != null && [latlngs[0], latlngs.at(-1)].every((end) => this.map.latLngToLayerPoint(end).distanceTo(closest) > 1);
      const off = Math.min(along, ...dots.map(distance));
      if (off <= PICK_TOLERANCE_PX) found.push({ id: hike.id, off, along, inside, start: distance(dots[0]), walk: hike.walk_m });
    }
    if (!found.length) return [];
    found.sort((a, b) => a.off - b.off || a.start - b.start);
    const shared = found.filter((hike) => hike.inside && hike.along <= PICK_TOLERANCE_PX);
    if (shared.length < 2) return [found[0].id];
    if (!shared.includes(found[0])) shared.push(found[0]);
    return shared.sort((a, b) => a.walk - b.walk).map((hike) => hike.id);
  },

  // The hike under the pointer stands out, named in a tooltip beside the pointer.
  hover(id, at) {
    if (id !== this.hovered) {
      this.lines.get(this.hovered)?.setStyle(this.style(this.hovered));
      this.hovered = id;
      this.map?.getContainer().classList.toggle("leaflet-interactive", id != null);
      if (id == null) {
        this.tip?.remove();
        return;
      }
      const { hike, line } = this.shown.find((shown) => shown.hike.id === id);
      line.setStyle(PICK_HOVER);
      line.bringToFront();
      this.tip ??= L.tooltip({ direction: "top", offset: [0, -8] });
      this.tip.setContent(`<strong>${escapeHtml(hike.name)}</strong><br>${hike.multi_day ? multiDayText(hike) : fmt.km(hike.walk_m)}`);
    }
    if (id != null && at) this.tip.setLatLng(at).addTo(this.map);
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
        // Its click shows the result, and stays off the map: no hike under it is chosen as well.
        L.polyline(decodePolyline(version.line), { ...(k ? VERSION_LINE : version.route ? LOOP_LINE : RESULT_LINE), bubblingMouseEvents: false })
          .bindTooltip(`<strong>Similar hike #${i + 1}${kind}${label}</strong><br>${escapeHtml(version.name)}<br>${fmt.km(version.length_m)}`, { sticky: true })
          .on("click", () => showResult(i, k))
          .addTo(this.loops);
      });
    });
  },
};

// ---------- design your own from segments ----------

const UNCONNECTED = "That is not connected to your route's end over the trails loaded so far. Pan the map along the trails between them, zoomed in so they load, then click it again; or click the segments in between.";

// The marked trails and graded paths in view as segments, junction to junction (from COMPOSE_ZOOM on).
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
  cannot: null, // why the hike opened to edit cannot be drawn, said until the Hiker starts a drawing
  loaded: 0, // the steps of the hike the drawing opened with: Undo takes back only the Hiker's own clicks
  source: null, // the measured hike the drawing opened with (the liked one, or one edited from the list)
  named: false, // the Hiker typed the name
  segments: new Map(), // every segment loaded so far, by id
  junctions: new Map(),
  dots: [], // the junctions drawn as dots, every one a click can walk to
  shown: [], // segment ids in the last view
  timer: null,
  abort: null,

  // On or off; ``from``: a measured hike with a Route to open the drawing with (else the liked one, when composed).
  toggle(on, from = null) {
    this.on = on;
    this.cannot = null;
    const button = $("#compose-on");
    button.setAttribute("aria-pressed", String(on));
    button.textContent = on ? "Back to whole hikes" : "Design your own from segments";
    $("#compose-tools").hidden = !on;
    $("#compose-readout").hidden = !on;
    if (!on) $("#compose-links").innerHTML = "";
    if (!this.layer) this.layer = L.layerGroup().addTo(pick.map);
    clearTimeout(pick.timer);
    pick.abort?.abort();
    pick.hover(null);
    pick.layer.clearLayers();
    pick.lines.clear();
    pick.shown = [];
    this.layer.clearLayers();
    if (on && from && !from.route.segments) { // its link cuts a Segment of this data: shown, not drawn
      this.cannot = `${from.name} cuts a trail segment of this map's data, so it cannot be drawn here; click a junction near it to draw your own.`;
      pick.map.setView(from.trailhead, COMPOSE_ZOOM);
      pick.load();
      pick.hint(this.cannot);
      return;
    }
    const source = from ?? (state.liked?.route?.segments ? state.liked : null);
    if (on && source && this.start == null) {
      // Go on from that hike (unless its link cuts a Segment of this data), zoomed in so its segments load.
      const { start, segments, steps, text } = source.route;
      this.start = start;
      this.startKey = text.split(":")[0];
      this.route = segments.map((id, at) => ({ id, step: steps[at], auto: false }));
      this.loaded = this.route.length;
      this.source = source;
      this.named = source.name_origin === "hiker";
      $("#compose-name").value = source.name;
      $("#compose-done").disabled = !this.route.length;
      $("#compose-undo").disabled = !this.undoable();
      pick.map.setView(...viewOf(pick.map, source, COMPOSE_ZOOM), { animate: false });
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
      pick.hint("Zoom in on the map to see the trails there as segments.");
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
        else if (!this.shown.length) pick.hint("No trails in view; move the map.");
        else this.hintNext();
      } catch (error) {
        if (error.name !== "AbortError" && request === boxed) pick.hint(error.message);
      }
    }, 250);
  },

  hintNext() {
    if (this.start == null && this.cannot) { pick.hint(this.cannot); return; }
    const kept = this.source && this.source !== state.liked && state.liked
      ? ` Until then Find similar still uses ${state.liked.name}.` : "";
    pick.hint(this.start == null
      ? "Click a junction or a trail segment to start your hike there. Only marked trails and graded paths are segments, and a hike starts and ends at a junction."
      : `Click a segment to walk on it; Undo takes your last click back. Use this hike measures it and makes it your hike.${kept}`);
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
    const parts = [`<strong>${escapeHtml(segment.routes.join(" · ") || "Unnamed trail")}</strong>`, fmt.km(segment.length)];
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
    this.dots = [];
    for (const id of ends) {
      const junction = this.junctions.get(id);
      if (!junction || id === this.start || id === end) continue;
      const marker = L.circleMarker(junction.latlng, { radius: 4, color: "#fff", weight: 1, fillColor: "#1e3a8a", fillOpacity: 0.9 })
        .bindTooltip(this.start == null ? "Start here" : "Junction: click to walk here").addTo(this.layer);
      marker.on("click", () => this.toJunction(id));
      this.dots.push(id);
    }
    const at = (id, fill, text) => {
      const junction = this.junctions.get(id);
      if (!junction) return;
      L.circleMarker(junction.latlng, { radius: 8, color: "#fff", weight: 2, fillColor: fill, fillOpacity: 1 })
        .bindTooltip(text).on("click", () => this.toJunction(id)).addTo(this.layer);
      this.dots.push(id);
    };
    if (this.start != null) at(this.start, "#2f7d4f", this.route.length ? "<strong>Start</strong>: click to walk back here" : "<strong>Start</strong>");
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
    // A line hovered is drawn on top, so it takes a click on a junction's dot: the dot nearest wins.
    line.on("click", (event) => {
      const junction = this.junctionAt(event.latlng);
      if (junction != null) this.toJunction(junction); else this.add(segment, event.latlng);
    });
  },

  // The junction drawn as a dot within JUNCTION_PX of ``latlng``, the nearest; else null.
  junctionAt(latlng) {
    const point = pick.map.latLngToLayerPoint(latlng);
    let nearest = null, best = JUNCTION_PX;
    for (const id of this.dots) {
      const px = point.distanceTo(pick.map.latLngToLayerPoint(this.junctions.get(id).latlng));
      if (px <= best) { nearest = id; best = px; }
    }
    return nearest;
  },

  // A click on a junction: the start, while the hike has none; else the shortest way to it from the route's
  // end over the trail segments loaded so far (to the start: back there), as one click Undo takes back.
  toJunction(id) {
    if (this.start == null) { this.startAt(id); return; }
    const end = this.end();
    if (end == null) return;
    if (id === end) { pick.hint("Your hike ends there so far: click a segment or another junction to go on."); return; }
    const way = this.shortest(end, { a: id, b: id });
    if (!way) { pick.hint(UNCONNECTED); return; }
    let here = end;
    way.forEach((step, i) => {
      this.route.push({ id: step.id, step: here === step.a ? step.stepAB : step.stepBA, auto: i < way.length - 1 });
      here = here === step.a ? step.b : step.a;
    });
    this.changed();
    pick.hint(`Walked ${way.length} segment${way.length === 1 ? "" : "s"} to ${id === this.start ? "the start" : "that junction"}${way.length > 1 ? " (amber, dashed: the way there)" : ""}; Undo takes ${way.length === 1 ? "it" : "them"} back.`);
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
        pick.hint(UNCONNECTED);
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

  // Whether Undo has a click of the Hiker's to take back: a segment walked, or the start of a new drawing.
  undoable() {
    return this.route.length > this.loaded || (!this.loaded && this.start != null);
  },

  // Takes back the last click: its segment and those added to reach it; never a segment of the hike the
  // drawing opened with (Clear starts over instead).
  undo() {
    if (!this.undoable()) {
      pick.hint("Undo takes back only your own clicks: the segments of the hike you opened stay. Clear starts over.");
      return;
    }
    if (this.route.length > this.loaded) {
      this.route.pop();
      while (this.route.length > this.loaded && this.route.at(-1)?.auto) this.route.pop();
      if (!this.route.length) this.named = false;
    } else this.start = this.startKey = null;
    this.changed();
  },

  // Starts over. A drawing of the liked hike clears that hike too, so Find similar never uses a hike no
  // longer drawn; a drawing of a hike from the list leaves the liked hike be.
  clear() {
    const liked = this.source && this.source === state.liked;
    this.start = this.startKey = null;
    this.route = [];
    this.loaded = 0;
    this.source = null;
    this.changed();
    if (liked) {
      chosen++; // drops a hike still loading
      unchoose();
      history.replaceState(null, "", location.pathname + location.search);
      pick.hint(CLEARED_HINT);
    }
  },

  changed() {
    if (this.start == null) this.named = false; // a new hike: its name is proposed again
    if (!this.named) $("#compose-name").value = "";
    this.draw();
    this.hintNext();
  },

  // The route as its link names it.
  text() {
    return `${this.startKey}:${this.route.map((step) => step.step).join(",")}`;
  },

  // Length and an estimate of the climb from the segments; "Use this hike" measures it exactly,
  // with its way back when Return to start is on, and once it is the hike shown below, the readout
  // shows its figures as measured, the same as there. Over MAX_COMPOSED_M it cannot be used.
  readout() {
    const out = $("#compose-readout");
    out.classList.remove("over");
    $("#compose-done").disabled = !this.route.length;
    $("#compose-undo").disabled = !this.undoable();
    if (this.start == null) { out.textContent = "No hike drawn yet."; return; }
    const measured = this.route.length
      ? [state.liked, this.source].find((hike) => hike?.route?.text === this.text() && hike.climb_m != null) ?? null : null;
    const climb = measured
      ? `${upDown(measured.climb_m, measured.descent_m)}${measured.way_back && measured.way_back.kind !== "none" ? " with the way back" : ""}, as measured`
      : null;
    let metres = 0, up = 0, down = 0, here = this.start, reused = false;
    const seen = new Set();
    for (const { id } of this.route) {
      const segment = this.segments.get(id);
      if (!segment) {
        out.textContent = `${this.route.length} segments; ${climb ?? "move the map over them to see the totals."}`;
        return;
      }
      const forward = segment.a === here;
      metres += segment.length;
      up += (forward ? segment.climbAB : segment.climbBA) ?? 0;
      down += (forward ? segment.climbBA : segment.climbAB) ?? 0;
      reused ||= seen.has(id);
      seen.add(id);
      here = forward ? segment.b : segment.a;
    }
    // Ending a few metres from the start is ending there, as the hike below measures it (a round trip).
    const [from, to] = [this.start, here].map((id) => this.junctions.get(id)?.latlng);
    const apart = here === this.start ? 0 : from && to ? metresBetween(from, to) : Infinity;
    const loop = this.route.length && apart <= ROUND_TRIP_M;
    const parts = [
      `${this.route.length} segment${this.route.length === 1 ? "" : "s"}`,
      `${fmt.km(metres)}${loop ? "" : " one way"}`,
      climb ?? `about ↑ ${fmt.m(up)} ↓ ${fmt.m(down)}`,
    ];
    if (loop) parts.push(apart ? `ends ${fmt.m(apart)} from the start` : "back at the start");
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
    chooseComposed(this.text(), this.named ? $("#compose-name").value.trim() : null, "compose");
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
  const request = ++paired, filling = ++selecting, liked = state.liked, uncounted = uncountedReasons();
  setStatus("Scoring the pair…");
  try {
    const [pair, hike] = await Promise.all([
      api("/api/pair", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(query) }),
      api(`/api/hikes/${id}?return_to_start=${query.return_to_start}`),
    ]);
    if (request !== paired || filling !== selecting || liked !== state.liked) return;
    $("#pair-search").value = hike.name;
    document.querySelectorAll("#results-table tr.active").forEach((r) => r.classList.remove("active"));
    const card = $("#selected");
    state.selected = { pair: id };
    renderDetail(card, hike, {
      role: "Compared with the liked hike", compare: liked, result: { ...pair, drive_hours: null },
      query: { weights: pair.weights, month: pair.month, return_to_start: pair.return_to_start, uncounted },
    });
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    setStatus("");
  } catch (error) {
    if (request === paired && filling === selecting && liked === state.liked) setStatus(error.message, true);
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
  const text = $("#origin").value.trim(), request = ++placed;
  if (!text) { $("#origin-found").textContent = "Type a town name or lat, lon first."; return; }
  // The box no longer names the start found before: a failed lookup must not leave it in place.
  state.origin = null;
  state.missed = null;
  const parsed = parseLatLon(text);
  if (parsed) {
    const warning = swappedWarning(parsed);
    if (warning) $("#origin-found").textContent = warning; else setOrigin(parsed, true);
    return;
  }
  if (state.geocoded.has(text)) { setOrigin(state.geocoded.get(text), true); return; }
  $("#origin-found").textContent = "Searching…";
  try {
    let origin;
    if (state.info.place_search) {
      const { results: [place] } = await api(`/api/place?limit=1&q=${encodeURIComponent(text)}`);
      if (place) origin = { lat: place.lat, lon: place.lon, label: `${place.name}, ${place.country}` };
    } else {
      // An export without GeoNames places: one request per click (Nominatim usage policy: no autocomplete, cache results).
      const response = await fetch(`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q=${encodeURIComponent(text)}`);
      const [place] = await response.json();
      if (place) origin = { lat: Number(place.lat), lon: Number(place.lon), label: place.display_name };
    }
    if (request !== placed) return; // the box changed, or another lookup started, meanwhile
    if (!origin) {
      state.missed = text;
      $("#origin-found").textContent = "Place not found; try a town name or lat, lon.";
      return;
    }
    state.geocoded.set(text, origin);
    setOrigin(origin, true);
  } catch {
    if (request === placed) $("#origin-found").textContent = "Place search failed; type lat, lon instead.";
  }
}

// ``found``: found by the Hiker now, so the address and every "Link to this hike" name it (not one an address names).
function setOrigin(origin, found = false) {
  state.origin = origin;
  pick.focus({ center: [origin.lat, origin.lon] });
  $("#origin-found").textContent = `Start: ${origin.label} (${origin.lat.toFixed(4)}, ${origin.lon.toFixed(4)})`;
  markStale();
  if (found) syncAddress();
}

function useMyLocation() {
  if (!navigator.geolocation) { $("#origin-found").textContent = "This browser cannot share a location."; return; }
  const request = ++placed;
  $("#origin-found").textContent = "Asking the browser…";
  navigator.geolocation.getCurrentPosition(
    (position) => {
      if (request !== placed) return;
      const { latitude: lat, longitude: lon } = position.coords;
      $("#origin").value = `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
      setOrigin({ lat, lon, label: "your location" }, true);
    },
    () => { if (request === placed) $("#origin-found").textContent = "Location not shared; type a place or lat, lon."; },
  );
}

// ---------- grouping ----------

// The grouping radius the server takes when the Hiker leaves it empty: a share of the smaller limit,
// a drive hour counted as so many km, at least a floor; with no limit, today's 10 km (ADR-0032).
function groupDefaultKm() {
  const { km, share, min_km: least, kmh } = state.info.grouping;
  const limits = [];
  if ($("#drive-on").checked) {
    if ($("#hours-on").checked && Number($("#hours").value) > 0) limits.push(Number($("#hours").value) * kmh);
    if ($("#km-on").checked && Number($("#km").value) > 0) limits.push(Number($("#km").value));
  }
  return limits.length ? Math.max(least, share * Math.min(...limits)) : km;
}

function showGroupDefault() {
  const { km, share, min_km: least, kmh } = state.info.grouping;
  const now = String(Math.round(groupDefaultKm() * 10) / 10);
  $("#group-km").placeholder = now;
  $("#group-hint").textContent = `A hike starting this near a better one is listed as one of its versions, not in a row of its own. Empty: ${now} km for this search, ${share * 100} % of the limit (a drive hour counts as ${kmh} km), at least ${least} km; ${km} km with no limit.`;
}

// ---------- ranking ----------

const REDRAW_TITLE = "Draw other hikes from the same results, closer matches more often";
const REDRAW_TRIES = 5; // a Redraw draws again at most this many times while page 1 comes out the same

// Gumbel noise of the row named ``key`` in draw ``draw``: a hash of "draw:key", as serving.draw_noise.
function drawNoise(draw, key) {
  let h = 0x811c9dc5;
  for (const byte of new TextEncoder().encode(`${draw}:${key}`)) h = Math.imul(h ^ byte, 0x01000193);
  h ^= h >>> 16;
  h = Math.imul(h, 0x85ebca6b);
  h ^= h >>> 13;
  h = Math.imul(h, 0xc2b2ae35);
  h ^= h >>> 16;
  return -Math.log(-Math.log(((h >>> 0) + 0.5) / 2 ** 32));
}

// The indices of ``count`` rows drawn by score, each next one with probability rising as
// exp(score / t), in their given order: Gumbel-top-k, as serving.drawn (ADR-0047).
function drawn(scores, keys, draw, count, t) {
  const keyed = scores.map((score, i) => [score / t + drawNoise(draw, keys[i]), i]);
  keyed.sort((a, b) => b[0] - a[0] || a[1] - b[1]);
  return keyed.slice(0, count).map(([, i]) => i).sort((a, b) => a - b);
}

// The score a row is drawn by: in a day hike's Ranking a multi-day route's is 1 less, so it is drawn only
// once the day hikes of the pool run out, as the server lists it after them (ADR-0056).
function drawScore(row) {
  return row.score - (state.answer?.day_first && row.multi_day ? 1 : 0);
}

// The rows a page lists of its pool: in Strict order its best, as ranked, else drawn by score with
// the draw number; in score order either way. Drawn rows Strict order would not list are marked.
function pageOf(pool) {
  const count = state.info.results;
  const picked = state.strict || pool.length <= count
    ? pool.slice(0, count).map((_, i) => i)
    : drawn(pool.map(drawScore), pool.map(resultKey), state.draw, count, state.info.draw.t);
  picked.filter((i) => i >= count).forEach((i) => state.picked.add(pool[i]));
  return picked.map((i) => pool[i]);
}

// The request for a Ranking: with the draw number, which draws the loop starts too, unless in Strict order.
function asking({ uncounted, ...query }, draw = state.draw) { // why a Measure is not counted stays on the page
  return state.strict ? query : { ...query, draw };
}

// Page 1 of the shown Ranking again, for a new draw or Strict order: no request, and later pages dropped.
function relist() {
  state.pools = state.pools.slice(0, 1);
  state.picked = new Set();
  state.results = pageOf(state.pools[0]);
  renderResults();
}

// Strict order on or off: the address says so, and a shown Ranking lists page 1 again.
function setStrict(on) {
  state.strict = on;
  $("#strict").checked = on;
  $("#redraw").disabled = on;
  $("#redraw").title = on ? "Off in Strict order: untick it to draw other hikes" : REDRAW_TITLE;
  syncAddress();
  if (state.pools.length) relist();
}

// Page 1 drawn again from the same answer, until it lists another hike, a few times at most.
function redraw() {
  const shown = new Set(pageOf(state.pools[0]).map(resultKey)), was = state.draw;
  for (let tries = 0; tries < REDRAW_TRIES; tries++) {
    state.draw = (state.draw + 1) >>> 0;
    if (pageOf(state.pools[0]).some((row) => !shown.has(resultKey(row)))) {
      relist();
      setStatus("");
      return;
    }
  }
  state.draw = was;
  setStatus("No other close matches here; widen the distance or turn on Strict order.");
}

// What a Ranking is like: the liked hike, or the wish while the Hiker describes the hike they want.
function rankBasis() {
  return state.wishing ? "wish" : state.liked;
}

async function findSimilar() {
  const was = state.liked;
  if (!state.wishing && was && (was.way_back != null) !== returnToStart()) { // its measuring again failed
    await remeasure(was);
    if (state.liked === was) return; // still not measured: the status line says so
  }
  const liked = rankBasis();
  if (!liked) return;
  let query, place = null;
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
    query.uncounted = uncountedReasons();
    if ($("#drive-on").checked) {
      const typed = parseLatLon($("#origin").value);
      const warning = typed && swappedWarning(typed);
      if (warning) throw new Error(warning);
      const origin = typed || state.origin;
      if (!origin) {
        throw new Error(state.missed ? `No start: ${state.missed} was not found.` : "Set a starting place.");
      }
      const byHours = $("#hours-on").checked, byKm = $("#km-on").checked;
      if (!byHours && !byKm) throw new Error("Tick Max drive hours, Max km or both.");
      query.origin = { lat: origin.lat, lon: origin.lon };
      place = typed ? typed.label : origin.label;
      if (byHours) {
        const hours = Number($("#hours").value);
        if (!(hours >= 0.5 && hours <= state.info.max_drive_hours)) {
          throw new Error(`Max hours is from 0.5 to ${state.info.max_drive_hours}.`);
        }
        query.max_hours = hours;
      }
      if (byKm) {
        const km = Number($("#km").value);
        if (!(km >= 1 && km <= state.info.max_km)) throw new Error(`Max km is from 1 to ${state.info.max_km}.`);
        query.max_km = km;
      }
    }
    const group = $("#group-km").value.trim();
    if (group !== "") { // empty: the server groups by the limits (ADR-0032)
      const km = Number(group);
      if (!(km >= 0.1 && km <= 50)) throw new Error("Group hikes starting within 0.1 to 50 km.");
      query.group_km = km;
    }
  } catch (error) {
    setStatus(error.message, true);
    // Beside the list it leaves, which is no answer to this search.
    if (!$("#results").hidden) {
      markStale(false);
      $("#results-stale").textContent = `This search was not run: ${error.message.replace(/\.$/, "")}. The list below is from before.`;
      $("#results-stale").classList.add("error");
    }
    return;
  }
  // A GoatCounter event when the server set analytics up; without them there is no goatcounter.
  window.goatcounter?.count?.({ path: "find", event: true });
  await rank(query, liked, false, crypto.getRandomValues(new Uint32Array(1))[0], place);
}

// POST /api/rank with this tab's name. While the server still runs this tab's previous Ranking (one the
// page no longer waits for), it asks again every RANK_RETRY_MS, saying so, for up to TIMEOUT_S, unless
// ``current()`` says a later request took its place (ADR-0054).
async function postRank(request, current = () => true) {
  const until = Date.now() + TIMEOUT_S * 1000;
  for (;;) {
    try {
      return await api("/api/rank", {
        method: "POST",
        headers: { "Content-Type": "application/json", [TAB_HEADER]: TAB },
        body: JSON.stringify(request),
      });
    } catch (error) {
      if (error.status !== 409 || Date.now() > until || !current()) throw error;
      setStatus("Waiting for your earlier search to end…");
      await new Promise((resolve) => setTimeout(resolve, RANK_RETRY_MS));
      if (!current()) throw error;
    }
  }
}

// Ask for a Ranking and show it, unless a later one was asked or the liked hike changed meanwhile;
// ``stale``: the query on the left is no longer this one; ``draw``: the draw number of its rows;
// ``place``: the name of the starting place the limits are from; ``rows``: list as many rows as this,
// with "Show 10 more" asked for the rows past the first page (Suggest loops keeps the rows shown).
async function rank(query, liked, stale = false, draw = state.draw, place = state.asked?.place, rows = 0) {
  const request = ++ranked;
  ranking = true;
  $("#suggest-loops").disabled = true;
  syncFind();
  if (query.loops) $("#suggest-loops").textContent = "Suggesting loops…";
  setStatus(query.loops ? "Suggesting loops near these hikes…"
    : query.max_hours ? "Finding hikes within the drive, then ranking…" : "Ranking every hike…");
  try {
    const body = await postRank(asking(query, draw), () => request === ranked && liked === rankBasis());
    if (request !== ranked || liked !== rankBasis()) return;
    state.query = {
      weights: body.weights, month: body.month, drive: Boolean(query.max_hours), km: Boolean(query.max_km),
      return_to_start: body.return_to_start, wish: query.wish ?? null, group_km: body.group_km, uncounted: query.uncounted,
    };
    // ``address``: what it was asked as, so an address typed or gone back to that asks otherwise marks it.
    state.asked = { query, basis: liked, place, address: shownAddress() };
    state.answer = body;
    state.draw = draw;
    state.pools = [body.results];
    relist();
    // Instant: a smooth scroll can be cut short, and the list may be screens below the button.
    $("#results").scrollIntoView({ block: "start" });
    setStatus(`Done in ${(body.elapsed_ms / 1000).toFixed(1)} s.`);
    if (stale) markStale();
    while (state.results.length < rows && !$("#show-more").hidden && request === ranked) {
      const shown = state.results.length;
      await showMore();
      if (state.results.length === shown) break;
    }
  } catch (error) {
    if (request !== ranked || liked !== rankBasis()) return;
    if (!query.loops || stale) markStale(false);
    sayFailed($("#status"), error, () => rank(query, liked, stale, draw, place, rows));
    // Beside the list it leaves, which is no answer to this search; a list left under "still running"
    // would read as this search's, so it goes.
    const failed = $("#results-stale");
    if (!$("#results").hidden) {
      failed.textContent = error.status === 409
        ? `This search did not run: ${error.message.replace(/\.$/, "")}. Press ${$("#find").textContent} again in a moment.`
        : `This search failed: ${error.reason ?? error.message.replace(/\.$/, "")}. The list below is from before; press ${$("#find").textContent} to try again.`;
      failed.classList.add("error");
      failed.hidden = false;
      $("#results").classList.toggle("failed", error.status === 409);
    }
  } finally {
    if (request === ranked) ranking = $("#suggest-loops").disabled = false;
    syncFind();
    $("#suggest-loops").textContent = SUGGEST_LOOPS;
  }
}

const STALE_NOTE = "The query changed since this ranking; press Find again to update it.";
// The same, under the Find button, where a phone's Hiker is when they change a setting (ADR-0055).
const FIND_STALE = (button) => `Your settings changed since the list below: press ${button} again to update it.`;
const SUGGEST_LOOPS = "Suggest loops near these hikes (takes a few seconds)";

// The shown results no longer match the query on the left; ``say``: under the Find button too (not when
// it says why a search did not run).
function markStale(say = true) {
  if ($("#results").hidden) return;
  $("#results").classList.add("stale");
  $("#results-stale").textContent = STALE_NOTE;
  $("#results-stale").classList.remove("error");
  $("#results-stale").hidden = false;
  if (say) setStatus(FIND_STALE($("#find").textContent));
}

// The shown Ranking's table, of its first answer and the rows shown.
function renderResults() {
  const body = state.answer, pool = state.pools[0], shown = state.results;
  const section = $("#results");
  section.hidden = false;
  section.classList.remove("stale", "failed");
  $("#results-stale").hidden = true;
  $("#selected").hidden = true;
  // The limits named with their starting place: a limit kept from an earlier search is plain to see.
  const limits = [body.max_hours && `a ${body.max_hours} h drive`, body.max_km && `${body.max_km} km`].filter(Boolean).join(" and ");
  const considered = body.reachable == null
    ? `${state.info.dayhikes.toLocaleString("en")} hikes`
    : `${body.reachable.toLocaleString("en")} hikes within ${limits} of ${state.asked.place}`;
  const mode = body.return_to_start ? "each with its way back to the start" : "each one way";
  const composed = shown.some((result) => [result, ...result.versions].some((row) => row.route))
    ? " Rows marked composed are loops drawn from trail segments, not catalog hikes; with Select hike on map ticked, that map shows them dashed green."
    : "";
  const searched = body.loops
    ? " Loops are searched from a limited number of starting points near the most similar hikes, so they are not every loop there is and can change when the catalog is rebuilt."
    : "";
  const versions = shown.some((result) => result.versions.length)
    ? ` Hikes starting within ${body.group_km} km of a better one are its versions: click a row with versions to list the others (var 2, 3) under it and see each on the map.`
    : "";
  $("#wish-card").hidden = true;
  $("#results-title").textContent = state.query.wish ? "Hikes closest to the one you want" : "Most similar hikes";
  const picked = !state.strict && pool.length > shown.length
    ? ` These ${shown.length} are picked from the best ${pool.length} areas, closer matches more often: Redraw for others, or tick Strict order for the exact ranking.`
    : "";
  const tied = pool.filter((result) => result.score === pool[0]?.score).length;
  const ties = tied > 1 ? ` The best ${tied} areas match equally well.` : "";
  $("#results-note").textContent = shown.length
    ? `${state.query.wish ? "Closest to the hike you want" : `Like “${state.liked.name}”`}, out of ${considered}${body.loops ? " and loops composed near the best of them" : ""}, ${mode}.${picked}${ties} Click a row to see it.${composed}${searched}${versions}`
    : `No hike matched, out of ${considered}. Try a longer drive, a longer distance or other weights.`;

  showFilters();
  $("#results-draw").hidden = !shown.length;
  $("#results-loops").hidden = !state.info.compose || body.loops; // SuggestedLoops on request (ADR-0028)
  const table = $("#results-table");
  const measureHeads = comparedMeasures(body.return_to_start).map((m) =>
    `<th class="measure${body.weights[m.name] ? "" : " excluded"}" title="${escapeHtml(`${m.label}: ${m.description}`)}">${escapeHtml(m.label)} ${helpButton(m.name)}</th>`).join("");
  const figures = body.return_to_start ? "with the way back" : "one way";
  table.innerHTML = `<thead><tr><th>#</th><th class="name">Hike</th><th>Score ${helpButton("overall")}</th>${state.query.drive ? "<th>Drive</th>" : ""}${state.query.km ? '<th title="Straight line from the starting place">Distance</th>' : ""}<th title="Length, ${figures}">Length${body.return_to_start ? " with way back" : ""}</th><th title="Climb and descent, ${figures}">Up / down${body.return_to_start ? " with way back" : ""}</th>${measureHeads}</tr></thead>`;
  state.expanded = null;
  table.appendChild(document.createElement("tbody"));
  listRows(0);
  showMoreButton(body);
  showSwipeCue();
}

// Under a table wider than its box: a cue that it scrolls sideways, to every measure's score.
function showSwipeCue() {
  const wrap = $("#results .table-wrap");
  $("#results-swipe").hidden = $("#results").hidden || wrap.scrollWidth <= wrap.clientWidth + 1;
}

// The filters the shown list was found with, as chips above it: the × of each drops it and finds again.
function showFilters() {
  const body = state.answer, { query, place } = state.asked;
  const chips = [];
  if (body.max_hours) chips.push([`Within a ${body.max_hours} h drive of ${place}`, () => dropLimit("#hours-on")]);
  if (body.max_km) chips.push([`Within ${body.max_km} km of ${place}`, () => dropLimit("#km-on")]);
  if (body.month) {
    chips.push([`Weather in ${MONTHS[body.month - 1]}`, () => {
      $("#month").value = "";
      $("#month").dispatchEvent(new Event("change"));
      findSimilar();
    }]);
  }
  if (!body.return_to_start) {
    chips.push(["One way, without the way back", () => {
      $("#return-on").checked = true;
      $("#return-on").dispatchEvent(new Event("change")); // measures the hike again and finds again
    }]);
  }
  if (query.group_km != null) {
    chips.push([`Grouped within ${query.group_km} km`, () => {
      $("#group-km").value = "";
      showGroupDefault();
      findSimilar();
    }]);
  }
  const list = $("#results-filters");
  list.innerHTML = "";
  for (const [text, drop] of chips) {
    const chip = document.createElement("li");
    chip.innerHTML = `<span>${escapeHtml(text)}</span><button type="button" class="chip-clear" title="Clear this filter and find again" aria-label="Clear ${escapeHtml(text)}">×</button>`;
    chip.querySelector("button").addEventListener("click", drop);
    list.appendChild(chip);
  }
  list.hidden = !chips.length;
}

// Unticks a limit from the starting place, and the starting place with its last limit; then finds again.
function dropLimit(box) {
  $(box).checked = false;
  if (!$("#hours-on").checked && !$("#km-on").checked) {
    $("#drive-on").checked = false;
    $("#drive").hidden = true;
  }
  showGroupDefault();
  findSimilar();
}

// What the loop search did, of the loops in the rows shown, with their versions: which rows they are,
// counted as rows, or, with none listed, how the best loop found scores against the rows shown.
function noteLoops() {
  const search = state.answer.loop_search;
  const loops = state.results.map((result) => [result, ...result.versions].filter((row) => row.route).length);
  const listed = loops.reduce((sum, n) => sum + n, 0);
  const rows = loops.flatMap((n, i) => (n ? [`#${i + 1}`] : []));
  const others = state.query.wish ? "a listed hike" : "a listed hike or the hike you like";
  const last = state.results.at(-1);
  const best = search?.best?.toFixed(3), lastScore = last?.score.toFixed(3);
  const versus = Number(best) < Number(lastScore) ? "below" : best === lastScore ? "level with" : "above";
  const unshown = (state.pools[0] ?? [])
    .filter((row) => !state.results.includes(row)).flatMap((row) => [row, ...row.versions]).filter((row) => row.route);
  const drawn = !state.strict && unshown.length > 0;
  const why = versus === "below" ? "" : unshown.some((row) => row.score.toFixed(3) === best)
    ? state.strict ? `; it ranks just below #${state.results.length}` : "; it is not drawn on this page: Redraw or tick Strict order"
    : `; it ranks after the hikes it ties with, or walks mostly the same trails as ${others}`;
  const count = listed === rows.length ? `${listed} listed` : `${listed} listed in ${rows.length} row${rows.length === 1 ? "" : "s"}`;
  const where = listed
    ? `, ${count}: ${rows.join(", ")} (marked composed)${search?.found > listed ? `; the others rank lower, ${drawn ? "are not drawn on this page (Redraw or tick Strict order), " : ""}or walk mostly the same trails as ${others}` : ""}`
    : search?.found
      ? `, none listed${best != null && last ? `: the best scores ${best}, ${versus} #${state.results.length}'s ${lastScore}${why}` : ""}`
      : "";
  $("#results-loops-note").textContent = search && state.results.length
    ? ` Loops: searched from ${search.starts} start${search.starts === 1 ? "" : "s"} near the best hikes, ${search.found ? `${search.found} found${where}` : "no loop found, so none is listed"}.${search.stopped ? " The search stopped at its time limit, so it may have missed some loops." : ""}`
    : "";
}

// Rows for the results from ``from`` on, at the end of the table.
function listRows(from) {
  const tbody = $("#results-table tbody");
  state.results.slice(from).forEach((result, k) => {
    const i = from + k;
    const row = resultRow(result, i + 1, () => showResult(i));
    row.dataset.index = i;
    tbody.appendChild(row);
  });
  noteLoops();
  noteDraw();
  notePoor();
  pick.drawLoops();
}

// Rows scoring below what a typical pair of random hikes scores are weak matches: a note above the list
// says so, of every row when even the best is (ADR-0054).
function notePoor() {
  const typical = state.info.typical_pair, rows = state.results, body = state.answer;
  const weak = rows.findIndex((row) => row.score < typical);
  const note = $("#results-poor");
  note.hidden = typical == null || weak < 0;
  if (note.hidden) return;
  const like = state.query.wish ? "what you want" : "this hike";
  const limits = [body.max_hours && `a ${body.max_hours} h drive`, body.max_km && `${body.max_km} km`].filter(Boolean).join(" and ");
  const near = limits ? `within ${limits} of ${state.asked.place}` : "";
  note.textContent = weak === 0
    ? `Weak matches: the best of these scores ${rows[0].score.toFixed(3)}, below the ${typical} of a typical pair of random hikes, so ${near
      ? `nothing ${near} is much like ${like}. Try a longer drive or distance, or other weights.`
      : `no hike is much like ${like} under these weights. Try other weights.`}`
    : `From #${weak + 1} down, these score below the ${typical} of a typical pair of random hikes: weak matches${near ? `, as little else ${near} is much like ${like}` : ""}.`;
}

// Beside Redraw: what the "picked" mark means, of the rows shown, or why Redraw is off.
function noteDraw() {
  const { results: count, draw: { pool } } = state.info;
  $("#results-draw-note").textContent = state.strict
    ? "Redraw is off while Strict order is ticked: the list is the exact ranking."
    : state.results.some((result) => state.picked.has(result))
      ? `picked: not one of this page's ${count} best areas, drawn in for variety from its best ${pool}. Strict order lists the ${count} best in order.`
      : "";
}

// "Show 10 more" while the last answer holds rows not shown and the table may grow,
// beside how many hikes start in other areas than the rows shown: of the last answer, also
// every hike in the areas of its rows not shown.
function showMoreButton(answer) {
  const pool = state.pools.at(-1), shown = pageOf(pool);
  const otherAreas = answer.other_areas
    + pool.filter((row) => !shown.includes(row)).reduce((sum, row) => sum + (row.in_area ?? 0), 0);
  $("#show-more").textContent = `Show ${state.info.results} more`;
  $("#show-more").hidden = pool.length <= shown.length || state.results.length >= state.info.max_rows;
  $("#other-areas").textContent = otherAreas
    ? `${otherAreas.toLocaleString("en")} more hike${otherAreas === 1 ? " starts" : "s start"} in other areas.`
    : "";
  $("#results-more").hidden = $("#show-more").hidden && !otherAreas;
}

// The next rows of the shown Ranking: its query again, leaving out the areas of the rows shown (ADR-0032).
// Loops are searched for the first rows only, so no later row's loop outscores the rows above (ADR-0054).
async function showMore() {
  const { asked, results } = state, status = $("#more-status");
  $("#show-more").disabled = true;
  status.classList.remove("error");
  status.textContent = "Finding the next hikes…";
  try {
    const after = results.map((result) => ({ lat: result.trailhead[0], lon: result.trailhead[1] }));
    const body = await postRank(asking({ ...asked.query, loops: false, after }), () => asked === state.asked && results === state.results);
    if (asked !== state.asked || results !== state.results) return;
    state.pools.push(body.results);
    results.push(...pageOf(body.results));
    listRows(after.length);
    showMoreButton(body);
    showSwipeCue();
    status.textContent = body.results.length ? `Done in ${(body.elapsed_ms / 1000).toFixed(1)} s.` : "No more hikes in other areas.";
  } catch (error) {
    if (asked === state.asked && results === state.results) sayFailed(status, error, showMore);
  } finally {
    $("#show-more").disabled = false;
  }
}

// One row of the results table: the hike's figures and every Measure's score.
function resultRow(result, number, open) {
  const { weights, drive, km, return_to_start: back, wish } = state.query;
  const row = document.createElement("tr");
  row.tabIndex = 0;
  const cells = comparedMeasures(back).map((m) => {
    const { score, note } = result.measures[m.name];
    const missing = wish && wish[m.name] == null ? "not in your wish" : "no data";
    if (score == null) return `<td class="cell none" title="${escapeHtml(note ?? missing)}">–</td>`;
    if (!weights[m.name]) {
      return `<td class="cell left-out" title="${escapeHtml(m.label)}: ${score.toFixed(2)}, left out of this ranking">${Math.round(score * 100)}</td>`;
    }
    return `<td class="cell" style="background:${scoreColor(score)}" title="${escapeHtml(m.label)}: ${score.toFixed(2)}">${Math.round(score * 100)}</td>`;
  }).join("");
  // A row's composed version is marked on the row too, as its versions are listed only once it is clicked.
  const composedVar = result.route ? -1 : (result.versions ?? []).findIndex((version) => version.route);
  const tag = (result.route ? '<span class="composed-tag" title="A loop drawn from trail segments, not a catalog hike">composed</span> '
    : composedVar >= 0 ? `<span class="composed-var" title="Its var ${composedVar + 2} is a loop drawn from trail segments, not a catalog hike: click the row to see it">composed var ${composedVar + 2}</span> ` : "")
    + (result.multi_day ? `<span class="multi-day-tag" title="A route of several days${state.answer?.day_first ? ", listed after the day hikes" : ""}">${multiDayText(result)}</span> ` : "");
  const versions = result.versions?.length
    ? ` <span class="versions" title="Click to see each on the map and in this table">${result.versions.length + 1} versions</span>`
    : "";
  const more = result.more_here
    ? `${versions ? " ·" : ""} <span class="versions more-here" title="${escapeHtml(moreHereNote(result.more_here))}">${result.more_here} more good match${result.more_here === 1 ? " starts" : "es start"} here</span>`
    : "";
  const picked = state.picked.has(result)
    ? ' <span class="picked" title="Not among the best of its ten: drawn in for variety. Strict order lists the best in order instead">picked</span>'
    : "";
  row.innerHTML = `<td><span class="number">${number}</span>${picked}</td><td class="name" title="${escapeHtml(result.name)}"><span class="hike-name">${tag}${escapeHtml(result.name)}</span>${versions || more ? `<span class="badge-line">${versions}${more}</span>` : ""}</td>
    <td class="score">${result.score.toFixed(3)}</td>
    ${drive ? `<td>${result.drive_hours.toFixed(1)} h</td>` : ""}
    ${km ? `<td>${result.distance_km.toFixed(1)} km</td>` : ""}
    <td class="figure">${result.length_m != null ? fmt.km(result.length_m) : "–"}</td>
    <td class="figure">${result.climb_m != null ? upDown(result.climb_m, result.descent_m) : "–"}</td>${cells}`;
  row.addEventListener("click", open);
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); }
  });
  return row;
}

// Why a row's good matches starting in its area are not listed (ADR-0046).
function moreHereNote(count) {
  return `Only the best hike of each area (hikes starting within ${state.query.group_km} km of it) is listed, with up to 2 versions that walk mostly other trails. ${count === 1 ? "This one scores" : `These ${count} score`} within 0.05 of it but ${count === 1 ? "is" : "are"} not listed, and later pages do not show ${count === 1 ? "it" : "them"}.`;
}

// The versions of a row, catalog or composed, on the map, and under it as rows var 2 and 3:
// the row itself is var 1.
function expandVersions(index) {
  document.querySelectorAll("#results-table tr.version").forEach((row) => row.remove());
  document.querySelectorAll("#results-table tr[data-version]").forEach((row) => {
    delete row.dataset.version;
    row.querySelector(".number").textContent = Number(row.dataset.index) + 1;
  });
  state.expanded = index;
  const result = state.results[index];
  let after = document.querySelector(`#results-table tr[data-index="${index}"]`);
  after.dataset.version = `${index}-0`;
  after.querySelector(".number").textContent = `${index + 1}, var 1`;
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
  const request = ++shown, filling = ++selecting;
  setStatus("Loading the hike…");
  try {
    const hike = result.route
      ? await api("/api/compose", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ route: result.route.text, name: result.name, return_to_start: state.query.return_to_start }),
      })
      : await api(`/api/hikes/${result.id}?return_to_start=${state.query.return_to_start}`);
    if (request !== shown || filling !== selecting || results !== state.results || liked !== rankBasis()) return;
    const card = $("#selected");
    state.selected = { key: resultKey(result) };
    const which = versions ? `, var ${version + 1} of ${versions.length}` : "";
    const role = `${state.query.wish ? "Hike" : "Similar hike"} #${index + 1}${result.route ? ", composed from trail segments" : ""}${which}`;
    renderDetail(card, hike, { role, compare: state.query.wish ? { wish: state.query.wish } : state.liked, result });
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    setStatus("");
  } catch (error) {
    if (request === shown && filling === selecting && results === state.results && liked === rankBasis()) setStatus(error.message, true);
  }
}

// ---------- start ----------

// What to tell the hiker when the server did not answer the page: paused, its own message, paused or busy, or down.
// Cloud Run with its public access closed answers 403. A page on another origin cannot read an
// answer without CORS headers (that 403, but also a 429 or 503 from Cloud Run's front end): there
// an opaque answer to a no-cors request says only that something is up.
async function notAnswering(error) {
  if (error.status === 401 || error.status === 403) {
    return "HikesAlike is paused for now: its owner has switched the server off. Please try again later.";
  }
  if (error.status) return error.message;
  try {
    await fetch(`${API_BASE}/api/health`, { mode: "no-cors", cache: "no-store" });
    return "HikesAlike is paused or busy. Please try again later.";
  } catch {
    return "The server is not answering. Please try again in a few minutes.";
  }
}

async function start() {
  for (let m = 1; m <= 12; m++) $("#month").add(new Option(MONTHS[m - 1], String(m)));
  // The weights fold away on a narrow screen, also when the window narrows.
  const narrow = window.matchMedia("(max-width: 900px)");
  $("#weights-panel").open = !narrow.matches;
  narrow.addEventListener("change", () => { $("#weights-panel").open = !narrow.matches; });
  const loading = $("#loading-text");
  const waking = setTimeout(() => {
    loading.textContent = "Waking the server… It sleeps while no one is using it, so the first visit after a quiet spell can take up to about 20 seconds.";
  }, WAKE_AFTER_MS);
  try {
    const [info, measures] = await Promise.all([api("/api/info"), api("/api/measures")]);
    state.info = info;
    state.groups = measures.groups;
    state.measures = measures.measures;
    state.scoreHelp = measures.score_help;
  } catch (error) {
    clearTimeout(waking);
    loading.textContent = await notAnswering(error);
    loading.classList.add("error");
    $("#loading-retry").hidden = false;
    return;
  }
  clearTimeout(waking);
  $("#loading").hidden = true;
  $("#count").textContent = state.info.dayhikes.toLocaleString("en");
  $("#results-scores").innerHTML = `<strong>Score:</strong> ${escapeHtml(state.scoreHelp.overall)}
    <strong>Measure cells:</strong> ${escapeHtml(state.scoreHelp.measure)} ${helpButton("measures")}`;
  scoreHelp.listen();
  buildWeights();
  $("#wish-on").hidden = !state.measures.some(wishable);
  syncFind();
  $("#wish-on").addEventListener("click", () => {
    chosen++; // drops a hike still loading
    setWishing(true);
  });
  $("#wish-off").addEventListener("click", () => setWishing(false));
  if (!state.info.drive_time) { // the distance needs no drive-time service
    $("#hours-on").checked = false;
    $("#hours-on").disabled = $("#hours").disabled = true;
    $("#drive-hint").hidden = true;
    $("#drive-off").hidden = false;
    $("#km-on").checked = true;
  }
  showGroupDefault();
  for (const id of ["#hours", "#km", "#hours-on", "#km-on"]) OPENED[id] = { value: $(id).value, checked: $(id).checked };
  LIKED_EMPTY = $("#liked").innerHTML;
  nameSearch($("#search"), $("#suggestions"), (id, note) => choose(id, { from: "search", note }), (landmark, open) => {
    const town = landmark.kind === "town" || landmark.kind === "village";
    pick.focus({ center: [landmark.lat, landmark.lon], zoom: town ? PICK_ZOOM : PICK_ZOOM + 1 });
    if (open && !$("#map-pick-on").checked) {
      $("#map-pick-on").checked = true;
      pick.open();
    }
  });
  if (state.info.landmark_search) $("#search").placeholder = "Type a hike name, peak or place, e.g. Triglav";
  $("#search").addEventListener("input", forgetBrokenLink);
  $("#map-pick-on").addEventListener("change", (event) => {
    forgetBrokenLink();
    if (event.target.checked) pick.open(); else pick.close();
  });
  $("#compose-bar").hidden = !state.info.compose;
  $("#compose-on").addEventListener("click", () => compose.toggle(!compose.on));
  $("#compose-undo").addEventListener("click", () => compose.undo());
  $("#compose-clear").addEventListener("click", () => compose.clear());
  $("#compose-done").addEventListener("click", () => compose.done());
  $("#compose-name").addEventListener("input", (event) => { compose.named = event.target.value.trim() !== ""; });
  nameSearch($("#pair-search"), $("#pair-suggestions"), (id) => comparePair(id));
  $("#reset-weights").addEventListener("click", () => {
    buildWeights();
    if (state.wishing) applyWish(""); // the default weights, each asking its shown default value
    markStale();
    syncAddress();
  });
  for (const host of [$("#weights"), $("#advanced-weights")]) {
    host.addEventListener("input", markStale);
    host.addEventListener("change", markStale);
    // The address keeps the weights (and a wish) as they are set, so a reload or a shared link opens them again.
    host.addEventListener("input", syncAddress);
    host.addEventListener("change", syncAddress);
  }
  $("#advanced").addEventListener("toggle", () => {
    const counts = [...$("#advanced-weights").querySelectorAll(".weight")].some(
      (row) => !leftOut(row) && Number(row.querySelector("input:not(.wish)").value) > 0,
    );
    if (counts) markStale();
    syncAddress();
  });
  $("#drive-on").addEventListener("change", (event) => { $("#drive").hidden = !event.target.checked; markStale(); });
  $("#origin").addEventListener("input", () => { placed++; state.origin = null; state.missed = null; $("#origin-found").textContent = ""; markStale(); });
  for (const limit of ["#drive-on", "#hours-on", "#hours", "#km-on", "#km"]) {
    $(limit).addEventListener("input", markStale);
    $(limit).addEventListener("input", showGroupDefault);
    $(limit).addEventListener("change", syncAddress);
  }
  $("#origin").addEventListener("change", syncAddress);
  $("#group-km").addEventListener("input", markStale);
  $("#group-km").addEventListener("change", syncAddress);
  window.addEventListener("resize", showSwipeCue);
  $("#origin").addEventListener("keydown", (event) => { if (event.key === "Enter") findPlace(); });
  $("#origin-find").addEventListener("click", findPlace);
  $("#origin-here").addEventListener("click", useMyLocation);
  $("#month").addEventListener("change", () => {
    if (state.liked && !state.wishing) renderDetail($("#liked"), state.liked, { role: state.liked.route ? "Your hike" : "Liked hike" });
    markStale();
    syncAddress();
  });
  $("#find").addEventListener("click", findSimilar);
  $("#show-more").addEventListener("click", showMore);
  $("#redraw").addEventListener("click", redraw);
  $("#strict").addEventListener("change", (event) => setStrict(event.target.checked));
  // The shown Ranking again, with loops near its best hikes, though the query on the left changed since.
  $("#suggest-loops").addEventListener("click", () => rank(
    { ...state.asked.query, loops: true }, state.asked.basis, $("#results").classList.contains("stale"),
    state.draw, state.asked.place, state.results.length,
  ));
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
  // An address opens its hike, composed hike or wish with the search settings it carries; a part the
  // page does not know is left alone, and a link cut short or changed says it could not be opened.
  // An address typed or gone back or forward to (``changed``, a hashchange) that names no hike is the
  // home page as it opens, with no hike and no list; one whose settings differ from the list shown marks
  // the list as from before (ADR-0055).
  const openLinked = (changed) => {
    const parts = readLink(location.hash);
    const kind = ["hike", "compose", "wish"].find((name) => parts.has(name));
    if (!kind) {
      if (!changed) return;
      chosen++; // drops a hike still loading
      unchoose(); // first: leaving a wish then keeps the address the home page's
      dropClearedHint();
      if (state.wishing) setWishing(false);
      resetSettings();
      $("#return-on").checked = true;
      if (state.strict) setStrict(false);
      showGroupDefault();
      syncRoundTrip();
      return;
    }
    const oneWay = parts.has("oneway"), modeChanged = returnToStart() === oneWay;
    $("#return-on").checked = !oneWay;
    syncRoundTrip();
    if (parts.has("strict") !== state.strict) setStrict(parts.has("strict"));
    applySettings(parts, kind !== "wish");
    const broken = (malformed) => {
      chosen++; // drops a hike still loading
      brokenLink(new Error(`not a hike link: ${location.hash}`), malformed);
    };
    if (kind === "hike") {
      const id = (decoded(parts.get("hike")) ?? "").trim();
      if (!/^\d{1,17}$/.test(id)) broken(true);
      else if (modeChanged || Number(id) !== state.liked?.id) choose(Number(id), { from: "link" });
    } else if (kind === "compose") {
      const route = parts.get("compose"), name = parts.has("name") ? decoded(parts.get("name")) : null;
      if (!state.info.compose) return;
      if (!/^[\w:.,-]+$/.test(route)) broken(true);
      else if (modeChanged || state.liked?.route?.text !== route) {
        chooseComposed(route, name?.slice(0, NAME_LENGTH), "link"); // a hand-made long name is cut, not refused
      }
    } else if (state.measures.some(wishable)) {
      chosen++; // drops a hike still loading
      setWishing(true);
      applyWish(/^[\w:.,-]*$/.test(parts.get("wish")) ? parts.get("wish") : "");
      history.replaceState(null, "", wishHash());
    }
    refreshHikeLinks();
    if (changed && !$("#results").hidden && state.asked?.address !== shownAddress()) markStale();
  };
  window.addEventListener("hashchange", () => openLinked(true));
  openLinked(false);
}

start();
