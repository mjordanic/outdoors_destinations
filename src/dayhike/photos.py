"""Free Wikimedia Commons photos for a hike's named POIs.

``export_photos`` reads the GeometryStore beside a Catalog and writes at most
two photos onto each DayHike in a serving export: a named POI with
``wikimedia_commons`` or ``wikidata``, and only CC0, CC BY or CC BY-SA. The
image stays on ``upload.wikimedia.org``; the export stores the file name, the
author, the licence and the licence URL, plus the thumbnail address. API
answers are cached, so a later run fetches only what it does not already hold.
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
import time
import urllib.parse
import zlib
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from urllib.request import Request, urlopen

from dayhike.catalog import geometry_store_path_for
from dayhike.db import scoped

USER_AGENT = "HikesAlike (https://hikesalike.com)"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
PAUSE_S = 1.0
BATCH = 50
MAX_URL = 4000
PROGRESS_EVERY = 20
CATEGORY_FILES = 10
THUMB_PX = 480
_FREE = re.compile(r"cc by( sa)?( \d+(?:\.\d+)?)?")
_QID = re.compile(r"(?:https?://www\.wikidata\.org/(?:wiki|entity)/)?(Q\d+)", re.IGNORECASE)

Opener = Callable[[Request], bytes]
Progress = Callable[[str], None]


def plain_text(value: str) -> str:
    """Visible text of a Commons author credit, which is often a snippet of HTML."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def licence_free(licence: str) -> bool:
    """True for CC0, CC BY and CC BY-SA of any version. NC, ND and anything else are out."""
    text = " ".join(licence.strip().lower().replace("_", "-").replace("-", " ").split())
    if text in {"cc0", "cc0 1.0"}:
        return True
    return _FREE.fullmatch(text) is not None


def kept_photo(*, file: str, author: str, licence: str, licence_url: str, thumb: str) -> dict | None:
    """The photo the page may show, or None when the credit or the licence does not qualify."""
    licence = licence.strip()
    licence_url = licence_url.strip()
    if licence_url.startswith("http://"):
        licence_url = "https://" + licence_url[len("http://") :]
    author = plain_text(author)
    file = plain_text(file)
    thumb = thumb.strip()
    if not file or not author or not licence_free(licence):
        return None
    if not licence_url.startswith("https://") or not thumb.startswith("https://upload.wikimedia.org/"):
        return None
    return {
        "file": file,
        "author": author,
        "licence": licence,
        "licence_url": licence_url,
        "thumb": thumb,
    }


def photos_for_hike(pois: Sequence[dict], resolve: Callable[[dict], dict | None]) -> list[dict]:
    """Up to two free photos, in walk order, from named POIs. One file is kept once."""
    kept: list[dict] = []
    seen: set[str] = set()
    for poi in pois:
        if not poi.get("name"):
            continue
        photo = resolve(poi)
        if not photo or photo["file"] in seen:
            continue
        seen.add(photo["file"])
        kept.append(photo)
        if len(kept) == 2:
            break
    return kept


def commons_target(value: str) -> tuple[str, str] | None:
    """``('file', 'File:…')`` or ``('category', 'Category:…')`` from a ``wikimedia_commons`` tag."""
    text = urllib.parse.unquote(value.strip())
    if "/wiki/" in text:
        text = urllib.parse.unquote(text.split("/wiki/", 1)[1])
    text = text.replace("_", " ").strip()
    lower = text.lower()
    if lower.startswith("category:"):
        return ("category", "Category:" + text.split(":", 1)[1].strip())
    if lower.startswith("file:"):
        return ("file", "File:" + text.split(":", 1)[1].strip())
    return None


def qid_of(value: str) -> str | None:
    """A Wikidata id from a ``wikidata`` tag, or None."""
    match = _QID.fullmatch(value.strip())
    return match.group(1).upper() if match else None


def _norm_title(title: str) -> str:
    text = urllib.parse.unquote(title).replace("_", " ").strip()
    if text.lower().startswith("file:"):
        return "File:" + text.split(":", 1)[1].strip()
    return text


def _as_file(name: str) -> str:
    return _norm_title(name if name.lower().startswith("file:") else f"File:{name}")


