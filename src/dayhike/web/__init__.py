"""The public read-only web demo: a JSON API over a serving export, and the page.

``create_app`` serves ``ServingData`` (``dayhike.serving``): name search, the
hikes in a map view, hike detail, the Measure list, the score of any two hikes, and Ranking with an
optional highways-only DriveTime pre-filter. Every client gets a request budget per minute, and one Ranking
runs at a time per instance, so a small container holds its memory. When the export has a TrailGraph,
it also serves the Segments in a map view and the detail of a ComposedHike, which can then be the
Seed of a Ranking or of a pair, and a Ranking lists SuggestedLoops beside the DayHikes (ADR-0020).
Detail, compose, pair and Ranking measure every hike with its way back to the start unless
``return_to_start`` is false (ADR-0019).
"""

from __future__ import annotations

import threading
import time
import unicodedata
import urllib.error
from collections import deque
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from dayhike.catalog import MatrixClient, Origin
from dayhike.serving import MEASURE_GROUPS, MEASURE_INFO, ServingData
from dayhike.similarity import DEFAULT_WEIGHTS, MEASURES
from dayhike.trailgraph import Route, RouteError

STATIC_DIR = Path(__file__).parent / "static"
RESULTS = 10
MAX_DRIVE_HOURS = 4.0
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

DriveClientFactory = Callable[[float], MatrixClient]


class Place(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class Weighted(BaseModel):
    weights: dict[str, float] | None = None
    excluded: list[str] = []
    month: int | None = Field(default=None, ge=1, le=12)
    return_to_start: bool = True  # every hike with its way back, or every hike one way

    def measure_weights(self) -> dict[str, float]:
        """The weights with every excluded Measure at 0; None is the default weights.

        With the way back every hike is a round trip, so Round trip is left out (ADR-0022).
        """
        weights = dict(DEFAULT_WEIGHTS if self.weights is None else self.weights)
        for name in self.excluded:
            if name not in MEASURE_INFO:
                raise HTTPException(400, f"unknown Measure: {name}")
            weights[name] = 0.0
        if self.return_to_start and weights.get("round_trip", 0) > 0:
            weights["round_trip"] = 0.0
            if not any(weight > 0 for weight in weights.values()):
                raise HTTPException(
                    400, "Round trip is left out with Return to start (every hike is then a round trip); "
                    "give another measure a weight above 0"
                )
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
    origin: Place | None = None
    max_hours: float | None = Field(default=None, gt=0, le=MAX_DRIVE_HOURS)


class PairRequest(Weighted):
    a: int | None = None
    composed: Composed | None = None
    b: int


class RateLimiter:
    """At most ``limit`` requests per ``window_s`` seconds per client key."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit, self.window_s = limit, window_s
        self._seen: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if len(self._seen) > 10_000:  # forget idle clients
                self._seen = {k: v for k, v in self._seen.items() if v and now - v[-1] < self.window_s}
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
) -> FastAPI:
    """The app over one serving export.

    ``drive_client`` makes the DriveTime client for a max-hours limit; None
    turns the drive-time filter off. ``forwarded_hops`` is how many proxies
    append to ``X-Forwarded-For`` in front of the app (1 on Cloud Run): the
    client is that many entries from the end. 0 uses the socket peer.
    """
    app = FastAPI(title="DayHike demo", docs_url="/api/docs", openapi_url="/api/openapi.json")
    limiters = {"rank": RateLimiter(rank_rate, RATE_WINDOW_S), "api": RateLimiter(api_rate, RATE_WINDOW_S)}
    one_ranking = threading.Semaphore(1)
    measures = [
        {
            "name": measure.name,
            "kind": measure.kind,
            "group": MEASURE_INFO[measure.name][0],
            "label": MEASURE_INFO[measure.name][1],
            "description": MEASURE_INFO[measure.name][2],
            "columns": list(measure.columns),
            "monthly": measure.monthly,
            "default_weight": DEFAULT_WEIGHTS[measure.name],
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
        response = await call_next(request)
        if not path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"  # revalidate (ETag): a redeploy shows at once
        response.headers["X-Content-Type-Options"] = "nosniff"
        # OSM tile and Nominatim policies ask for a Referer; send the origin only.
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

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
            "attribution": data.meta["attribution"],
            "drive_time": drive_client is not None,
            "compose": data.trailgraph is not None,
            "max_drive_hours": MAX_DRIVE_HOURS,
            "results": RESULTS,
        }

    @app.api_route("/api/measures", methods=["GET", "HEAD"])
    def measure_list() -> dict:
        return {"groups": list(MEASURE_GROUPS), "measures": measures}

    @app.api_route("/api/search", methods=["GET", "HEAD"])
    def search(q: str = Query(max_length=100), limit: int = Query(20, ge=1, le=50)) -> dict:
        return {"results": data.search(q, limit)}

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
        if (body.origin is None) != (body.max_hours is None):
            raise HTTPException(400, "give both origin and max_hours, or neither")
        if body.origin is not None and drive_client is None:
            raise HTTPException(400, "the drive-time filter is not available on this server")
        seed = seed_of(body.seed, body.composed)
        if not one_ranking.acquire(timeout=RANK_WAIT_S):
            raise HTTPException(503, "busy; try again in a moment")
        try:
            hours = reach = None
            if body.origin is not None:
                origin, max_hours = Origin(body.origin.lat, body.origin.lon), body.max_hours
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
            )
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            raise HTTPException(503, "the drive-time service is not answering; try again") from None
        except ValueError as error:  # a RouteError too
            raise HTTPException(400, str(error)) from None
        finally:
            one_ranking.release()
        for result in (hike for row in results for hike in (row, *row["versions"])):
            if result["id"] is not None:  # a SuggestedLoop has the hours to its own start
                result["drive_hours"] = None if hours is None else hours[data.row_of(result["id"])]
        return {
            "seed": body.seed,
            "composed": None if body.composed is None else body.composed.model_dump(),
            "month": body.month,
            "return_to_start": body.return_to_start,
            "weights": {name: weight for name, weight in weights.items() if weight > 0},
            "reachable": None if hours is None else len(hours),
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

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="page")
    return app
