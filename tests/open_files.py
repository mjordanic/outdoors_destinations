"""Which files under a directory this process holds open, so a leak test ignores unrelated descriptors."""

import fcntl
import os
import sys
from pathlib import Path


def _target(fd: int) -> str | None:
    try:
        if sys.platform == "darwin":
            return fcntl.fcntl(fd, fcntl.F_GETPATH, b"\0" * 1024).split(b"\0", 1)[0].decode()
        return os.readlink(f"/proc/self/fd/{fd}")
    except OSError:  # closed meanwhile, or the listing's own descriptor
        return None


def open_under(root: Path) -> list[str]:
    """Paths under ``root`` that this process holds a file descriptor for."""
    prefix = str(root.resolve()) + os.sep
    targets = (_target(int(fd)) for fd in os.listdir("/dev/fd"))
    return sorted(t for t in targets if t is not None and t.startswith(prefix))
