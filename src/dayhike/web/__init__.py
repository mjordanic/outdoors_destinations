"""The public read-only web demo: a JSON API over a serving export, and the page.

``create_app`` serves ``ServingData`` (``dayhike.serving``): name search, place
search when the export has GeoNames places (ADR-0029), the hikes in a map view,
hike detail, the Measure list, the score of any two hikes, and Ranking with an
optional highways-only DriveTime pre-filter and a straight-line distance limit, ten rows at a time (ADR-0032). Every client gets a request budget per minute, and one Ranking
runs at a time per instance, so a small container holds its memory. When the export has a TrailGraph,
it also serves the Segments in a map view and the detail of a ComposedHike, which can then be the
Seed of a Ranking or of a pair, and a Ranking lists SuggestedLoops beside the DayHikes (ADR-0020).
A Ranking may start from a Wish instead of a Seed, and the Measure list says how each Measure takes
a Wish value (ADR-0024).
Detail, compose, pair and Ranking measure every hike with its way back to the start unless
``return_to_start`` is false (ADR-0019).
A browser keeps the API reads an hour and then revalidates them by an ETag of the export, the code
and the drive-time settings; the page, the API info and the Measure list always revalidate, so a
redeploy shows at once. Responses are
gzipped for a browser that takes it.
The site's pages (the demo, About, Support, Credits, Privacy, Terms) are served with two optional
settings filled in: a GoatCounter code adds the cookieless analytics tag, and a Ko-fi handle the tip
link; the privacy page names each only once it is set. Unset, a page loads no analytics and links no
tip jar.
The page may load from another origin, a loading shell on a static host (docs/deploy.md): the origins
given may then call the API (CORS). Their preflights are answered before the request budget, so
they use none of it. With none given, no other origin may.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
import time
import unicodedata
import urllib.error
from collections import deque
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from dayhike.catalog import MatrixClient, Origin, straight_line_distances_m
from dayhike.serving import (
    GEONAMES_ATTRIBUTION,
    GROUP_KMH,
    GROUP_M,
    GROUP_MIN_M,
    GROUP_SHARE,
    MEASURE_GROUPS,
    MEASURE_INFO,
    SCORE_HELP,
    WISH_INPUTS,
    ServingData,
    group_radius_m,
    name_dem,
)
from dayhike.similarity import DEFAULT_WEIGHTS, MEASURES, WISHABLE, Measure
from dayhike.trailgraph import Route, RouteError

STATIC_DIR = Path(__file__).parent / "static"
RESULTS = 10  # rows a Ranking lists; "Show 10 more" asks the next ones (ADR-0032)
MAX_ROWS = 100  # the most rows a Ranking may list in all, the rows shown before included
MAX_DRIVE_HOURS = 4.0
MAX_KM = 200.0  # the longest straight-line distance limit: 4 h at GROUP_KMH, so it groups as no limit does
MAX_GROUP_KM = 50.0  # the widest grouping radius a Hiker may ask
MAP_HIKES = 100  # a map view shows its hikes only when it holds at most this many
MAP_SEGMENTS = 1_500  # and its TrailGraph Segments only when it holds at most this many
ROUTE_STEPS = 2_000  # the most steps (OSM ways walked) a ComposedHike may have
NAME_LENGTH = 120  # the longest name a Hiker may give a ComposedHike
# Requests per client per RATE_WINDOW_S: Ranking, and everything else under /api.
RANK_RATE = 20
API_RATE = 300
RATE_WINDOW_S = 60.0
RANK_WAIT_S = 30.0  # a Ranking waits this long for the one running before it
VALHALLA_TIMEOUT_S = 50.0  # socket timeout for Valhalla calls, under Cloud Run's 60 s request timeout
# The API reads a browser keeps API_MAX_AGE_S seconds; after that the ETag revalidates them.
CACHED_READS = ("/api/info", "/api/measures", "/api/search", "/api/hikes", "/api/segments")
# The metadata the page's shape depends on: a browser revalidates it every time, so new code never reads old metadata.
REVALIDATED_READS = ("/api/info", "/api/measures")
API_MAX_AGE_S = 3600
# The code this app runs: a redeploy that changes it changes the ETag of the API reads.
BUILD = hashlib.sha256(
    b"".join(
        path.read_bytes()
        for path in sorted(Path(__file__).parent.parent.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    )
).hexdigest()

PAGES = ("index", "about", "support", "credits", "privacy", "terms")  # the site's pages, served with its settings filled in
# The elevation credits of an export sampled from Copernicus GLO-30 (ADR-0030), in place of
# FABDEM's between <!--elevation--> and <!--/elevation--> on each page that names it.
COPERNICUS_CREDITS = {
    "index": 'Copernicus GLO-30 (<a href="https://doi.org/10.5270/ESA-c5d3d65">© DLR, Airbus; Copernicus DEM licence</a>)',
    "terms": "The elevation profiles and the values derived from elevation (heights, gain, profile shape, steepness, "
    'slope aspect) are computed from Copernicus DEM GLO-30, offered under the <a href="https://docs.sentinel-hub.com/'
    'api/latest/static/files/data/dem/resources/license/License-COPDEM-30.pdf">Copernicus DEM licence</a>.',
    "credits": 'Copernicus DEM GLO-30, <a href="https://doi.org/10.5270/ESA-c5d3d65">doi:10.5270/ESA-c5d3d65</a>, '
    'from <a href="https://registry.opendata.aws/copernicus-dem/">AWS Open Data</a>, under the '
    '<a href="https://docs.sentinel-hub.com/api/latest/static/files/data/dem/resources/license/License-COPDEM-30.pdf">'
    "Copernicus DEM licence</a>. Produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence "
    "and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved. The "
    "organisations in charge of the Copernicus programme by law or by delegation do not incur any liability for any "
    "use of the Copernicus WorldDEM-30. The elevation profiles and the elevation-derived values on this site (heights, "
    "gain, profile shape, steepness, slope aspect) are computed from it; it maps the surface, so tree tops and roofs "
    "are included.",
}
PLAIN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")  # a GoatCounter code or Ko-fi handle

DriveClientFactory = Callable[[float], MatrixClient]
MEASURE_KINDS = {measure.name: measure.kind for measure in MEASURES}


class Place(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class Weighted(BaseModel):
    weights: dict[str, float] | None = None
    excluded: list[str] = []
    month: int | None = Field(default=None, ge=1, le=12)
    return_to_start: bool = True  # every hike with its way back, or every hike one way

    def given_weights(self) -> dict[str, float]:
        """The weights asked for; None is the default weights."""
        return dict(DEFAULT_WEIGHTS if self.weights is None else self.weights)

    def measure_weights(self) -> dict[str, float]:
        """The weights given with every excluded Measure at 0.

        With the way back every hike is a round trip, so Round trip is left out (ADR-0022).
        """
        weights = self.given_weights()
        dropped = []
        for name in self.excluded:
            if name not in MEASURE_INFO:
                raise HTTPException(400, f"unknown Measure: {name}")
            if weights.get(name, 0) > 0:
                dropped.append(MEASURE_INFO[name][1])
            weights[name] = 0.0
        round_trip = self.return_to_start and weights.get("round_trip", 0) > 0
        if round_trip:
            weights["round_trip"] = 0.0
        if (round_trip or dropped) and not any(weight > 0 for weight in weights.values()):
            reasons = ["Round trip is left out with Return to start (every hike is then a round trip)"] if round_trip else []
            if dropped:
                reasons.append(f"excluded: {', '.join(dropped)}")
            raise HTTPException(400, f"{'; '.join(reasons)}; give another measure a weight above 0")
        return weights


class Composed(BaseModel):
    """A ComposedHike: its Route as text (``Route.parse``) and the Hiker's name for it."""

    route: str = Field(max_length=40 * ROUTE_STEPS)  # a step is at most about 36 characters
    name: str | None = Field(default=None, max_length=NAME_LENGTH)

    @field_validator("route")
    @classmethod
    def _route(cls, text: str) -> str:
        if not 1 <= len(Route.parse(text).steps) <= ROUTE_STEPS:
            raise ValueError(f"a composed hike has 1 to {ROUTE_STEPS} steps")
        return text

    @field_validator("name")
    @classmethod
    def _name(cls, name: str | None) -> str | None:
        """Control and invisible format characters become spaces, then runs of spaces one."""
        shown = "".join(" " if unicodedata.category(c).startswith("C") else c for c in name or "")
        return " ".join(shown.split()) or None

    def parsed(self) -> Route:
        return Route.parse(self.route)


