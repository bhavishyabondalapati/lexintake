"""Inbox watcher: only stable files are picked up, and they are moved out afterwards."""

import os
import uuid

import pytest
from watchfiles import Change

from app.intake import IntakeRejected, IntakeResult
from app.watcher import PROCESSED, REJECTED, InboxWatcher, StabilityTracker, is_candidate


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeDoc:
    id = uuid.UUID(int=1)


@pytest.fixture
def inbox(tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    return folder


def _write(path, data: bytes, mtime: float) -> None:
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))


def test_ignores_temp_files_and_hidden_folders(inbox):
    assert is_candidate(inbox, inbox / "nda.pdf")
    assert is_candidate(inbox, inbox / "sub" / "nda.pdf")
    for name in [".hidden.pdf", "big.pdf.part", "x.crdownload", "~$lock.pdf", "a.tmp"]:
        assert not is_candidate(inbox, inbox / name)
    assert not is_candidate(inbox, inbox / PROCESSED / "nda.pdf")


def test_a_growing_file_is_not_stable_until_it_stops_changing(inbox):
    path = inbox / "scan.pdf"
    clock = FakeClock(1000.0)
    tracker = StabilityTracker(checks=2, min_age_s=2.0, clock=clock)

    _write(path, b"%PDF-1", mtime=999.0)
    assert not tracker.is_stable(path)  # first sighting
    _write(path, b"%PDF-1 more", mtime=1000.0)
    assert not tracker.is_stable(path)  # grew: start counting again
    clock.now = 1001.0
    assert not tracker.is_stable(path)  # same once
    clock.now = 1002.0
    assert tracker.is_stable(path)  # same twice and 2 s old


def test_a_file_must_also_be_old_enough(inbox):
    path = inbox / "scan.pdf"
    clock = FakeClock(1000.0)
    tracker = StabilityTracker(checks=1, min_age_s=5.0, clock=clock)
    _write(path, b"%PDF-1", mtime=999.0)

    tracker.is_stable(path)
    assert not tracker.is_stable(path)  # unchanged, but only 1 s old
    clock.now = 1004.0
    assert tracker.is_stable(path)


def _watcher(inbox, ingest):
    # checks=0, min_age 0: everything is stable at once, so these tests focus on what happens next.
    return InboxWatcher(inbox, ingest, StabilityTracker(checks=0, min_age_s=0))


def test_stable_file_is_ingested_and_moved_to_processed(inbox):
    seen = []

    def ingest(path):
        seen.append(path.read_bytes())
        return IntakeResult(FakeDoc(), duplicate=False)

    (inbox / "nda.pdf").write_bytes(b"%PDF-1.7")
    watcher = _watcher(inbox, ingest)
    watcher.scan()

    assert watcher.check_pending() == [(inbox / "nda.pdf", "ingested")]
    assert seen == [b"%PDF-1.7"]
    assert not (inbox / "nda.pdf").exists()
    [moved] = (inbox / PROCESSED).iterdir()
    assert moved.name.endswith("-nda.pdf")


def test_duplicate_also_moves_to_processed(inbox):
    (inbox / "copy.pdf").write_bytes(b"%PDF-1.7")
    watcher = _watcher(inbox, lambda p: IntakeResult(FakeDoc(), duplicate=True))
    watcher.add_changes({(Change.added, str(inbox / "copy.pdf"))})

    assert watcher.check_pending() == [(inbox / "copy.pdf", "duplicate")]
    assert len(list((inbox / PROCESSED).iterdir())) == 1


def test_rejected_file_moves_to_rejected_with_the_reason(inbox):
    def ingest(path):
        raise IntakeRejected("not a PDF (no %PDF- header)", 415)

    (inbox / "notes.docx").write_bytes(b"PK")
    watcher = _watcher(inbox, ingest)
    watcher.scan()

    assert watcher.check_pending() == [(inbox / "notes.docx", "rejected")]
    files = sorted(p.name for p in (inbox / REJECTED).iterdir())
    assert files[0].endswith("-notes.docx")
    assert files[1].endswith("-notes.docx.reason.txt")
    assert "not a PDF" in (inbox / REJECTED / files[1]).read_text()


def test_file_stays_put_when_ingestion_errors_and_is_retried(inbox):
    calls = []

    def ingest(path):
        calls.append(path)
        if len(calls) == 1:
            raise ConnectionError("database is down")
        return IntakeResult(FakeDoc(), duplicate=False)

    (inbox / "nda.pdf").write_bytes(b"%PDF-1.7")
    watcher = _watcher(inbox, ingest)
    watcher.scan()

    assert watcher.check_pending() == []
    assert (inbox / "nda.pdf").exists()
    assert watcher.check_pending() == [(inbox / "nda.pdf", "ingested")]


def test_deleted_file_is_forgotten(inbox):
    watcher = _watcher(inbox, lambda p: IntakeResult(FakeDoc(), duplicate=False))
    (inbox / "gone.pdf").write_bytes(b"%PDF")
    watcher.scan()
    (inbox / "gone.pdf").unlink()
    watcher.add_changes({(Change.deleted, str(inbox / "gone.pdf"))})

    assert watcher.pending == set()
