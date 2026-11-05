"""Downloading one raster file (DEM, WorldCover or CHELSA) to disk.

The file appears under its own name only once it is whole: the fetch stages
take any non-empty file there as present and never fetch it again.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ATTEMPTS = 5


class ShortDownload(OSError):
    """The server closed the connection before sending all it announced."""


def download(url: str, filename: str) -> None:
    """Fetch ``url`` into ``filename`` through a ``.part`` file, trying again
    after a network error or a download shorter than its Content-Length.
    A 404 raises ``FileNotFoundError`` at once."""
    dest = Path(filename)
    tmp = dest.with_name(dest.name + ".part")
    request = Request(url, headers={"User-Agent": "dayhike"})
    last_error: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            with urlopen(request, timeout=120) as source, tmp.open("wb") as out:
                shutil.copyfileobj(source, out)
                expected = source.headers.get("Content-Length")
                written = out.tell()
            if expected is not None and written != int(expected):
                raise ShortDownload(f"{url}: got {written} of {expected} bytes")
            tmp.replace(dest)
            return
        except HTTPError as exc:
            tmp.unlink(missing_ok=True)
            if exc.code == 404:
                raise FileNotFoundError(url) from exc
            last_error = exc
        except (URLError, TimeoutError, OSError) as exc:
            tmp.unlink(missing_ok=True)
            last_error = exc
        time.sleep(min(2**attempt, 16))
    assert last_error is not None
    raise last_error