def upload_thumb(url: str) -> str:
    """A thumbnail address on ``upload.wikimedia.org``.

    Imageinfo often returns the same file on ``thumb.wikimedia.org``, with a
    tracking query. The page loads the thumbnail from the upload host.
    """
    bare = url.strip().split("?", 1)[0]
    prefix = "https://thumb.wikimedia.org/"
    if bare.startswith(prefix):
        return "https://upload.wikimedia.org/" + bare[len(prefix) :]
    return bare


def photo_from_page(page: dict | None) -> dict | None:
    """One Commons ``imageinfo`` page, reduced to a photo the page may show."""
    if not page or not page.get("imageinfo"):
        return None
    info = page["imageinfo"][0]
    meta = info.get("extmetadata") or {}

    def value(key: str) -> str:
        raw = meta.get(key)
        return str(raw.get("value") or "") if isinstance(raw, dict) else ""

    title = str(page.get("title") or "")
    file_name = title.split(":", 1)[1] if ":" in title else title
    return kept_photo(
        file=file_name.replace("_", " "),
        author=value("Artist"),
        licence=value("LicenseShortName") or value("License"),
        licence_url=value("LicenseUrl"),
        thumb=upload_thumb(str(info.get("thumburl") or "")),
    )


def _chunks(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _files_url(titles: Sequence[str]) -> str:
    return COMMONS_API + "?" + urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": str(THUMB_PX),
            "maxlag": "5",
            "redirects": "1",
            "titles": "|".join(titles),
        }
    )


def _file_batches(titles: Sequence[str]) -> Iterable[list[str]]:
    """Up to ``BATCH`` titles per batch, each batch's request URL at most ``MAX_URL`` long.

    Non-ASCII titles grow ~6x when percent-encoded, so a count cap alone can
    exceed the server's URL limit (HTTP 414). A title too long on its own goes alone.
    """
    batch: list[str] = []
    for title in titles:
        if batch and (len(batch) >= BATCH or len(_files_url([*batch, title])) > MAX_URL):
            yield batch
            batch = []
        batch.append(title)
    if batch:
        yield batch


def _urlopen_bytes(request: Request) -> bytes:
    with urlopen(request, timeout=60) as response:
        return response.read()


