"""Inbox folder watcher: drop a PDF into the inbox and it gets ingested.

A file being copied in (or scanned, or synced from SharePoint) shows up before it is complete,
so a file is only picked up once its size and modified-time have stayed the same for a couple
of checks. Afterwards it moves to .processed/ or .rejected/, so a restart doesn't ingest the
same folder again.

Run with: python -m app.watcher
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path

from watchfiles import Change, watch

from app.config import get_settings
from app.intake import IntakeRejected, IntakeResult, ingest_file
from app.models import IntakeSource
from app.queue import enqueue_parse
from app.storage.blobs import LocalBlobStore

log = logging.getLogger("lexintake.watcher")

PROCESSED = ".processed"
REJECTED = ".rejected"
# Partial downloads, editor/Office lock files and temp files.
IGNORED = [".*", "*.part", "*.crdownload", "*.tmp", "~$*"]


def is_candidate(inbox: Path, path: Path) -> bool:
    rel = path.relative_to(inbox)
    if any(part.startswith(".") for part in rel.parts[:-1]):
        return False  # inside .processed/, .rejected/ or another hidden folder
    return not any(fnmatch(path.name, pattern) for pattern in IGNORED)


@dataclass
class _Seen:
    size: int
    mtime: float
    same: int  # how many checks in a row it looked identical


class StabilityTracker:
    """Decides when a file has stopped changing."""

    def __init__(
        self, checks: int = 2, min_age_s: float = 2.0, clock: Callable[[], float] = time.time
    ) -> None:
        self.checks = checks
        self.min_age_s = min_age_s
        self.clock = clock
        self._seen: dict[Path, _Seen] = {}

    def is_stable(self, path: Path) -> bool:
        try:
            st = path.stat()
        except FileNotFoundError:
            self._seen.pop(path, None)
            return False
        prev = self._seen.get(path)
        if prev and prev.size == st.st_size and prev.mtime == st.st_mtime:
            prev.same += 1
        else:
            prev = self._seen[path] = _Seen(st.st_size, st.st_mtime, 0)
        old_enough = self.clock() - st.st_mtime >= self.min_age_s
        return prev.same >= self.checks and old_enough

    def forget(self, path: Path) -> None:
        self._seen.pop(path, None)


def _move(path: Path, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    dest = folder / f"{stamp}-{path.name}"
    n = 1
    while dest.exists():
        dest = folder / f"{stamp}-{n}-{path.name}"
        n += 1
    path.rename(dest)
    return dest


def handle(inbox: Path, path: Path, ingest: Callable[[Path], IntakeResult]) -> str:
    """Ingest one stable file and move it out of the way. Returns what happened."""
    try:
        result = ingest(path)
    except IntakeRejected as e:
        dest = _move(path, inbox / REJECTED)
        dest.with_name(dest.name + ".reason.txt").write_text(e.reason + "\n")
        log.warning("rejected %s: %s", path.name, e.reason)
        return "rejected"
    _move(path, inbox / PROCESSED)
    outcome = "duplicate" if result.duplicate else "ingested"
    log.info("%s %s -> document %s", outcome, path.name, result.document.id)
    return outcome


class InboxWatcher:
    def __init__(
        self,
        inbox: Path,
        ingest: Callable[[Path], IntakeResult],
        tracker: StabilityTracker | None = None,
    ) -> None:
        self.inbox = inbox
        self.ingest = ingest
        self.tracker = tracker or StabilityTracker()
        self.pending: set[Path] = set()

    def scan(self) -> None:
        """Pick up files that were already there (watchfiles only reports changes)."""
        for path in self.inbox.rglob("*"):
            if path.is_file() and is_candidate(self.inbox, path):
                self.pending.add(path)

    def add_changes(self, changes: set[tuple[Change, str]]) -> None:
        for change, raw in changes:
            path = Path(raw)
            if change == Change.deleted:
                self.pending.discard(path)
                self.tracker.forget(path)
            elif path.is_file() and is_candidate(self.inbox, path):
                self.pending.add(path)

    def check_pending(self) -> list[tuple[Path, str]]:
        done = []
        for path in sorted(self.pending):
            if not self.tracker.is_stable(path):
                if not path.exists():
                    self.pending.discard(path)
                continue
            try:
                outcome = handle(self.inbox, path, self.ingest)
            except Exception:
                # e.g. the database is down: leave the file where it is and try again later.
                log.exception("could not ingest %s, will retry", path.name)
                continue
            self.pending.discard(path)
            self.tracker.forget(path)
            done.append((path, outcome))
        return done

    def run(self, poll_s: float, force_polling: bool, stop: threading.Event | None = None) -> None:
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.scan()
        log.info("watching %s (%d file(s) already there)", self.inbox, len(self.pending))
        poll_ms = int(poll_s * 1000)
        # yield_on_timeout: wake up every poll even with no new events, to re-check pending files.
        for changes in watch(
            self.inbox,
            force_polling=force_polling,
            poll_delay_ms=poll_ms,
            rust_timeout=poll_ms,
            yield_on_timeout=True,
            stop_event=stop,
        ):
            self.add_changes(changes)
            self.check_pending()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("watchfiles").setLevel(logging.WARNING)  # it logs every change at INFO
    settings = get_settings()
    blobs = LocalBlobStore(settings.blob_dir)

    def ingest(path: Path) -> IntakeResult:
        return ingest_file(
            path,
            path.name,
            source=IntakeSource.WATCHER,
            actor="watcher",
            blobs=blobs,
            enqueue=enqueue_parse,
            max_bytes=settings.max_upload_mb * 1024 * 1024,
        )

    InboxWatcher(Path(settings.inbox_dir), ingest).run(
        settings.watch_poll_s, settings.watch_force_polling
    )


if __name__ == "__main__":
    main()
