"""The ODbL download: the OpenStreetMap part of the hike database, as a free file (issue #122).

``export_osm_download`` reads a serving export (``dayhike.serving``) and writes into an
output directory:

- ``hikes-osm.sqlite``: the hikes whose ``source`` is OpenStreetMap, with only what comes
  from OpenStreetMap or from its geometry: id, name and its origin, kind, route and parts,
  the OSM ``source_ref``, ``sac_scale``, the line (WKT, simplified to about
  ``serving.LINE_TOLERANCE_M``) and its length, the Trailhead, and in ``hike_points`` the
  OSM amenity points along the line and at the Trailhead (parking, cafes, water, huts,
  peaks, shelters, viewpoints) with their OSM names. A ``licence`` table holds the ODbL
  notice, the attribution, the data date and what the file leaves out.
- ``README.txt``: the same notice for a reader who has not opened the database.

Heights (and everything read from them), land cover, climate, GeoNames places and Landmarks,
and every external source's hikes are not in it: they come from other sources with their own
licences (``DATA-LICENSES.md``), and the OSMF Collective Database guideline lets them stay
out of a share-alike database. A peak's or hut's ``ele`` tag is left out too, as a height.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
import zlib
from pathlib import Path

import numpy as np

from dayhike.catalog import OSM_ATTRIBUTION
from dayhike.serving import POINT_COLUMNS, decode_polyline
from dayhike.sources import SOURCE_OSM

DOWNLOAD_NAME = "hikes-osm.sqlite"
README_NAME = "README.txt"
LICENCE_NAME = "Open Database License (ODbL) 1.0"
LICENCE_URL = "https://opendatacommons.org/licenses/odbl/1-0/"
COPYRIGHT_URL = "https://www.openstreetmap.org/copyright"
CONTENTS = (
    "The hikes made from OpenStreetMap: their names, kinds, routes, lines and the OpenStreetMap points "
    "along them (parking, cafes, water, huts, peaks, shelters, viewpoints). "
    "Left out, as they come from other sources under other licences: heights and everything derived from "
    "them (gain, profiles), land cover, climate, GeoNames places and Landmarks, and hikes from other "
    "sources than OpenStreetMap."
)

_HIKE_COLUMNS = (
    "id, name, name_origin, kind, route, parts, source_ref, sac_scale, walk_m, line"
)


def export_osm_download(serving_dir: Path | str, out_dir: Path | str, data_date: str | None = None) -> Path:
    """Write the ODbL download of the serving export ``serving_dir`` into ``out_dir``; return ``out_dir``.

    ``data_date`` (``YYYY-MM-DD``) is the date of the OpenStreetMap data the export was built from;
    without it, the day the export's ``meta.json`` was written. A rerun replaces the files.
    """
    serving, out = Path(serving_dir), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if data_date is None:
        written = (serving / "meta.json").stat().st_mtime
        data_date = datetime.datetime.fromtimestamp(written, datetime.timezone.utc).date().isoformat()
    trailheads = dict(
        zip(
            np.load(serving / "similarity" / "ids.npy").tolist(),
            map(tuple, np.load(serving / "trailheads.npy").reshape(-1, 2).tolist()),
        )
    )
    path, partial = out / DOWNLOAD_NAME, out / f"{DOWNLOAD_NAME}.partial"
    partial.unlink(missing_ok=True)
    source = sqlite3.connect(f"file:{serving / 'hikes.sqlite'}?mode=ro&immutable=1", uri=True)
    db = sqlite3.connect(partial)
    try:
        db.executescript(
            "CREATE TABLE hikes (id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_origin TEXT NOT NULL,"
            " kind TEXT NOT NULL, route TEXT, parts TEXT, source_ref TEXT NOT NULL, sac_scale TEXT, walk_m REAL,"
            " trailhead_lat REAL, trailhead_lon REAL, geometry_wkt TEXT);"
            "CREATE TABLE hike_points (hike INTEGER NOT NULL, kind TEXT NOT NULL, place TEXT NOT NULL,"
            " lat REAL NOT NULL, lon REAL NOT NULL, along_m INTEGER, off_m INTEGER, name TEXT);"
            "CREATE INDEX hike_points_hike ON hike_points (hike);"
            "CREATE TABLE licence (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;"
        )
        for hike_id, *fields, line in source.execute(
            f"SELECT {_HIKE_COLUMNS} FROM hikes WHERE source = ? ORDER BY id", (SOURCE_OSM,)
        ):
            lat, lon = trailheads.get(hike_id, (None, None))
            db.execute(
                "INSERT INTO hikes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (hike_id, *fields, lat, lon, _wkt(line)),
            )
        for hike_id, blob in source.execute("SELECT id, points FROM hikes WHERE source = ? ORDER BY id", (SOURCE_OSM,)):
            db.executemany(
                "INSERT INTO hike_points VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (hike_id, p["kind"], p["where"], p["lat"], p["lon"], p["along_m"], p["off_m"], p["name"])
                    for p in (dict(zip(POINT_COLUMNS, point)) for point in json.loads(zlib.decompress(blob)))
                ],
            )
        db.executemany(
            "INSERT INTO licence VALUES (?, ?)",
            [
                ("licence", LICENCE_NAME),
                ("licence_url", LICENCE_URL),
                ("attribution", OSM_ATTRIBUTION),
                ("copyright_url", COPYRIGHT_URL),
                ("data_date", data_date),
                ("contents", CONTENTS),
            ],
        )
        db.commit()
    finally:
        source.close()
        db.close()
    partial.replace(path)
    (out / README_NAME).write_text(_readme(data_date), encoding="utf-8")
    return out


def _wkt(line: str | None) -> str | None:
    if not line:
        return None
    return "LINESTRING(" + ", ".join(f"{lon:.5f} {lat:.5f}" for lat, lon in decode_polyline(line)) + ")"


def _readme(data_date: str) -> str:
    return f"""HikesAlike: the OpenStreetMap hikes (ODbL)

{DOWNLOAD_NAME} is the OpenStreetMap part of the HikesAlike hike database, offered free of charge.

Licence:     {LICENCE_NAME}, {LICENCE_URL}
Attribution: {OSM_ATTRIBUTION} ({COPYRIGHT_URL})
Data date:   {data_date} (the OpenStreetMap data the hikes were built from)

This is a derivative database of OpenStreetMap. If you use it, credit
"{OSM_ATTRIBUTION}", and offer any database you derive from it under the ODbL too.

What is in it ({DOWNLOAD_NAME}, an SQLite database):
  hikes        one row per hike made from OpenStreetMap: id, name, name_origin, kind
               (relation or a built walk), route, parts, source_ref (the OpenStreetMap
               record), sac_scale, walk_m (metres), the Trailhead (trailhead_lat,
               trailhead_lon) and geometry_wkt, the line as a WKT LINESTRING in
               longitude-latitude order, simplified to about 10 m.
  hike_points  the OpenStreetMap points along each hike's line (place = 'line') and
               near its Trailhead (place = 'trailhead'): kind, lat, lon, along_m and
               off_m (metres along and off the line), name.
  licence      the notice above, and what is left out.

What is left out, because it comes from other sources under other licences: heights and
everything derived from them, land cover, climate, GeoNames places and Landmarks, and
hikes from sources other than OpenStreetMap.

Code and project: https://github.com/mjordanic/outdoors_destinations (MIT licence for the code only).
"""
