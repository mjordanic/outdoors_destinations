# syntax=docker/dockerfile:1
# The web demo: FastAPI app + highways-only Valhalla in one container, data baked in.
#
#   docker build -f web/Dockerfile --build-arg DATA_IMAGE=dayhike-data:FACTS_VERSION -t dayhike-web .
#
# DATA_IMAGE is the data image (web/data.Dockerfile): Valhalla 3.5.1 plus the serving
# export at /data. Its layers come first, so a code-only change rebuilds and pushes only
# the layers that follow.
ARG DATA_IMAGE=dayhike-data
FROM ${DATA_IMAGE}

USER root
COPY --from=ghcr.io/astral-sh/uv:0.11.13 /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1
WORKDIR /app
COPY pyproject.toml uv.lock .python-version LICENSE ./
RUN uv sync --frozen --no-dev --extra web --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra web --no-editable && rm -rf /app/src

COPY web/start.sh /app/start.sh

USER valhalla
# Two malloc arenas, not one per worker thread: each arena keeps its own high-water mark (~0.4 GB at 8 visitors).
ENV PORT=8080 DAYHIKE_DATA=/data MALLOC_ARENA_MAX=2
EXPOSE 8080
ENTRYPOINT []
CMD ["/app/start.sh"]