class ComposeRequest(Composed):
    return_to_start: bool = True


class RankRequest(Weighted):
    seed: int | None = None
    composed: Composed | None = None
    wish: dict[str, bool | float] | None = None  # Measure name to value, yes/no as true/false
    origin: Place | None = None
    max_hours: float | None = Field(default=None, gt=0, le=MAX_DRIVE_HOURS)
    max_km: float | None = Field(default=None, gt=0, le=MAX_KM)  # straight line from origin to a hike's start
    loops: bool = False  # SuggestedLoops too, only on request (ADR-0028)
    # Hikes starting this near a better one are its versions; None follows the limits (ADR-0032).
    # At least 0.1 km, so the rows after those shown leave the rows shown out.
    group_km: float | None = Field(default=None, ge=0.1, le=MAX_GROUP_KM)
    after: list[Place] = Field(default=[], max_length=MAX_ROWS - RESULTS)  # the starts of the rows already shown

    @field_validator("wish")
    @classmethod
    def _wish(cls, wish: dict[str, bool | float] | None) -> dict[str, bool | float] | None:
        if wish is None:
            return None
        if not wish:
            raise ValueError("a wish needs a value for at least one measure")
        for name, value in wish.items():
            if name not in WISHABLE:
                raise ValueError(f"{name} takes no wish value")
            if (MEASURE_KINDS[name] == "agree") != isinstance(value, bool):
                raise ValueError(f"{name} takes {'true or false' if MEASURE_KINDS[name] == 'agree' else 'a number'}")
            if not math.isfinite(value):
                raise ValueError(f"{name} takes a finite number")
        return wish

    @model_validator(mode="after")
    def _one_seed(self) -> RankRequest:
        if sum(given is not None for given in (self.seed, self.composed, self.wish)) != 1:
            raise ValueError("give one of a DayHike, a composed hike or a wish")
        return self

    def given_weights(self) -> dict[str, float]:
        """With a Wish only the Measures it has a value for weigh: the weights given
        with every other Measure at 0, else 100 each."""
        if self.wish is None:
            return super().given_weights()
        if self.weights is None:
            return dict.fromkeys(self.wish, 100.0)
        return {name: weight if name in self.wish else 0.0 for name, weight in self.weights.items()}


