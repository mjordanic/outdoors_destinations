"""Read-only memory maps of ``.npy`` files that hold no file descriptor."""

from __future__ import annotations

import math
import mmap
from pathlib import Path

import numpy as np


def map_npy(path: Path | str) -> np.ndarray:
    """The array in a ``.npy`` file, memory-mapped read-only.

    ``np.load(path, mmap_mode="r")`` keeps a duplicate of the file's descriptor
    open for as long as the map lives, so a process holding many stores (a
    test run's fixtures) runs out of descriptors. This maps with
    ``trackfd=False``: the descriptor closes when the file does.
    """
    with open(path, "rb") as file:
        version = np.lib.format.read_magic(file)
        read_header = np.lib.format.read_array_header_1_0 if version == (1, 0) else np.lib.format.read_array_header_2_0
        shape, fortran, dtype = read_header(file)
        if dtype.hasobject:
            raise ValueError(f"{path}: an object array cannot be memory-mapped")
        offset = file.tell()
        if math.prod(shape) == 0:  # mmap cannot map nothing
            array = np.empty(shape, dtype=dtype, order="F" if fortran else "C")
            array.flags.writeable = False
            return array
        buffer = mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ, trackfd=False)
    return np.ndarray(shape, dtype=dtype, buffer=buffer, offset=offset, order="F" if fortran else "C")
