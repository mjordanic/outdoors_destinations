"""Regions: the area a build covers, as configuration rather than code.

A region is a TOML file: a ``label`` for people, the Geofabrik ``extracts``
(PBF URLs) that ``fetch-extracts`` downloads, the ``bounds`` box that
``fetch-dem``, ``fetch-landcover`` and ``fetch-climate`` cover, and a ``map``
table, the coverage map's ``view`` box and its projection ``centre``. The
regions that come with the package are in ``dayhike/data/regions/``; a path to
any other TOML file works the same. A build of several regions takes the
union of their extracts and the box around all their bounds.

Boxes run west to east: a region crossing the 180° meridian (Alaska's
Aleutians, Fiji) is not supported.
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from dayhike.dem import Bounds

REGIONS_DIR = Path(__file__).parent / "data" / "regions"
DEFAULT_REGION = "alps-dinarides"  # what the fetch commands read without --region
DEFAULT_MAP_REGION = "europe"  # what coverage-map draws without --region


class RegionError(ValueError):
    """A region file is missing or does not describe a region."""


@dataclass(frozen=True)
class Region:
    name: str
    label: str
    extracts: tuple[str, ...]
    bounds: Bounds
    view: Bounds  # the coverage map's frame
    centre: tuple[float, float]  # the coverage map's projection centre (lon, lat)


def region_names() -> list[str]:
    """The regions that come with the package."""
    return sorted(path.stem for path in REGIONS_DIR.glob("*.toml"))


def load_region(name_or_path: str | Path) -> Region:
    """A packaged region by name (``europe``), or a region TOML file by path."""
    path = Path(name_or_path)
    if path.suffix != ".toml":
        path = REGIONS_DIR / f"{name_or_path}.toml"
        if not path.is_file():
            raise RegionError(f"no region {name_or_path!r}: one of {', '.join(region_names())}, or a .toml file")
    try:
        data = tomllib.loads(path.read_text())
        region = Region(
            name=path.stem,
            label=str(data["label"]),
            extracts=tuple(str(url) for url in data["extracts"]),
            bounds=_box(data["bounds"]),
            view=_box(data["map"]["view"]),
            centre=(float(data["map"]["centre"]["lon"]), float(data["map"]["centre"]["lat"])),
        )
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError, ValueError) as exc:
        raise RegionError(f"{path}: {exc}") from exc
    return region


def bounds_around(regions: Sequence[Region]) -> Bounds:
    """The box around every region's bounds."""
    return _around([region.bounds for region in regions])


def view_around(regions: Sequence[Region]) -> Bounds:
    """The box around every region's map view."""
    return _around([region.view for region in regions])


def extracts_of(regions: Sequence[Region]) -> list[str]:
    """Every region's extracts, each once, in order."""
    return list(dict.fromkeys(url for region in regions for url in region.extracts))


def _box(table: dict) -> Bounds:
    box = Bounds(
        south=float(table["south"]), west=float(table["west"]), north=float(table["north"]), east=float(table["east"])
    )
    if not (-180 <= box.west < box.east <= 180):
        raise ValueError(f"west {box.west} must be less than east {box.east}, within ±180 (a box crossing 180° is not supported)")
    if not (-90 <= box.south < box.north <= 90):
        raise ValueError(f"south {box.south} must be less than north {box.north}, within ±90")
    return box


def _around(boxes: Sequence[Bounds]) -> Bounds:
    return Bounds(
        south=min(box.south for box in boxes),
        west=min(box.west for box in boxes),
        north=max(box.north for box in boxes),
        east=max(box.east for box in boxes),
    )