class PairRequest(Weighted):
    a: int | None = None
    composed: Composed | None = None
    b: int


def wish_input(measure: Measure) -> dict | None:
    """How the page takes a Wish value for a Measure: a number in a range, or yes/no; None takes none."""
    if measure.name not in WISHABLE:
        return None
    if measure.kind == "agree":
        return {"kind": "yes/no", "unit": None, "min": None, "max": None, "step": None, "default": True}
    unit, low, high, step, default = WISH_INPUTS[measure.name]
    return {"kind": "number", "unit": unit, "min": low, "max": high, "step": step, "default": default}


def site_pages(
    goatcounter: str | None, kofi: str | None, place_search: bool = False, *, copernicus: bool = False
) -> dict[str, str]:
    """Each page's HTML with the GoatCounter tag where a code is set, and the Ko-fi link where a handle is.

    With ``place_search`` (the export has GeoNames places) the place-search credit names GeoNames, not Nominatim,
    and the privacy page keeps its ``places`` text, not its ``nominatim`` text.
    With ``copernicus`` (an export sampled from Copernicus GLO-30), the pages credit it instead of FABDEM.

    Text between ``<!--if-goatcounter-->`` and ``<!--/if-goatcounter-->`` (or ``kofi``, ``places``,
    ``nominatim``) is kept only when that setting is on.

    The tag loads the self-hosted ``count.js``, which counts at ``https://<code>.goatcounter.com/count``.
    """
    for setting, value in (("GoatCounter code", goatcounter), ("Ko-fi handle", kofi)):
        if value is not None and not PLAIN_NAME.fullmatch(value):
            raise ValueError(f"a {setting} is letters, digits, - and _, not {value!r}")
    analytics = "" if goatcounter is None else (
        f'<script data-goatcounter="https://{goatcounter}.goatcounter.com/count"'
        ' async src="vendor/goatcounter/count.js"></script>'
    )
    pages = {}
    for name in PAGES:
        html = (STATIC_DIR / f"{name}.html").read_text(encoding="utf-8").replace("<!--analytics-->", analytics)
        settings = {
            "goatcounter": goatcounter is not None,
            "kofi": kofi is not None,
            "places": place_search,
            "nominatim": not place_search,
        }
        for setting, on in settings.items():  # text true only once it is set
            block = rf"<!--if-{setting}-->(.*?)<!--/if-{setting}-->"
            html = re.sub(block, (lambda m: m[1]) if on else "", html, flags=re.S)
        if kofi is not None:
            tip = (
                f'<p class="tip"><a href="https://ko-fi.com/{kofi}">Leave a tip on Ko-fi</a></p>\n'
                '  <p class="hint">The link opens Ko-fi, which takes the tip; this site handles no payments.</p>'
            )
            html = re.sub(r"<!--ko-fi-->.*?<!--/ko-fi-->", tip, html, flags=re.S)
        if place_search:
            credit = (
                '<span id="place-credit">Place search: towns from <a href="https://www.geonames.org/">GeoNames</a>'
                ' (geonames.org), <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>.</span>'
            )
            html = re.sub(r'<span id="place-credit">.*?</span>', credit, html, flags=re.S)
        if copernicus and name in COPERNICUS_CREDITS:
            html = re.sub(r"<!--elevation-->.*?<!--/elevation-->", COPERNICUS_CREDITS[name], html, flags=re.S)
        pages[name] = html
    return pages


