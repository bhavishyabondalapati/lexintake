"""Small admin commands.

python -m app.cli requeue                # re-queue documents stuck in `received`
python -m app.cli requeue --status failed  # retry failed ones (resets their attempt count)
"""

import argparse

from sqlalchemy import select

from app.db import session_scope
from app.models import Document, Status
from app.queue import enqueue_parse


def requeue(statuses: list[str]) -> int:
    with session_scope() as session:
        docs = session.scalars(select(Document).where(Document.status.in_(statuses))).all()
        for doc in docs:
            if doc.status == Status.FAILED:
                doc.retry_count = 0  # a manual retry gets a fresh set of attempts
        ids = [doc.id for doc in docs]
    for document_id in ids:
        enqueue_parse(document_id)
    return len(ids)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    rq = sub.add_parser("requeue", help="queue parsing again for documents in these statuses")
    rq.add_argument("--status", default="received", help="comma-separated, e.g. received,failed")
    args = parser.parse_args(argv)

    if args.command == "requeue":
        statuses = [s.strip() for s in args.status.split(",") if s.strip()]
        for s in statuses:
            Status(s)  # fail early on a typo
        print(f"queued {requeue(statuses)} document(s)")


if __name__ == "__main__":
    main()
