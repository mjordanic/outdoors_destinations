from __future__ import annotations

import argparse
from pathlib import Path

from dayhike.catalog import Catalog, rank_dayhikes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dayhike")
    subparsers = parser.add_subparsers(dest="command", required=True)
    rank = subparsers.add_parser("rank", help="Rank DayHikes by similarity to Seeds")
    rank.add_argument("--catalog", required=True, type=Path)
    rank.add_argument("seeds", nargs="+", help="Seed DayHike names")
    args = parser.parse_args(argv)
    return _rank(args.catalog, args.seeds)


def _rank(catalog_path: Path, seeds: list[str]) -> int:
    ranking = rank_dayhikes(Catalog.open(catalog_path), seeds)
    for item in ranking.items:
        print(f"{item.dayhike.id}\t{item.dayhike.name}\t{item.score}")
    return 0