class CommonsClient:
    """Batched Commons and Wikidata reads, kept in a SQLite cache.

    A second client on the same file, including a later rebuild, does not ask
    again for a title, a category or a Wikidata id already stored. Requests
    wait ``pause_s`` after the one before, and name the project in the User-Agent.
    """

    def __init__(
        self,
        path: Path | str,
        opener: Opener | None = None,
        *,
        pause_s: float = PAUSE_S,
        progress: Progress | None = None,
    ) -> None:
        self.path = Path(path)
        self.progress = progress
        self.opener = opener or _urlopen_bytes
        self.pause_s = pause_s
        self._next = 0.0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with scoped(self._db()) as db:
            db.execute("CREATE TABLE IF NOT EXISTS response (key TEXT PRIMARY KEY, body TEXT NOT NULL)")

    def _report(self, line: str) -> None:
        if self.progress is not None:
            self.progress(line)

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _get(self, key: str):
        with scoped(self._db()) as db:
            row = db.execute("SELECT body FROM response WHERE key = ?", (key,)).fetchone()
        return None if row is None else json.loads(row[0])

    def _put(self, key: str, value: object) -> None:
        with scoped(self._db()) as db:
            db.execute(
                "INSERT OR REPLACE INTO response (key, body) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def _fetch(self, url: str) -> dict:
        error: Exception | None = None
        for attempt in range(4):
            wait = self._next - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            try:
                payload = self.opener(request)
                self._next = time.monotonic() + self.pause_s
                data = json.loads(payload)
            except (OSError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
                error = exc
                time.sleep(min(2**attempt, 8))
                continue
            if isinstance(data, dict) and "error" in data:
                error = RuntimeError(str(data["error"]))
                time.sleep(min(5 * (attempt + 1), 20))
                continue
            return data
        raise RuntimeError(f"Wikimedia request failed: {url}") from error

    def files(self, titles: Sequence[str], label: str = "files") -> dict[str, dict | None]:
        """Image info for each file title. A non-free file, or one with no author, is None."""
        wanted = list(dict.fromkeys(titles))
        if not wanted:
            return {}
        missing = [title for title in wanted if self._get(f"file:{_norm_title(title)}") is None]
        done = 0
        for number, batch in enumerate(_file_batches(missing), start=1):
            data = self._fetch(_files_url(batch))
            query = data.get("query", {})
            normalized = _title_map(query.get("normalized"))
            redirects = _title_map(query.get("redirects"))
            found: dict[str, dict] = {}
            for page in _image_pages(data):
                found[_norm_title(str(page.get("title") or ""))] = page
            for title in batch:
                name = normalized.get(_norm_title(title), _norm_title(title))
                page = found.get(redirects.get(name, name))
                self._put(f"file:{_norm_title(title)}", page if page is not None else {"title": title, "missing": True})
            done += len(batch)
            if number % PROGRESS_EVERY == 0 or done == len(missing):
                self._report(f"photos: {label} {done}/{len(missing)}")
        return {title: photo_from_page(self._get(f"file:{_norm_title(title)}")) for title in wanted}

    def _load_entities(self, qids: Sequence[str]) -> None:
        missing = [qid for qid in qids if self._get(f"entity:{qid}") is None]
        done = 0
        for number, batch in enumerate(_chunks(missing, BATCH), start=1):
            url = WIKIDATA_API + "?" + urllib.parse.urlencode(
                {"action": "wbgetentities", "format": "json", "props": "claims", "ids": "|".join(batch)}
            )
            entities = self._fetch(url).get("entities") or {}
            for qid in batch:
                self._put(f"entity:{qid}", _p18(entities.get(qid) or {}))
            done += len(batch)
            if number % PROGRESS_EVERY == 0 or done == len(missing):
                self._report(f"photos: entities {done}/{len(missing)}")

    def _entity_file(self, qid: str) -> str:
        cached = self._get(f"entity:{qid}")
        if cached is None:
            self._load_entities([qid])
            cached = self._get(f"entity:{qid}")
        return cached or ""

    def _category_files(self, title: str) -> list[str]:
        cached = self._get(f"category:{title}")
        if cached is None:
            url = COMMONS_API + "?" + urllib.parse.urlencode(
                {
                    "action": "query",
                    "format": "json",
                    "list": "categorymembers",
                    "cmtitle": title,
                    "cmtype": "file",
                    "cmlimit": str(CATEGORY_FILES),
                    "maxlag": "5",
                }
            )
            members = self._fetch(url).get("query", {}).get("categorymembers") or []
            cached = [_norm_title(str(member.get("title") or "")) for member in members if member.get("title")]
            self._put(f"category:{title}", cached)
        return list(cached)

    def warm(self, tags_list: Iterable[dict]) -> None:
        """Fetch the files these POIs name, in batches.

        A direct Commons file and a Wikidata image (P18) come first. A Commons
        category is read only when neither of those is a free photo, so a
        rebuild does not list a category it will not use.
        """
        tags_list = list(tags_list)
        files: list[str] = []
        qids: list[str] = []
        for tags in tags_list:
            target = commons_target(str(tags.get("wikimedia_commons") or ""))
            if target is not None and target[0] == "file":
                files.append(target[1])
            qid = qid_of(str(tags.get("wikidata") or ""))
            if qid:
                qids.append(qid)
        qids = list(dict.fromkeys(qids))
        self._load_entities(qids)
        for qid in qids:
            title = self._entity_file(qid)
            if title:
                files.append(title)
        known = self.files(list(dict.fromkeys(files)))
        categories: list[str] = []
        for tags in tags_list:
            if _already_free(tags, known, self):
                continue
            target = commons_target(str(tags.get("wikimedia_commons") or ""))
            if target is not None and target[0] == "category":
                categories.append(target[1])
        extra: list[str] = []
        listed = list(dict.fromkeys(categories))
        for number, category in enumerate(listed, start=1):
            extra.extend(self._category_files(category))
            if number % (PROGRESS_EVERY * BATCH) == 0 or number == len(listed):
                self._report(f"photos: categories {number}/{len(listed)}")
        self.files([title for title in dict.fromkeys(extra) if title not in known], "category files")

    def one(self, tags: dict) -> dict | None:
        """One photo for a POI: its Commons file, else its Wikidata image, else the first free file in its category."""
        target = commons_target(str(tags.get("wikimedia_commons") or ""))
        if target is not None and target[0] == "file":
            photo = self.files([target[1]]).get(target[1])
            if photo:
                return photo
        qid = qid_of(str(tags.get("wikidata") or ""))
        if qid:
            title = self._entity_file(qid)
            if title:
                photo = self.files([title]).get(title)
                if photo:
                    return photo
        if target is None or target[0] != "category":
            return None
        for title in self._category_files(target[1]):
            photo = self.files([title]).get(title)
            if photo:
                return photo
        return None


def _already_free(tags: dict, known: dict[str, dict | None], client: CommonsClient) -> bool:
    target = commons_target(str(tags.get("wikimedia_commons") or ""))
    if target is not None and target[0] == "file" and known.get(target[1]):
        return True
    qid = qid_of(str(tags.get("wikidata") or ""))
    if not qid:
        return False
    title = client._entity_file(qid)
    return bool(title and known.get(title))


def _title_map(items: list[dict] | None) -> dict[str, str]:
    return {
        _norm_title(str(item.get("from") or "")): _norm_title(str(item.get("to") or ""))
        for item in items or []
    }


def _image_pages(data: dict) -> list[dict]:
    pages = data.get("query", {}).get("pages", {})
    if isinstance(pages, dict):
        return list(pages.values())
    return list(pages)


def _p18(entity: dict) -> str:
    claims = (entity.get("claims") or {}).get("P18") or []
    if not claims:
        return ""
    value = claims[0].get("mainsnak", {}).get("datavalue", {}).get("value")
    return _as_file(value) if isinstance(value, str) and value else ""


def _poi_index(geo: sqlite3.Connection) -> dict[tuple, dict]:
    index: dict[tuple, dict] = {}
    rows = geo.execute(
        "SELECT latitude, longitude, kind, tags FROM amenities "
        "WHERE tags LIKE '%\"wikimedia_commons\"%' OR tags LIKE '%\"wikidata\"%'"
    )
    for lat, lon, kind, text in rows:
        tags = json.loads(text)
        name = tags.get("name")
        if not name or not (tags.get("wikimedia_commons") or tags.get("wikidata")):
            continue
        index[(kind, round(float(lat), 5), round(float(lon), 5), name)] = tags
    return index


def _photos_on_hike(blob: bytes, index: dict[tuple, dict], client: CommonsClient) -> list[dict]:
    pois = []
    seen: set[tuple] = set()
    for point in json.loads(zlib.decompress(blob)):
        if len(point) < 7 or not point[6]:
            continue
        key = (point[0], round(float(point[2]), 5), round(float(point[3]), 5), point[6])
        if key in seen:
            continue
        tags = index.get(key)
        if tags is None:
            continue
        seen.add(key)
        pois.append(tags)
    return photos_for_hike(pois, client.one)


def export_photos(
    catalog_path: Path | str,
    out_dir: Path | str,
    cache_path: Path | str,
    *,
    opener: Opener | None = None,
    pause_s: float = PAUSE_S,
    progress: Progress | None = None,
) -> int:
    """Write each hike's photos into ``out_dir/hikes.sqlite``. Returns how many hikes got one.

    ``cache_path`` holds the API answers. An export with no ``photos`` column
    gains one; a hike with nothing free stores NULL, which the page ignores.
    """
    geo_path = geometry_store_path_for(catalog_path)
    hikes_path = Path(out_dir) / "hikes.sqlite"
    if not geo_path.is_file():
        raise FileNotFoundError(geo_path)
    if not hikes_path.is_file():
        raise FileNotFoundError(hikes_path)
    geo = sqlite3.connect(f"file:{geo_path}?mode=ro", uri=True)
    try:
        index = _poi_index(geo)
    finally:
        geo.close()
    client = CommonsClient(cache_path, opener, pause_s=pause_s, progress=progress)
    client.warm(index.values())
    db = sqlite3.connect(hikes_path)
    try:
        columns = {row[1] for row in db.execute("PRAGMA table_info(hikes)")}
        if "photos" not in columns:
            db.execute("ALTER TABLE hikes ADD COLUMN photos TEXT")
        rows = db.execute("SELECT id, points FROM hikes").fetchall()
        updates = []
        count = 0
        for done, (hike_id, blob) in enumerate(rows, start=1):
            photos = _photos_on_hike(blob, index, client)
            updates.append((json.dumps(photos, ensure_ascii=False) if photos else None, hike_id))
            count += bool(photos)
            if progress is not None and done % 2000 == 0:
                progress(f"photos: {done}/{len(rows)} DayHikes")
        db.executemany("UPDATE hikes SET photos = ? WHERE id = ?", updates)
        db.commit()
    finally:
        db.close()
    if progress is not None:
        progress(f"photos: {count} DayHikes with a photo")
    return count
