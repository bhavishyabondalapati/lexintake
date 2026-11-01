"""download_data.py tests. No network: the "remote" archive is a local file:// URL."""

import hashlib
import zipfile

import pytest

from scripts import download_data
from scripts.download_data import MARKER, ChecksumError, Dataset, fetch, safe_extract


def make_zip(path, entries):
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return path


@pytest.fixture
def remote(tmp_path):
    (tmp_path / "remote").mkdir()
    archive = make_zip(
        tmp_path / "remote" / "tiny.zip",
        {"tiny/train.json": '{"documents": []}', "tiny/LICENSE": "CC BY 4.0"},
    )
    return Dataset(
        name="tiny",
        url=archive.as_uri(),
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        archive_name="tiny.zip",
        strip_prefix="tiny/",
        license="CC BY 4.0",
    )


def test_download_verifies_and_extracts(remote, tmp_path):
    data = tmp_path / "data"

    assert fetch(remote, data) == "downloaded"

    assert (data / "tiny" / "train.json").read_text() == '{"documents": []}'
    assert (data / "tiny" / MARKER).exists()
    assert (data / "downloads" / "tiny.zip").exists()
    assert not list(data.glob("downloads/*.part"))


def test_second_run_skips_without_downloading(remote, tmp_path, monkeypatch):
    data = tmp_path / "data"
    fetch(remote, data)

    def no_network(*args):
        raise AssertionError("should not download again")

    monkeypatch.setattr(download_data, "download", no_network)

    assert fetch(remote, data) == "skipped"


def test_interrupted_extraction_is_redone_from_the_cached_archive(remote, tmp_path, monkeypatch):
    data = tmp_path / "data"
    fetch(remote, data)
    (data / "tiny" / MARKER).unlink()  # as if the last run died before finishing
    monkeypatch.setattr(download_data, "download", lambda *a: pytest.fail("re-downloaded"))

    assert fetch(remote, data) == "extracted"
    assert (data / "tiny" / MARKER).exists()


def test_wrong_checksum_extracts_nothing(remote, tmp_path):
    data = tmp_path / "data"
    bad = Dataset(**{**remote.__dict__, "sha256": "0" * 64})

    with pytest.raises(ChecksumError):
        fetch(bad, data)

    assert not (data / "tiny").exists()
    assert not list((data / "downloads").iterdir())


def test_zip_slip_entry_is_rejected(tmp_path):
    archive = make_zip(tmp_path / "evil.zip", {"../escaped.txt": "gotcha"})

    with pytest.raises(ValueError, match="unsafe path"):
        safe_extract(archive, tmp_path / "out")

    assert not (tmp_path / "escaped.txt").exists()
