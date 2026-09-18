from pathlib import Path

from dayhike.extracts import GEOFABRIK_EXTRACTS, fetch_geofabrik_extracts


def test_geofabrik_extract_list_matches_prd_regions() -> None:
    urls = {url.rstrip("/").split("/")[-1] for url in GEOFABRIK_EXTRACTS}
    assert urls == {
        "croatia-latest.osm.pbf",
        "slovenia-latest.osm.pbf",
        "bosnia-herzegovina-latest.osm.pbf",
        "montenegro-latest.osm.pbf",
        "austria-latest.osm.pbf",
        "switzerland-latest.osm.pbf",
        "nord-est-latest.osm.pbf",
        "bayern-latest.osm.pbf",
    }
    assert all(url.startswith("https://download.geofabrik.de/") for url in GEOFABRIK_EXTRACTS)


def test_fetch_geofabrik_extracts_writes_dest_files(tmp_path: Path) -> None:
    recorded: list[tuple[str, str]] = []

    def fake_retrieve(url: str, filename: str) -> tuple[str, None]:
        recorded.append((url, filename))
        Path(filename).write_bytes(b"pbf-bytes")
        return filename, None

    paths = fetch_geofabrik_extracts(tmp_path, urlretrieve=fake_retrieve)

    assert [path.name for path in paths] == [
        url.rsplit("/", 1)[1] for url in GEOFABRIK_EXTRACTS
    ]
    assert all(path.is_file() for path in paths)
    assert [url for url, _ in recorded] == list(GEOFABRIK_EXTRACTS)
