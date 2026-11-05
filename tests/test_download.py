import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dayhike import download as download_module
from dayhike.download import ShortDownload, download

WHOLE = b"x" * 1000


@pytest.fixture
def server(monkeypatch):
    """A server whose first ``short`` answers close after 10 of 1000 bytes."""
    monkeypatch.setattr(download_module.time, "sleep", lambda _: None)
    # Proxies from the environment: on macOS the system's would start native
    # threads here, and a later test's fork would warn of them.
    monkeypatch.setenv("no_proxy", "*")
    state = {"short": 0, "requests": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"] += 1
            self.send_response(200)
            self.send_header("Content-Length", str(len(WHOLE)))
            self.end_headers()
            if state["requests"] <= state["short"]:
                self.wfile.write(WHOLE[:10])
                self.close_connection = True
            else:
                self.wfile.write(WHOLE)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = False  # server_close joins them: no thread outlives the test
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/tile.tif", state
    httpd.shutdown()
    thread.join()
    httpd.server_close()


def test_a_download_cut_short_is_fetched_again(tmp_path: Path, server) -> None:
    url, state = server
    state["short"] = 1
    dest = tmp_path / "tile.tif"

    download(url, str(dest))

    assert dest.read_bytes() == WHOLE
    assert state["requests"] == 2


def test_a_download_always_cut_short_leaves_no_file(tmp_path: Path, server) -> None:
    url, state = server
    state["short"] = 99
    dest = tmp_path / "tile.tif"

    with pytest.raises(ShortDownload, match="10 of 1000"):
        download(url, str(dest))

    assert list(tmp_path.iterdir()) == []
