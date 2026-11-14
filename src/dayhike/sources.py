"""The sources register and the Source of each DayHike (ADR-0059).

``docs/sources.csv`` lists every data source the project uses, plans or
rejected, one row each. A DayHike's Source is a register id (``osm``) and its
``source_ref``, the record's own id there (``relation/123456``). Licence and
credit are looked up from the register, not copied into every row.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path

REGISTER_PATH = Path(__file__).resolve().parents[2] / "docs" / "sources.csv"

COLUMNS = (
    "id",
    "name",
    "kind",
    "coverage",
    "url",
    "licence",
    "licence_url",
    "odbl_compatible",
    "commercial_ok",
    "status",
    "reason",
    "checked",
    "issue",
)
KINDS = ("hikes", "elevation", "land cover", "climate", "places", "base map", "photos", "popularity")
ODBL_COMPATIBLE = ("yes", "separate layer only", "no")
COMMERCIAL_OK = ("yes", "no", "unknown")
STATUSES = ("used", "planned", "to check", "rejected", "watch")

SOURCE_OSM = "osm"


def read_register(path: Path | str = REGISTER_PATH) -> list[dict[str, str]]:
    """The register's rows, in file order."""
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def used_source_ids(path: Path | str = REGISTER_PATH) -> set[str]:
    """The ids of the register rows with status ``used``."""
    return {row["id"] for row in read_register(path) if row["status"] == "used"}


def require_used(source_ids: Sequence[str] | set[str], path: Path | str = REGISTER_PATH) -> None:
    """Refuse a Source id the register does not list as ``used``."""
    unknown = sorted(set(source_ids) - used_source_ids(path))
    if unknown:
        raise ValueError(f"source {', '.join(unknown)} is not listed as used in {path}")


def osm_source_ref(dayhike_id: int, kind: str, parts: Sequence[int], route: str | None) -> str:
    """The OSM record an OSM DayHike comes from: its relation, or for a built
    DayHike (ADR-0038) the one relation it is built from, else the node its
    Route starts at."""
    if kind == "relation":
        return f"relation/{dayhike_id}"
    if len(parts) == 1:
        return f"relation/{parts[0]}"
    if route:
        return f"node/{route.split(':', 1)[0]}"
    raise ValueError(f"DayHike {dayhike_id}: a built hike with no single relation and no Route")


def osm_record_url(source_ref: str) -> str:
    """The OpenStreetMap page of an ``osm`` source_ref."""
    return f"https://www.openstreetmap.org/{source_ref}"
