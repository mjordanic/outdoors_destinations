# Contributing to HikesAlike

Thanks for helping. This page says how a change gets in. Setup, the data
pipeline and how everything works are in the
[technical docs](docs/technical.md); start with its
[quick start](docs/technical.md#run-it-yourself-quick-start).

## Branches and pull requests

- Branch from **`develop`** and open your pull request against `develop`.
  GitHub's default branch is `main`, so change the base branch when you open
  the PR.
- CI (`.github/workflows/ci.yml`) runs `uv sync --locked` and
  `uv run pytest -n 4 -m "not real_data"` on every pull request to `develop` or
  `main`. It does not install Playwright, so the page tests in
  `tests/test_page.py` are skipped there.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):
  `fix(web): …`, `feat(ingest): …`, `docs: …`, with an optional scope naming
  the part you changed. Say in the body why, when it isn't obvious.
- One topic per pull request. Behaviour changes come with a test.

## Tests

```bash
uv run pytest -n 4                # the whole fast suite on 4 cores: about 70 to 80 seconds
uv run pytest                     # the same, on one core: about 2.5 minutes
uv run pytest tests/test_web.py   # just the file next to your change: seconds
uv run pytest -k rank             # tests whose name matches
uv run playwright install chromium   # once, to run tests/test_page.py locally too
```

The suite prints a few thousand warnings, mostly Python's `fork()`
DeprecationWarning; they are expected.

Tests build their own small Catalogs in a temporary directory and never read
data you built. Checks on real data are opt-in:
`DAYHIKE_REAL_DATA=1 uv run pytest -m real_data` (see
[Tests](docs/technical.md#tests)).

## Where things are

| Path | What it holds |
| --- | --- |
| `src/dayhike/cli.py` | Every `dayhike` command and its options. |
| `src/dayhike/osm.py`, `ingest.py`, `catalog.py` | Reading OSM, ingest (filter, assemble, write) and the Catalog: which routes are kept, the main walk, the Trailhead, names. |
| `src/dayhike/built.py`, `stages.py`, `network_hikes.py` | Hikes that are not one OSM relation: day stages of long tours and hikes chained from signpost networks. |
| `src/dayhike/trailgraph.py`, `loops.py` | The trail network: the way back to the start, hikes drawn from segments, suggested loops. |
| `src/dayhike/dem.py`, `landcover.py`, `climate.py` | Downloading and sampling heights, land cover and climate. |
| `src/dayhike/similarity.py` | The facts, the Measures, the Calibration and the Ranking. Start here for a scoring bug. |
| `src/dayhike/valhalla.py`, `highways.py` | Drive time through Valhalla. |
| `src/dayhike/serving.py` | The serving export the web demo reads, and the text of the score explanations. |
| `src/dayhike/web/__init__.py`, `web/static/` | The web API and the page (`app.js`, the HTML pages, `style.css`). Start here for a page bug. |
| `src/dayhike/coverage.py`, `extracts.py`, `parallel.py` | The coverage map, the Geofabrik downloads, worker processes. |
| `web/` | The container (data and web Dockerfiles, Cloud Build config, compose file, start script) and `smoke.py`. |
| `tests/` | The test suite and its small fixtures. |
| `notebooks/` | Walkthroughs of ingest, the Catalog, ranking and the Measures. |

The words the code uses (DayHike, Seed, Measure, Trailhead, …) are defined in
[GLOSSARY.md](GLOSSARY.md). Design decisions are in [docs/adr/](docs/adr/),
one file each.

## Issues

Report bugs and ask questions in
[GitHub issues](https://github.com/mjordanic/outdoors_destinations/issues). The
`.scratch/` folder holds the maintainer's own planning notes; it is not the
issue tracker. `AGENTS.md` and `CLAUDE.md` are instructions for coding agents,
not house rules for people.

## Licence

By contributing you agree that your code is released under the
[MIT licence](LICENSE), like the rest of the code.
