"""Crash test with real-world PDFs: send ContractNLI's original NDAs through the running stack.

ContractNLI's raw PDFs come from many different sources (EDGAR filings, company websites,
scans), so they exercise the parser far more than our synthetic set. This uploads a seeded
random sample to the API, also drops a few into the inbox for the folder watcher, waits for
every document to finish, and reports what happened to each one.

Needs the stack running (`make dev`) and `make data`. Reads ports and the DB password from .env.

    make ingest-smoke
    PYTHONPATH=backend uv run --project backend python -m scripts.ingest_smoke --n 20 --watch 3
"""

import argparse
import hashlib
import json
import random
import shutil
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "contractnli" / "raw"
REPORTS = ROOT / "data" / "reports"


def settings() -> tuple[str, str]:
    env = dotenv_values(ROOT / ".env")
    api = f"http://localhost:{env.get('API_PORT') or 8000}"
    db = (
        f"postgresql+psycopg://{env.get('POSTGRES_USER') or 'lexintake'}:"
        f"{env.get('POSTGRES_PASSWORD') or 'change-me'}@localhost:{env.get('DB_PORT') or 5432}/"
        f"{env.get('POSTGRES_DB') or 'lexintake'}"
    )
    return api, db


def wait_for(engine, shas: dict[str, str], timeout: float) -> dict[str, dict]:
    """Poll the database until every file's document is parsed or failed."""
    deadline = time.monotonic() + timeout
    while True:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT d.sha256, d.id, d.status, d.error, d.page_count, d.source,"
                    " (SELECT count(*) FROM text_blocks b WHERE b.document_id = d.id) AS blocks,"
                    " (SELECT a.detail FROM audit_log a WHERE a.document_id = d.id"
                    "   AND a.to_status = 'parsed' ORDER BY a.id DESC LIMIT 1) AS parsed"
                    " FROM documents d WHERE d.sha256 = ANY(:shas)"
                ),
                {"shas": list(shas.values())},
            ).mappings()
            found = {r["sha256"]: dict(r) for r in rows}
        done = [r for r in found.values() if r["status"] in ("parsed", "failed")]
        if len(done) == len(set(shas.values())) or time.monotonic() > deadline:
            return found
        time.sleep(3)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--n", type=int, default=20, help="PDFs to upload through the API")
    parser.add_argument("--watch", type=int, default=3, help="extra PDFs for the inbox watcher")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=1200)
    args = parser.parse_args()

    api, db_url = settings()
    pdfs = sorted(RAW.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"no PDFs in {RAW}; run `make data` first")
    sample = random.Random(args.seed).sample(pdfs, args.n + args.watch)
    upload, watch = sample[: args.n], sample[args.n :]
    shas = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sample}

    started = time.monotonic()
    http_errors = {}
    with httpx.Client(timeout=60) as client:
        for path in upload:
            pdf = (path.name, path.read_bytes(), "application/pdf")
            r = client.post(f"{api}/documents", files={"file": pdf})
            if r.status_code not in (200, 201):
                http_errors[path.name] = f"HTTP {r.status_code}: {r.text[:200]}"
    inbox = ROOT / "inbox"
    inbox.mkdir(exist_ok=True)
    for path in watch:
        shutil.copy(path, inbox / path.name)
    print(f"sent {len(upload)} via API, {len(watch)} via inbox; waiting for the worker...")

    engine = create_engine(db_url)
    found = wait_for(engine, {k: v for k, v in shas.items() if k not in http_errors}, args.timeout)
    elapsed = round(time.monotonic() - started, 1)

    results = []
    for path in sample:
        row = found.get(shas[path.name], {})
        parsed = row.get("parsed") or {}
        status = "rejected" if path.name in http_errors else row.get("status", "missing")
        results.append(
            {
                "file": path.name,
                "via": "inbox" if path in watch else "api",
                "size_kb": round(path.stat().st_size / 1024),
                "status": status,
                "pages": row.get("page_count"),
                "blocks": row.get("blocks"),
                "ocr_pages": len(parsed.get("ocr_pages", [])),
                "seconds": parsed.get("seconds"),
                "error": http_errors.get(path.name) or row.get("error"),
            }
        )

    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    lines = [
        f"# ContractNLI ingest smoke ({len(results)} PDFs, seed {args.seed}, {elapsed}s)",
        "",
        f"Status counts: {counts}",
        "",
        "| File | Via | KB | Status | Pages | Blocks | OCR pages | Seconds | Error |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        error = (r["error"] or "").replace("|", "/")[:100]
        lines.append(
            f"| {r['file'][:40]} | {r['via']} | {r['size_kb']} | {r['status']} | {r['pages']} | "
            f"{r['blocks']} | {r['ocr_pages']} | {r['seconds']} | {error} |"
        )
    report = "\n".join(lines)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "ingest_smoke.md").write_text(report + "\n")
    (REPORTS / "ingest_smoke.json").write_text(json.dumps(results, indent=2))
    print(report)


if __name__ == "__main__":
    main()
