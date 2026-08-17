from __future__ import annotations

import hashlib

import pytest

from datatown.hashing import sha256_file


@pytest.mark.parametrize("chunk_size", [1, 7, 1024 * 1024])
def test_sha256_file_streams_known_content(tmp_path, chunk_size: int) -> None:
    content = (b"Datatown preserves provenance.\n" * 100) + b"end"
    source = tmp_path / "source.bin"
    source.write_bytes(content)

    assert sha256_file(source, chunk_size=chunk_size) == hashlib.sha256(content).hexdigest()


def test_sha256_file_rejects_non_positive_chunk_size(tmp_path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"content")

    with pytest.raises(ValueError, match="greater than zero"):
        sha256_file(source, chunk_size=0)
