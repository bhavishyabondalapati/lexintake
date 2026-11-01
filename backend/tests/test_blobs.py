import hashlib

import pytest

from app.storage.blobs import LocalBlobStore, sha256_file


def test_put_stores_file_under_its_hash(tmp_path):
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF-1.7 hello")
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    store = LocalBlobStore(tmp_path / "blobs")

    store.put(src, sha)

    assert store.path(sha) == tmp_path / "blobs" / sha[:2] / sha[2:4] / f"{sha}.pdf"
    assert store.open(sha).read() == b"%PDF-1.7 hello"
    assert store.exists(sha)


def test_put_twice_is_a_no_op(tmp_path):
    src = tmp_path / "a.pdf"
    src.write_bytes(b"same bytes")
    sha = sha256_file(src)
    store = LocalBlobStore(tmp_path / "blobs")

    store.put(src, sha)
    mtime = store.path(sha).stat().st_mtime_ns
    store.put(src, sha)

    assert store.path(sha).stat().st_mtime_ns == mtime


def test_put_rejects_a_wrong_hash_and_leaves_nothing_behind(tmp_path):
    src = tmp_path / "a.pdf"
    src.write_bytes(b"bytes")
    store = LocalBlobStore(tmp_path / "blobs")
    wrong = "0" * 64

    with pytest.raises(ValueError):
        store.put(src, wrong)

    assert not store.exists(wrong)
    assert list(store.path(wrong).parent.iterdir()) == []  # no leftover temp file


def test_path_rejects_anything_but_a_hex_digest(tmp_path):
    with pytest.raises(ValueError):
        LocalBlobStore(tmp_path).path("../../etc/passwd")
