"""The deploy's scripts against the app on the test catalog.

web/smoke.py, the check of a candidate, runs the checks of each region the server has hikes in, and
fails a page that names the wrong regions. The checks of the real known hikes themselves (search,
detail, Rankings) are recorded here, not run: the test catalog has none of those hikes.
web/shell.py writes the loading shell of the server.
"""

import dataclasses
import importlib.util
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
import uvicorn
import yaml

from dayhike import build_catalog, build_similarity
from dayhike.serving import ServingData, export_serving
from dayhike.web import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / "web" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # its dataclasses look their module up
    spec.loader.exec_module(module)
    return module


smoke = _script("smoke")
shell = _script("shell")



@pytest.fixture(scope="module")
def export(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("smoke")
    build_catalog(FIXTURE, directory / "catalog.sqlite")
    build_similarity(directory / "catalog.sqlite", directory / "dem")
    return export_serving(directory / "catalog.sqlite", directory / "web-serving")


@pytest.fixture
def checks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The regions whose known hikes' checks ran, by the first hike each searches for."""
    ran: list[str] = []
    # The test catalog's 4 hikes, not the many each region has at least.
    few = {name: dataclasses.replace(build, min_dayhikes=0) for name, build in smoke.BY_REGION.items()}
    monkeypatch.setattr(smoke, "BY_REGION", few)
    monkeypatch.setattr(smoke, "check_build", lambda base, info, drive, build: ran.append(build.known[0][1]))
    return ran


def _moved(export: Path, out: Path, west: set[str] | None, regions: list[dict] | None) -> ServingData:
    """A copy of ``export`` with the hikes named ``west`` (None: every hike) moved 120° west, into the
    United States, and ``meta.json`` naming ``regions`` (None: as an export from before regions, none)."""
    shutil.copytree(export, out)
    data = ServingData(out)
    trailheads = np.load(out / "trailheads.npy")
    rows = range(len(trailheads)) if west is None else [data.row_of(data.search(name)[0]["id"]) for name in west]
    for row in rows:
        trailheads[row, 1] -= 120.0
    np.save(out / "trailheads.npy", trailheads)
    meta = json.loads((out / "meta.json").read_text())
    meta.pop("regions")
    if regions is not None:
        meta["regions"] = regions
    (out / "meta.json").write_text(json.dumps(meta))
    return ServingData(out)


def serve(data: ServingData) -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    running = uvicorn.Server(uvicorn.Config(create_app(data), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    while not running.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    running.should_exit = True
    thread.join()


def test_a_europe_site_passes_with_the_europe_checks(export: Path, checks: list[str]) -> None:
    for base in serve(ServingData(export)):
        smoke.smoke(base, drive=False, pid=None)
        with pytest.raises(SystemExit):  # 4 hikes: not the 50,000 a Europe site has at least
            smoke.smoke(base, drive=False, pid=None, builds=[smoke.EUROPE])

    assert checks == ["Pot kralja Triglava"]


def test_a_europe_and_us_site_passes_with_both_regions_checks(export: Path, tmp_path: Path, checks: list[str]) -> None:
    both = _moved(export, tmp_path / "both", {"Valley Path"}, None)
    for base in serve(both):
        smoke.smoke(base, drive=False, pid=None)

    assert checks == ["Pot kralja Triglava", "Mount Whitney Trail"]


def test_a_us_site_passes_with_the_us_checks_only(export: Path, tmp_path: Path, checks: list[str]) -> None:
    us = _moved(export, tmp_path / "us", None, None)
    for base in serve(us):
        smoke.smoke(base, drive=False, pid=None)

    assert checks == ["Mount Whitney Trail"]


def test_a_us_site_whose_pages_say_europe_fails(export: Path, tmp_path: Path, checks: list[str]) -> None:
    both = _moved(export, tmp_path / "wrong", {"Valley Path"}, [{"label": "Europe", "continent": "Europe"}])
    for base in serve(both):
        with pytest.raises(SystemExit):
            smoke.smoke(base, drive=False, pid=None)

    assert checks == []


def test_the_shell_holds_the_pages_as_the_server_fills_them_in_and_asks_the_server_for_its_api(
    export: Path, tmp_path: Path
) -> None:
    us = _moved(export, tmp_path / "us", None, None)
    for base in serve(us):
        out = shell.write_shell(base + "/", tmp_path / "shell")
        page = (out / "index.html").read_text()

        assert f'<meta name="api-base" content="{base}">' in page
        assert "hikes across the United States." in page and "<!--regions-->" not in page
        assert (out / "about.html").read_text() == urllib.request.urlopen(base + "/about.html").read().decode()
        assert (out / "robots.txt").read_text().startswith("User-agent: *")
        assert "<urlset" in (out / "sitemap.xml").read_text()
    assert (out / "app.js").is_file() and (out / "vendor" / "leaflet").is_dir() and (out / "404.html").is_file()
    assert "Cache-Control: no-cache" in (out / "_headers").read_text()


@pytest.mark.parametrize(
    ("data_image", "memory_variable", "memory"),
    [
        ("r/dayhike-data:abc-eu-us", "", "4Gi"),
        ("r/dayhike-data:abc-eu", "", "4Gi"),
        ("r/dayhike-data:abc-us", "", "2Gi"),
        ("r/dayhike-data:abc-demo", "", "2Gi"),
        ("r/dayhike-data:c88caa19c38378f3-copernicus-glo30", "", "4Gi"),
        ("r/dayhike-data:abc-us", "3Gi", "3Gi"),
    ],
)
def test_the_deploy_sets_the_memory_from_the_data_image(
    tmp_path: Path, data_image: str, memory_variable: str, memory: str
) -> None:
    arguments = _candidate_deploy(tmp_path, data_image, memory_variable)
    assert arguments[arguments.index("--memory") + 1] == memory


def test_the_deploy_scales_to_zero_and_at_most_four_instances(tmp_path: Path) -> None:
    arguments = _candidate_deploy(tmp_path, "r/dayhike-data:abc-eu-us", "")
    assert arguments[arguments.index("--min-instances") + 1] == "0"
    assert arguments[arguments.index("--max-instances") + 1] == "4"


def _candidate_deploy(tmp_path: Path, data_image: str, memory_variable: str) -> list[str]:
    """The arguments the workflow's candidate step gives `gcloud run deploy`, run with a stub gcloud."""
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")
    workflow = yaml.safe_load((Path(__file__).parents[1] / ".github" / "workflows" / "deploy.yml").read_text())
    steps = [step for job in workflow["jobs"].values() for step in job.get("steps", [])]
    (script,) = [step["run"] for step in steps if step.get("id") == "candidate"]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gcloud = bin_dir / "gcloud"
    gcloud.write_text(
        "#!/bin/bash\n"
        'if [ "$2" = deploy ]; then printf "%s\\n" "$@" > "$CALLS"; fi\n'
        'echo \'{"status": {"traffic": [{"tag": "c-0123456789ab", "revisionName": "r1", "url": "https://c"}]}}\'\n'
    )
    gcloud.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin",
        "GITHUB_SHA": "0123456789abcdef",
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "CALLS": str(tmp_path / "calls"),
        "DATA_IMAGE": data_image,
        "MEMORY": memory_variable,
    }
    subprocess.run(["bash", "-e", "-c", script], env=env, check=True)
    return (tmp_path / "calls").read_text().splitlines()
