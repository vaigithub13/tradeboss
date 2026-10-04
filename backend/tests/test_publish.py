"""A download is published only after the temp file is accepted."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.data.publish import publish_file


def test_a_rejected_or_interrupted_write_leaves_the_destination_and_no_temp(tmp_path: Path) -> None:
    path = tmp_path / "candles" / "1m.parquet"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"kept")

    def partial(tmp: Path) -> None:
        tmp.write_bytes(b"partial")
        raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        publish_file(path, partial, accept=lambda _p: True)
    assert path.read_bytes() == b"kept"
    assert not path.with_name(path.name + ".tmp").exists()

    assert publish_file(path, lambda tmp: tmp.write_bytes(b""), accept=lambda _p: True) is False
    assert path.read_bytes() == b"kept"
    assert not path.with_name(path.name + ".tmp").exists()


def test_an_accepted_write_replaces_the_destination_and_removes_the_temp(tmp_path: Path) -> None:
    path = tmp_path / "NSE.json.gz"

    def write(tmp: Path) -> None:
        tmp.write_bytes(b"gzip-bytes")

    assert publish_file(path, write, accept=lambda p: p.read_bytes() == b"gzip-bytes")
    assert path.read_bytes() == b"gzip-bytes"
    assert not path.with_name(path.name + ".tmp").exists()
