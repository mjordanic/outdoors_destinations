import mmap
from pathlib import Path

import numpy as np
import pytest

from open_files import open_under

from dayhike.npy import map_npy


@pytest.mark.parametrize(
    "array",
    [
        np.arange(12, dtype="<i4").reshape(3, 4),
        np.asfortranarray(np.arange(6, dtype="<f8").reshape(2, 3)),
        np.array(["ab", "cde"], dtype="<U3"),
        np.array([], dtype="<f4"),
    ],
)
def test_a_mapped_array_reads_what_np_load_reads_and_is_read_only(tmp_path: Path, array: np.ndarray) -> None:
    np.save(tmp_path / "a.npy", array)

    mapped = map_npy(tmp_path / "a.npy")

    np.testing.assert_array_equal(mapped, np.load(tmp_path / "a.npy"))
    assert mapped.dtype == array.dtype and mapped.flags.f_contiguous == np.load(tmp_path / "a.npy").flags.f_contiguous
    assert not mapped.flags.writeable


def test_a_mapped_array_lives_in_a_memory_map(tmp_path: Path) -> None:
    np.save(tmp_path / "a.npy", np.arange(100))

    mapped = map_npy(tmp_path / "a.npy")

    base = mapped
    while isinstance(base, np.ndarray):
        base = base.base
    assert isinstance(base, mmap.mmap)


def test_a_mapped_array_holds_no_file_descriptor(tmp_path: Path) -> None:
    np.save(tmp_path / "a.npy", np.arange(100))

    held = [map_npy(tmp_path / "a.npy") for _ in range(20)]

    assert open_under(tmp_path) == []
    assert sum(int(array.sum()) for array in held) == 20 * sum(range(100))


def test_an_object_array_is_refused(tmp_path: Path) -> None:
    np.save(tmp_path / "a.npy", np.array([{}, 1], dtype=object), allow_pickle=True)

    with pytest.raises(ValueError, match="object"):
        map_npy(tmp_path / "a.npy")
