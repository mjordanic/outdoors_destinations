# syntax=docker/dockerfile:1
# The web demo: FastAPI app + highways-only Valhalla in one container, data baked in.
#
#   docker build -f web/Dockerfile --build-context serving=data/web-serving -t dayhike-web .
#
# The `serving` build context is a directory written by `dayhike export-serving`.
# Valhalla 3.5.1: the same image that built the highways tiles (tiles are version-sensitive).
FROM ghcr.io/gis-ops/docker-valhalla/valhalla:latest@sha256:060da5b92e6024a67f65135c236d918b021d63e73d1808a0d55b7e7cbd17240c

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

COPY --from=serving . /data
COPY web/start.sh /app/start.sh

USER valhalla
ENV PORT=8080 DAYHIKE_DATA=/data
EXPOSE 8080
ENTRYPOINT []
CMD ["/app/start.sh"]
