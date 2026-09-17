from dataclasses import replace
from pathlib import Path

import pytest

from dayhike import AmbiguousSeedName, UnknownSeedName, build_catalog, resolve_seed

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
RIDGE_WALK_ID = 1001
VALLEY_PATH_ID = 1002


def test_exact_fixture_dayhike_name_resolves_to_that_id(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")

    assert resolve_seed(catalog, "Ridge Walk") == RIDGE_WALK_ID
    assert resolve_seed(catalog, "Valley Path") == VALLEY_PATH_ID


def test_unknown_seed_name_raises_clear_error_not_empty_list(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")

    with pytest.raises(UnknownSeedName, match="Unknown Ridge") as raised:
        resolve_seed(catalog, "Unknown Ridge")

    assert "Unknown Ridge" in str(raised.value)


def test_ambiguous_seed_name_lists_candidates_instead_of_picking_one(
    tmp_path: Path,
) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    catalog = replace(
        catalog,
        dayhikes=tuple(replace(hike, name="Shared Path") for hike in catalog.dayhikes),
    )

    with pytest.raises(AmbiguousSeedName, match="Shared Path") as raised:
        resolve_seed(catalog, "Shared Path")

    assert raised.value.candidates == ("Shared Path", "Shared Path")


def test_case_folded_duplicate_names_are_ambiguous_and_list_catalog_names(
    tmp_path: Path,
) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    first, second = catalog.dayhikes
    catalog = replace(
        catalog,
        dayhikes=(replace(first, name="Shared Path"), replace(second, name="shared path")),
    )

    with pytest.raises(AmbiguousSeedName) as raised:
        resolve_seed(catalog, "SHARED PATH")

    assert raised.value.candidates == ("Shared Path", "shared path")
    assert "Shared Path" in str(raised.value)
    assert "shared path" in str(raised.value)


def test_seed_name_match_is_case_insensitive_exact_after_strip(
    tmp_path: Path,
) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")

    assert resolve_seed(catalog, "  ridge walk  ") == RIDGE_WALK_ID
    assert resolve_seed(catalog, "VALLEY PATH") == VALLEY_PATH_ID


def test_partial_seed_name_does_not_match(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")

    with pytest.raises(UnknownSeedName):
        resolve_seed(catalog, "Ridge")
