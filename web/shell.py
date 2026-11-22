"""Write the loading shell (docs/deploy.md, section 8): the site's pages for a static host, which shows
the page, and its "Waking the server…" screen, at once while Cloud Run starts.

    python3 web/shell.py --url https://SERVICE.run.app --out SHELL_DIR

The pages are fetched from the running server at --url, so they hold what it fills in (the regions and
hike count of its data, its GoatCounter tag, base map and site address, robots.txt and sitemap.xml);
index.html then asks that server for its API. The other static files are the package's own, the same
code as the server's image when run from the commit it was deployed from. A Cloudflare Pages
``_headers`` file sets the headers the server sends with its pages.
"""

from __future__ import annotations

import argparse
import shutil
import urllib.request
from pathlib import Path

STATIC = Path(__file__).parents[1] / "src" / "dayhike" / "web" / "static"
# The server's pages and the files it writes itself: the shell's file, and the address it is fetched from.
FETCHED = {
    "index.html": "/",
    "about.html": "/about.html",
    "credits.html": "/credits.html",
    "privacy.html": "/privacy.html",
    "terms.html": "/terms.html",
    "robots.txt": "/robots.txt",
    "sitemap.xml": "/sitemap.xml",
}
API_BASE = '<meta name="api-base" content="">'
HEADERS = """/*
  Cache-Control: no-cache
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
"""


def write_shell(url: str, out: Path) -> Path:
    """Write the shell of the server at ``url`` into ``out``, which must not exist yet."""
    url = url.rstrip("/")
    shutil.copytree(STATIC, out)
    for name, path in FETCHED.items():
        with urllib.request.urlopen(url + path, timeout=120) as response:
            (out / name).write_bytes(response.read())
    index = (out / "index.html").read_text(encoding="utf-8")
    if index.count(API_BASE) != 1:
        raise SystemExit(f"{url}/ has no empty {API_BASE}: is it the site's page?")
    (out / "index.html").write_text(index.replace(API_BASE, f'<meta name="api-base" content="{url}">'), encoding="utf-8")
    shutil.copyfile(STATIC / "not-found.html", out / "404.html")  # what Cloudflare Pages answers an unknown address with
    (out / "_headers").write_text(HEADERS)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True, help="The server whose API the shell calls (the Cloud Run URL)")
    parser.add_argument("--out", type=Path, required=True, help="The directory to write; must not exist")
    args = parser.parse_args()
    print(write_shell(args.url, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
