"""Publish a downloaded file only after the bytes are complete.

The write goes to a sibling ``*.tmp``. That temp file replaces the destination
only when ``accept`` says it is usable. A crash, a raised error, or a rejected
file deletes the temp and leaves the destination as it was, so an interrupted
fetch cannot leave an empty or partial file in its place.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


def publish_file(path: Path, write: Callable[[Path], None], *, accept: Callable[[Path], bool]) -> bool:
    """Write ``path`` via a temp file. Returns whether the destination was replaced."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        write(tmp)
        if not tmp.is_file() or tmp.stat().st_size == 0 or not accept(tmp):
            return False
        os_replace(tmp, path)
        return True
    finally:
        if tmp.exists():
            tmp.unlink()


def os_replace(src: Path, dst: Path) -> None:
    import os

    os.replace(src, dst)