class RateLimiter:
    """At most ``limit`` requests per ``window_s`` seconds per client key."""

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit, self.window_s, self._clock = limit, window_s, clock
        self._seen: dict[str, deque[float]] = {}
        self._swept = clock()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        """How many clients it remembers."""
        return len(self._seen)

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            if now - self._swept >= self.window_s:  # forget idle clients once a window
                self._seen = {k: v for k, v in self._seen.items() if v and now - v[-1] < self.window_s}
                self._swept = now
            seen = self._seen.setdefault(key, deque())
            while seen and now - seen[0] >= self.window_s:
                seen.popleft()
            if len(seen) >= self.limit:
                return False
            seen.append(now)
            return True


def create_app(
    data: ServingData,
    drive_client: DriveClientFactory | None = None,
    *,
    forwarded_hops: int = 0,
    rank_rate: int = RANK_RATE,
    api_rate: int = API_RATE,
    goatcounter: str | None = None,
    kofi: str | None = None,
    cors_origins: tuple[str, ...] = (),
) -> FastAPI:
    """The app over one serving export.

    ``drive_client`` makes the DriveTime client for a max-hours limit; None
    turns the drive-time filter off. ``forwarded_hops`` is how many proxies
    append to ``X-Forwarded-For`` in front of the app (1 on Cloud Run): the
    client is that many entries from the end. 0 uses the socket peer.
    ``goatcounter`` and ``kofi`` fill in the pages (``site_pages``); a value that
    is not a plain name is a ValueError. ``cors_origins`` are the other origins
    (``https://host``) whose pages may call the API.
    """
    pages = site_pages(goatcounter, kofi, data.places is not None, copernicus="copernicus" in data.meta["attribution"])
    app = FastAPI(title="GoFindMeAHike", docs_url="/api/docs", openapi_url="/api/openapi.json")
    limiters = {"rank": RateLimiter(rank_rate, RATE_WINDOW_S), "api": RateLimiter(api_rate, RATE_WINDOW_S)}
    one_ranking = threading.Semaphore(1)
    # The API reads change only with the export (its meta.json holds facts_version, places.sqlite may come later), the code and the settings.
    settings = f"{BUILD} {drive_client is not None} {MAX_DRIVE_HOURS} {RESULTS} {data.places is not None}".encode()
    etag = f'W/"{hashlib.sha256((data.directory / "meta.json").read_bytes() + settings).hexdigest()[:16]}"'
    attribution = data.meta["attribution"]
    measures = [
        {
            "name": measure.name,
            "kind": measure.kind,
            "group": MEASURE_INFO[measure.name][0],
            "label": MEASURE_INFO[measure.name][1],
            "description": name_dem(MEASURE_INFO[measure.name][2], attribution),
            "source": name_dem(MEASURE_INFO[measure.name][3], attribution),
            "columns": list(measure.columns),
            "monthly": measure.monthly,
            "default_weight": DEFAULT_WEIGHTS[measure.name],
            "wish": wish_input(measure),
        }
        for measure in MEASURES
    ]

    def client_key(request: Request) -> str:
        forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",")]
        if forwarded_hops and len(forwarded) >= forwarded_hops and forwarded[-forwarded_hops]:
            return forwarded[-forwarded_hops]
        return request.client.host if request.client else "unknown"

    @app.middleware("http")
    async def limit_and_harden(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path != "/api/health":
            limiter = limiters["rank" if path == "/api/rank" else "api"]
            if not limiter.allow(client_key(request)):
                return JSONResponse(
                    {"detail": "Too many requests; wait a minute."},
                    status_code=429,
                    headers={"Retry-After": str(int(RATE_WINDOW_S))},
                )
        cached = request.method in ("GET", "HEAD") and (path in CACHED_READS or path.startswith("/api/hikes/"))
        response = await call_next(request)
        if cached and response.status_code == 200:
            policy = "no-cache" if path in REVALIDATED_READS else f"public, max-age={API_MAX_AGE_S}"
            headers = {"Cache-Control": policy, "ETag": etag}
            if etag in (tag.strip() for tag in request.headers.get("if-none-match", "").split(",")):
                response = Response(status_code=304, headers=headers)
            else:
                response.headers.update(headers)
        if not path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"  # revalidate (ETag): a redeploy shows at once
        response.headers["X-Content-Type-Options"] = "nosniff"
        # OSM tile and Nominatim policies ask for a Referer; send the origin only.
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    app.add_middleware(GZipMiddleware)  # API JSON and the page, for a browser that takes gzip
    if cors_origins:  # outermost: a preflight is answered before the limiter, and a 429 is readable
        app.add_middleware(
            CORSMiddleware, allow_origins=list(cors_origins), allow_methods=["GET", "HEAD", "POST"],
            allow_headers=["Content-Type"], max_age=3600,
        )

    # A refusal leaves out the input it refuses: a NaN or Infinity in the body cannot be written back as JSON.
    @app.exception_handler(RequestValidationError)
    async def refuse(request: Request, error: RequestValidationError) -> JSONResponse:
        errors = [{key: value for key, value in e.items() if key != "input"} for e in error.errors()]
        return JSONResponse({"detail": jsonable_encoder(errors)}, status_code=422)

    # Reads answer HEAD too (health checks); otherwise HEAD falls through to the page mount's 404.
    @app.api_route("/api/health", methods=["GET", "HEAD"])
    def health() -> dict:
        return {"ok": True}

    # The Trailheads' box (south, west, north, east): the page warns when a typed
    # "lat, lon" is outside it but its swap is inside.
    coverage = [*map(float, data.trailheads.min(axis=0)), *map(float, data.trailheads.max(axis=0))]

    @app.api_route("/api/info", methods=["GET", "HEAD"])
    def info() -> dict:
        return {
            "dayhikes": data.meta["dayhikes"],
            "coverage": coverage,
            "attribution": data.meta["attribution"] | ({"geonames": GEONAMES_ATTRIBUTION} if data.places else {}),
            "place_search": data.places is not None,
            "drive_time": drive_client is not None,
            "compose": data.trailgraph is not None,
            "max_drive_hours": MAX_DRIVE_HOURS,
            "max_km": MAX_KM,
            "results": RESULTS,
            "max_rows": MAX_ROWS,
            # The default grouping radius (ADR-0032): share of the limit, a drive hour as kmh km, at least min_km, km with none.
            "grouping": {"km": GROUP_M / 1000, "share": GROUP_SHARE, "min_km": GROUP_MIN_M / 1000, "kmh": GROUP_KMH},
        }

    score_help = {
        **SCORE_HELP,
        "sections": [{**section, "text": name_dem(section["text"], attribution)} for section in SCORE_HELP["sections"]],
    }

    @app.api_route("/api/measures", methods=["GET", "HEAD"])
    def measure_list() -> dict:
        return {"groups": [{"name": name, "label": label} for name, label in MEASURE_GROUPS.items()], "measures": measures, "score_help": score_help}

    @app.api_route("/api/search", methods=["GET", "HEAD"])
    def search(q: str = Query(max_length=100), limit: int = Query(20, ge=1, le=50)) -> dict:
        return {"results": data.search(q, limit)}

    # The page's "Find place": a start for the drive filter, without asking Nominatim.
    @app.api_route("/api/place", methods=["GET", "HEAD"])
    def place(q: str = Query(max_length=100), limit: int = Query(5, ge=1, le=20)) -> dict:
        if data.places is None:
            raise HTTPException(404, "no place search in this export")
        return {"results": data.find_place(q, limit)}

    @app.api_route("/api/hikes", methods=["GET", "HEAD"])
    def hikes_in_box(
        south: float = Query(ge=-90, le=90),
        west: float = Query(ge=-180, le=180),
        north: float = Query(ge=-90, le=90),
        east: float = Query(ge=-180, le=180),
    ) -> dict:
        if south > north or west > east:
            raise HTTPException(400, "the box needs south <= north and west <= east")
        results, too_many = data.in_box(south, west, north, east, MAP_HIKES)
        return {"results": results, "too_many": too_many}

    def trailgraph() -> None:
        if data.trailgraph is None:
            raise HTTPException(404, "composing hikes is not available on this server")

    def seed_of(dayhike_id: int | None, composed: Composed | None) -> int | Route:
        """The Seed a request names: a DayHike id or a ComposedHike, exactly one."""
        if (dayhike_id is None) == (composed is None):
            raise HTTPException(400, "give a DayHike or a composed hike, not both or neither")
        if composed is None:
            try:
                data.row_of(dayhike_id)
            except KeyError:
                raise HTTPException(404, f"no DayHike {dayhike_id}") from None
            return dayhike_id
        trailgraph()
        return composed.parsed()

    @app.api_route("/api/segments", methods=["GET", "HEAD"])
    def segments_in_box(
        south: float = Query(ge=-90, le=90),
        west: float = Query(ge=-180, le=180),
        north: float = Query(ge=-90, le=90),
        east: float = Query(ge=-180, le=180),
    ) -> dict:
        trailgraph()
        if south > north or west > east:
            raise HTTPException(400, "the box needs south <= north and west <= east")
        found, too_many = data.segments_in_box(south, west, north, east, MAP_SEGMENTS)
        return {**found, "too_many": too_many}

    @app.post("/api/compose")
    def compose(body: ComposeRequest) -> dict:
        trailgraph()
        try:
            return data.compose(body.parsed(), body.name, body.return_to_start)
        except RouteError as error:
            raise HTTPException(400, str(error)) from None

    @app.api_route("/api/hikes/{dayhike_id}", methods=["GET", "HEAD"])
    def hike(dayhike_id: int, return_to_start: bool = True) -> dict:
        try:
            return data.detail(dayhike_id, return_to_start)
        except KeyError:
            raise HTTPException(404, f"no DayHike {dayhike_id}") from None

    @app.post("/api/rank")
    def rank(body: RankRequest) -> dict:
        started = time.monotonic()
        weights = body.measure_weights()
        if (body.origin is None) != (body.max_hours is None and body.max_km is None):
            raise HTTPException(400, "give origin with max_hours, max_km or both, or none of them")
        if body.max_hours is not None and drive_client is None:
            raise HTTPException(400, "the drive-time filter is not available on this server")
        origin = None if body.origin is None else Origin(body.origin.lat, body.origin.lon)
        near = None if body.max_km is None else (origin, body.max_km * 1000)
        group_m = group_radius_m(body.max_hours, body.max_km) if body.group_km is None else body.group_km * 1000
        seed = seed_of(body.seed, body.composed) if body.wish is None else body.wish
        if not one_ranking.acquire(timeout=RANK_WAIT_S):
            raise HTTPException(503, "busy; try again in a moment")
        try:
            hours = reach = None
            if body.max_hours is not None:
                max_hours = body.max_hours
                client = drive_client(max_hours)
                hours = data.reachable(origin, max_hours, client)

                def reach(places: list[tuple[float, float]]) -> list[float | None]:
                    return data.drive_hours(origin, max_hours, client, places)

            results = data.rank(
                seed,
                weights,
                month=body.month,
                allowed_rows=None if hours is None else sorted(hours),
                limit=RESULTS,
                return_to_start=body.return_to_start,
                reach=reach,
                loops=body.loops,
                near=near,
                group_m=group_m,
                after=[(place.lat, place.lon) for place in body.after],
            )
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            raise HTTPException(503, "the drive-time service is not answering; try again") from None
        except ValueError as error:  # a RouteError too
            raise HTTPException(400, str(error)) from None
        finally:
            one_ranking.release()
        listed = [hike for row in results for hike in (row, *row["versions"])]
        for result in listed:
            if result["id"] is not None:  # a SuggestedLoop has the hours to its own start
                result["drive_hours"] = None if hours is None else hours[data.row_of(result["id"])]
        starts = [result["trailhead"] for result in listed]
        metres = straight_line_distances_m(origin, starts) if origin is not None and starts else [None] * len(starts)
        for result, distance_m in zip(listed, metres):
            result["distance_km"] = None if distance_m is None else round(float(distance_m) / 1000, 1)
        reachable = None if hours is None else set(hours)
        if near is not None:  # the rows within both limits
            within = (straight_line_distances_m(origin, data.trailheads) <= near[1]).nonzero()[0].tolist()
            reachable = set(within) if reachable is None else reachable & set(within)
        return {
            "seed": body.seed,
            "composed": None if body.composed is None else body.composed.model_dump(),
            "wish": body.wish,
            "month": body.month,
            "return_to_start": body.return_to_start,
            "loops": body.loops,
            "max_hours": body.max_hours,
            "max_km": body.max_km,
            "group_km": round(group_m / 1000, 3),
            "after": len(body.after),
            "weights": {name: weight for name, weight in weights.items() if weight > 0},
            "reachable": None if reachable is None else len(reachable),
            "results": results,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }

    @app.post("/api/pair")
    def pair(body: PairRequest) -> dict:
        weights = body.measure_weights()
        a = seed_of(body.a, body.composed)
        try:
            result = data.pair(a, body.b, weights, month=body.month, return_to_start=body.return_to_start)
        except KeyError as error:
            raise HTTPException(404, f"no DayHike {error.args[0]}") from None
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        return {
            **result,
            "month": body.month,
            "return_to_start": body.return_to_start,
            "weights": {name: w for name, w in weights.items() if w > 0},
        }

    page_etags = {name: f'"{hashlib.sha256(html.encode()).hexdigest()[:16]}"' for name, html in pages.items()}

    def site_page(name: str, request: Request) -> Response:
        """A page with its ETag, like the static files: the browser revalidates it every time."""
        if name not in pages:
            raise HTTPException(404, "Not Found")
        etag = page_etags[name]
        if etag in (tag.strip() for tag in request.headers.get("if-none-match", "").split(",")):
            return Response(status_code=304, headers={"ETag": etag})
        return HTMLResponse(pages[name], headers={"ETag": etag})

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    def home(request: Request) -> Response:
        return site_page("index", request)

    @app.api_route("/{name}.html", methods=["GET", "HEAD"], include_in_schema=False)
    def page(name: str, request: Request) -> Response:
        return site_page(name, request)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="page")
    return app
