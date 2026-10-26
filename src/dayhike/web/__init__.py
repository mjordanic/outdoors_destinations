"""The public read-only web demo: a JSON API over a serving export, and the page.

``create_app`` serves ``ServingData`` (``dayhike.serving``): name search, hike
detail, the Measure list, the score of any two hikes, and Ranking with an
optional highways-only DriveTime pre-filter. Every client gets a request budget per minute, and one Ranking
runs at a time per instance, so a small container holds its memory.
"""

from __future__ import annotations

import threading
import time
import urllib.error
from collections import deque
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dayhike.catalog import MatrixClient, Origin
from dayhike.serving import MEASURE_GROUPS, MEASURE_INFO, ServingData
from dayhike.similarity import DEFAULT_WEIGHTS, MEASURES

STATIC_DIR = Path(__file__).parent / "static"
RESULTS = 10
MAX_DRIVE_HOURS = 4.0
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

    def measure_weights(self) -> dict[str, float]:
        """The weights with every excluded Measure at 0; None is the default weights."""
        weights = dict(DEFAULT_WEIGHTS if self.weights is None else self.weights)
        for name in self.excluded:
            if name not in MEASURE_INFO:
                raise HTTPException(400, f"unknown Measure: {name}")
            weights[name] = 0.0
        return weights


class RankRequest(Weighted):
    seed: int
    origin: Place | None = None
    max_hours: float | None = Field(default=None, gt=0, le=MAX_DRIVE_HOURS)


class PairRequest(Weighted):
    a: int
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
            "max_drive_hours": MAX_DRIVE_HOURS,
            "results": RESULTS,
        }

    @app.api_route("/api/measures", methods=["GET", "HEAD"])
    def measure_list() -> dict:
        return {"groups": list(MEASURE_GROUPS), "measures": measures}

    @app.api_route("/api/search", methods=["GET", "HEAD"])
    def search(q: str = Query(max_length=100), limit: int = Query(20, ge=1, le=50)) -> dict:
        return {"results": data.search(q, limit)}

    @app.api_route("/api/hikes/{dayhike_id}", methods=["GET", "HEAD"])
    def hike(dayhike_id: int) -> dict:
        try:
            return data.detail(dayhike_id)
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
        try:
            data.row_of(body.seed)
        except KeyError:
            raise HTTPException(404, f"no DayHike {body.seed}") from None
        if not one_ranking.acquire(timeout=RANK_WAIT_S):
            raise HTTPException(503, "busy; try again in a moment")
        try:
            hours = None
            if body.origin is not None:
                origin = Origin(body.origin.lat, body.origin.lon)
                try:
                    hours = data.reachable(origin, body.max_hours, drive_client(body.max_hours))
                except (urllib.error.URLError, ConnectionError, TimeoutError):
                    raise HTTPException(503, "the drive-time service is not answering; try again") from None
            results = data.rank(
                body.seed,
                weights,
                month=body.month,
                allowed_rows=None if hours is None else sorted(hours),
                limit=RESULTS,
            )
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        finally:
            one_ranking.release()
        for result in results:
            result["drive_hours"] = None if hours is None else hours[data.row_of(result["id"])]
        return {
            "seed": body.seed,
            "month": body.month,
            "weights": {name: weight for name, weight in weights.items() if weight > 0},
            "reachable": None if hours is None else len(hours),
            "results": results,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }

    @app.post("/api/pair")
    def pair(body: PairRequest) -> dict:
        weights = body.measure_weights()
        try:
            result = data.pair(body.a, body.b, weights, month=body.month)
        except KeyError as error:
            raise HTTPException(404, f"no DayHike {error.args[0]}") from None
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        return {**result, "month": body.month, "weights": {name: w for name, w in weights.items() if w > 0}}

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="page")
    return app
