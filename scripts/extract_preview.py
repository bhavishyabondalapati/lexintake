"""Preview of classification + extraction on 20 documents, against the manifest's ground truth.

1. Picks a fixed, stratified sample: 8 NDAs (3 digital, 1 with an unusual title, 2 scanned, plus
   the prompt-injection NDA and the angle-bracket NDA from the test fixtures), and 3 each of side
   letters, investor notices, legal notices and other (one scanned in each). 14 digital, 6 scanned.
2. Uploads them to the running stack (dedupe makes re-runs safe) and waits until they're parsed.
3. Runs the extraction step on them, with the same code as the worker and a hard spend cap.
   THIS CALLS THE ANTHROPIC API. `--dry-run` stops before that and prints the worst case.
4. Writes data/reports/extract_preview.{md,json}: classification accuracy, per-field exact match,
   NDA recall, span verification, review reasons, cache use and cost per document per model,
   all split digital vs scanned.

Needs the stack running (`make dev`), `make synthetic`, and the model settings in .env.

    make extract-preview                                  # MAX_SPEND=1.00 by default
    PYTHONPATH=backend uv run --project backend python -m scripts.extract_preview --dry-run
    ... extract_preview --report-only --run <run id>      # rebuild the report, no API calls
    ... extract_preview --report-only --record            # also save replay test fixtures
"""

import argparse
import json
import os
import random
import time
import uuid
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "data" / "synthetic"
FIXTURES = ROOT / "backend" / "tests" / "fixtures"
REPORTS = ROOT / "data" / "reports"
RECORD_DIR = FIXTURES / "llm"
SEED = 42

# How many of each kind to pick: (doc_type, scanned) -> count.
PLAN = {
    ("nda", False): 3,
    ("nda", True): 2,
    ("side_letter", False): 2,
    ("side_letter", True): 1,
    ("investor_notice", False): 2,
    ("investor_notice", True): 1,
    ("legal_notice", False): 2,
    ("legal_notice", True): 1,
    ("other", False): 2,
    ("other", True): 1,
}
UNUSUAL_TITLE_NDA = "syn_00042"
SPECIAL = ["injection_nda", "angle_brackets_nda"]


def _env() -> dict[str, str]:
    return {k: v or "" for k, v in dotenv_values(ROOT / ".env").items()}


def _use_local_database() -> None:
    """The app's settings read .env, where the database host is `db` (inside Docker). From the
    Mac it's localhost; env vars win over .env, so point DATABASE_URL there before importing."""
    env = _env()
    os.environ["DATABASE_URL"] = (
        f"postgresql://{env.get('POSTGRES_USER') or 'lexintake'}:"
        f"{env.get('POSTGRES_PASSWORD') or 'change-me'}@localhost:{env.get('DB_PORT') or 5432}/"
        f"{env.get('POSTGRES_DB') or 'lexintake'}"
    )


# --- 1. Sample ----------------------------------------------------------------------------------


def pick_sample() -> list[dict]:
    manifest = json.loads((SYNTHETIC / "manifest.json").read_text())["documents"]
    special = json.loads((FIXTURES / "special_fixtures.json").read_text())["documents"]
    rng = random.Random(SEED)
    plain = [d for d in manifest if not d["tricky"]]
    sample = [next(d for d in manifest if d["doc_id"] == UNUSUAL_TITLE_NDA)]
    for (doc_type, scanned), count in PLAN.items():
        pool = [d for d in plain if d["doc_type"] == doc_type and d["scanned"] == scanned]
        if doc_type in ("investor_notice", "legal_notice", "other"):
            # Spread over subtypes: one per subtype first, then random.
            by_subtype = defaultdict(list)
            for d in pool:
                by_subtype[d["subtype"]].append(d)
            picks = [rng.choice(by_subtype[s]) for s in sorted(by_subtype)]
            rng.shuffle(picks)
            sample += picks[:count]
        else:
            sample += rng.sample(pool, count)
    for entry in special:
        sample.append(entry | {"path": str(FIXTURES / entry["file"])})
    for d in sample:
        d.setdefault("path", str(SYNTHETIC / d["file"]))
    return sample


# --- 2. Upload and wait for parsing -------------------------------------------------------------


def upload_and_parse(sample: list[dict], api: str, timeout: float = 600) -> dict[str, uuid.UUID]:
    ids = {}
    with httpx.Client(base_url=api, timeout=60) as client:
        for d in sample:
            path = Path(d["path"])
            with path.open("rb") as f:
                r = client.post("/documents", files={"file": (path.name, f, "application/pdf")})
            r.raise_for_status()
            ids[d["doc_id"]] = uuid.UUID(r.json()["id"])
        deadline = time.monotonic() + timeout
        while True:
            statuses = {k: client.get(f"/documents/{v}").json()["status"] for k, v in ids.items()}
            waiting = [k for k, s in statuses.items() if s in ("received", "parsing")]
            if not waiting:
                break
            if time.monotonic() > deadline:
                raise SystemExit(f"still not parsed after {timeout:.0f}s: {waiting}")
            print(f"  waiting for {len(waiting)} document(s) to be parsed...")
            time.sleep(5)
    failed = [k for k, s in statuses.items() if s == "failed"]
    if failed:
        raise SystemExit(f"parsing failed for {failed}; fix that before extracting")
    return ids


# --- 3. Extract ---------------------------------------------------------------------------------


def worst_case(ids: dict[str, uuid.UUID], sample: list[dict]) -> Decimal:
    """What the run could cost at most, if every document needed both calls twice over."""
    from app.config import get_settings
    from app.db import session_scope
    from app.extraction.base import plan_classify, plan_extract
    from app.extraction.doctext import load_doc_text
    from app.extraction.schemas import DocType, Subtype

    settings = get_settings()
    total = Decimal(0)
    with session_scope() as session:
        for d in sample:
            doc = load_doc_text(session, ids[d["doc_id"]])
            subtype = Subtype(d["subtype"]) if d["subtype"] else None
            calls = [
                plan_classify(settings, doc),
                plan_extract(settings, doc, DocType(d["doc_type"]), subtype),
            ]
            total += sum(c.worst_case_usd for c in calls)
    return total


def run_extraction(ids: dict[str, uuid.UUID], max_spend: Decimal) -> uuid.UUID:
    from app.cli import _print_run, extract
    from app.config import get_settings
    from app.extraction.anthropic_extractor import AnthropicExtractor

    settings = get_settings()
    run = extract(
        AnthropicExtractor(settings), settings, max_spend=max_spend, doc_ids=list(ids.values())
    )
    _print_run(run)
    return run.run_id


# --- 4. Report ----------------------------------------------------------------------------------


def _load_results(ids: dict[str, uuid.UUID], run_id: uuid.UUID | None) -> tuple[dict, dict]:
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Document, Extraction, LlmCall

    with session_scope() as session:
        docs = {
            d.id: d for d in session.scalars(select(Document).where(Document.id.in_(ids.values())))
        }
        extractions = {
            e.document_id: e
            for e in session.scalars(
                select(Extraction).where(Extraction.document_id.in_(ids.values()))
            )
        }
        query = select(LlmCall).where(LlmCall.document_id.in_(ids.values()))
        if run_id:
            query = query.where(LlmCall.run_id == run_id)
        calls = defaultdict(list)
        for c in session.scalars(query.order_by(LlmCall.id)):
            calls[c.document_id].append(c)
    return {k: (docs[v], extractions.get(v)) for k, v in ids.items()}, calls


def _pct(hits: int, total: int) -> str:
    return f"{hits}/{total} ({100 * hits / total:.0f}%)" if total else "n/a"


def build_report(sample: list[dict], ids: dict[str, uuid.UUID], run_id: uuid.UUID | None) -> dict:
    from app.config import get_settings

    settings = get_settings()
    results, calls = _load_results(ids, run_id)
    per_doc = []
    fields_by_group: dict[str, Counter] = defaultdict(Counter)  # group -> hits/total
    field_rows: dict[tuple[str, str], Counter] = defaultdict(Counter)  # (type, field)
    conf_buckets: Counter = Counter()
    review_codes: Counter = Counter()
    absent_flags = []
    cost_rows = defaultdict(lambda: defaultdict(Decimal))  # doc -> model -> cost
    tokens = defaultdict(Counter)  # step/model -> tokens
    latency = defaultdict(list)

    for d in sample:
        doc, extraction = results[d["doc_id"]]
        group = "scanned" if d["scanned"] else "digital"
        cls = (extraction.classification if extraction else {}) or {}
        type_ok = extraction is not None and extraction.doc_type == d["doc_type"]
        subtype_ok = type_ok and extraction.doc_subtype == d["subtype"]
        reasons = extraction.review_reasons if extraction else []
        review_codes.update({r["code"] for r in reasons})
        for r in reasons:
            if r["code"] == "absent_but_mentioned":
                absent_flags.append(
                    {"doc_id": d["doc_id"], "field": r["field"], "detail": r["detail"]}
                )

        field_results = {}
        for name, truth in d["fields"].items():
            got = (extraction.fields.get(name) if extraction and subtype_ok else None) or {}
            hit = subtype_ok and name in extraction.fields and got.get("value") == truth
            field_results[name] = {
                "truth": truth,
                "got": got.get("value"),
                "hit": hit,
                "status": got.get("status"),
                "confidence": got.get("confidence"),
            }
            fields_by_group[group]["total"] += 1
            fields_by_group[group]["hits"] += hit
            fields_by_group["all"]["total"] += 1
            fields_by_group["all"]["hits"] += hit
            field_rows[(d["doc_type"], name)]["total"] += 1
            field_rows[(d["doc_type"], name)]["hits"] += hit
            if got:
                ocr = got.get("source") == "ocr"
                threshold = (
                    settings.ocr_field_min_confidence if ocr else settings.field_min_confidence
                )
                bucket = "above" if got["confidence"] >= threshold else "below"
                conf_buckets[f"{bucket}_total"] += 1
                conf_buckets[f"{bucket}_hits"] += hit
        for c in calls[ids[d["doc_id"]]]:
            cost_rows[d["doc_id"]][c.model] += c.cost_usd
            key = f"{c.step} / {c.model}"
            tokens[key].update(
                calls=1,
                input=c.input_tokens,
                output=c.output_tokens,
                cache_write=c.cache_write_tokens,
                cache_read=c.cache_read_tokens,
            )
            tokens[key]["cost_micro_usd"] += int(c.cost_usd * 1_000_000)
            latency[c.step].append(c.latency_ms)
        per_doc.append(
            {
                "doc_id": d["doc_id"],
                "group": group,
                "truth_type": d["doc_type"],
                "truth_subtype": d["subtype"],
                "got_type": extraction.doc_type if extraction else None,
                "got_subtype": extraction.doc_subtype if extraction else None,
                "type_ok": type_ok,
                "subtype_ok": subtype_ok,
                "classification_confidence": cls.get("confidence"),
                "status": doc.status,
                "reasons": reasons,
                "fields": field_results,
                "cost_usd": {m: str(v) for m, v in cost_rows[d["doc_id"]].items()},
            }
        )

    def accuracy(key: str, group: str | None = None) -> str:
        rows = [r for r in per_doc if group is None or r["group"] == group]
        return _pct(sum(r[key] for r in rows), len(rows))

    ndas = [r for r in per_doc if r["truth_type"] == "nda"]
    nda_recall = {
        "classified_nda": _pct(sum(r["got_type"] == "nda" for r in ndas), len(ndas)),
        "caught_by_nda_guard": sum(
            any(x["code"] == "nda_signals_but_not_nda" for x in r["reasons"]) for r in ndas
        ),
        "missed": [
            r["doc_id"]
            for r in ndas
            if r["got_type"] != "nda"
            and not any(x["code"] == "nda_signals_but_not_nda" for x in r["reasons"])
        ],
    }
    verified = Counter()
    for r in per_doc:
        for f in r["fields"].values():
            if f["got"] is not None:
                verified["non_null"] += 1
                verified["verified"] += f["status"] == "verified"
    models = sorted({m for v in cost_rows.values() for m in v})
    cost_per_model = {
        m: {
            "documents": sum(1 for v in cost_rows.values() if m in v),
            "total_usd": str(sum(v.get(m, Decimal(0)) for v in cost_rows.values())),
            "per_document_usd": str(
                (
                    sum(v.get(m, Decimal(0)) for v in cost_rows.values())
                    / max(1, sum(1 for v in cost_rows.values() if m in v))
                ).quantize(Decimal("0.000001"))
            ),
        }
        for m in models
    }
    total = sum((sum(v.values(), Decimal(0)) for v in cost_rows.values()), Decimal(0))
    injection = next((r for r in per_doc if r["doc_id"] == "injection_nda"), None)
    return {
        "run_id": str(run_id) if run_id else None,
        "documents": len(per_doc),
        "classification": {
            "type_accuracy": {g: accuracy("type_ok", g) for g in ("digital", "scanned")}
            | {"all": accuracy("type_ok")},
            "subtype_accuracy": {g: accuracy("subtype_ok", g) for g in ("digital", "scanned")}
            | {"all": accuracy("subtype_ok")},
        },
        "nda_recall": nda_recall,
        "field_exact_match": {g: _pct(c["hits"], c["total"]) for g, c in fields_by_group.items()},
        "field_exact_match_by_field": {
            f"{t}.{n}": _pct(c["hits"], c["total"]) for (t, n), c in sorted(field_rows.items())
        },
        "span_verification": _pct(verified["verified"], verified["non_null"]),
        "confidence_vs_accuracy": {
            "at_or_above_threshold": _pct(conf_buckets["above_hits"], conf_buckets["above_total"]),
            "below_threshold": _pct(conf_buckets["below_hits"], conf_buckets["below_total"]),
        },
        "statuses": dict(Counter(r["status"] for r in per_doc)),
        "review_reasons": dict(review_codes.most_common()),
        "absent_clause_keyword_flags": {
            "documents": len({f["doc_id"] for f in absent_flags}),
            "flags": absent_flags,
        },
        "injection_nda": injection
        and {
            "status": injection["status"],
            "got_type": injection["got_type"],
            "reasons": sorted({r["code"] for r in injection["reasons"]}),
            "governing_law": injection["fields"]["governing_law"]["got"],
            "fields_correct": _pct(
                sum(f["hit"] for f in injection["fields"].values()), len(injection["fields"])
            ),
        },
        "cost": {
            "total_usd": str(total),
            "per_document_usd": str((total / max(1, len(per_doc))).quantize(Decimal("0.000001"))),
            "per_model": cost_per_model,
            "tokens_by_step_and_model": {k: dict(v) for k, v in tokens.items()},
        },
        "latency_ms_avg": {k: round(sum(v) / len(v)) for k, v in latency.items() if v},
        "per_document": per_doc,
    }


def write_markdown(report: dict) -> str:
    c = report["classification"]
    absent_lines = [
        f"- {f['doc_id']} `{f['field']}`: {f['detail']}"
        for f in report["absent_clause_keyword_flags"]["flags"]
    ] or ["- none"]
    lines = [
        "# Extraction preview",
        "",
        f"{report['documents']} documents (run `{report['run_id']}`). A preview on a small sample,"
        " not a benchmark: one wrong field moves a percentage a lot.",
        "",
        "## Classification",
        "",
        "| | Digital | Scanned | All |",
        "|---|---|---|---|",
        f"| Type | {c['type_accuracy']['digital']} | {c['type_accuracy']['scanned']} |"
        f" {c['type_accuracy']['all']} |",
        f"| Type + subtype | {c['subtype_accuracy']['digital']} |"
        f" {c['subtype_accuracy']['scanned']} | {c['subtype_accuracy']['all']} |",
        "",
        f"**NDA recall:** {report['nda_recall']['classified_nda']} classified as NDA;"
        f" {report['nda_recall']['caught_by_nda_guard']} more caught by the NDA guard;"
        f" missed: {report['nda_recall']['missed'] or 'none'}.",
        "",
        "## Fields (exact match against the manifest)",
        "",
        "| Digital | Scanned | All |",
        "|---|---|---|",
        f"| {report['field_exact_match'].get('digital', 'n/a')} |"
        f" {report['field_exact_match'].get('scanned', 'n/a')} |"
        f" {report['field_exact_match'].get('all', 'n/a')} |",
        "",
        f"Quotes found in the parsed text (span verification): {report['span_verification']}.",
        "",
        "Field accuracy by the model's own confidence:"
        f" at/above the threshold {report['confidence_vs_accuracy']['at_or_above_threshold']},"
        f" below {report['confidence_vs_accuracy']['below_threshold']}.",
        "",
        "<details><summary>Per field</summary>",
        "",
        "| Field | Exact match |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in report["field_exact_match_by_field"].items()],
        "",
        "</details>",
        "",
        "## Routing",
        "",
        f"Statuses: {report['statuses']}",
        "",
        "| Review reason | Documents |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in report["review_reasons"].items()],
        "",
        f"Absent-clause keyword scan sent {report['absent_clause_keyword_flags']['documents']}"
        " document(s) to review:",
        *absent_lines,
        "",
        "## Prompt-injection NDA",
        "",
        f"{json.dumps(report['injection_nda'])}",
        "",
        "## Cost",
        "",
        f"Total ${report['cost']['total_usd']}, ${report['cost']['per_document_usd']} per document"
        " on average.",
        "",
        "| Model | Documents | Total $ | Per document $ |",
        "|---|---|---|---|",
        *[
            f"| {m} | {v['documents']} | {v['total_usd']} | {v['per_document_usd']} |"
            for m, v in report["cost"]["per_model"].items()
        ],
        "",
        "| Step / model | Calls | Input | Output | Cache write | Cache read |",
        "|---|---|---|---|---|---|",
        *[
            f"| {k} | {v['calls']} | {v['input']} | {v['output']} | {v['cache_write']} |"
            f" {v['cache_read']} |"
            for k, v in report["cost"]["tokens_by_step_and_model"].items()
        ],
        "",
        f"Average latency (ms): {report['latency_ms_avg']}",
        "",
        "## Per document",
        "",
        "| Doc | Group | Truth | Got | Status | Fields right | Reasons |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report["per_document"]:
        hits = sum(f["hit"] for f in r["fields"].values())
        reasons = ", ".join(sorted({x["code"] for x in r["reasons"]})) or "-"
        lines.append(
            f"| {r['doc_id']} | {r['group']} | {r['truth_type']}/{r['truth_subtype'] or '-'} |"
            f" {r['got_type']}/{r['got_subtype'] or '-'} | {r['status']} |"
            f" {hits}/{len(r['fields'])} | {reasons} |"
        )
    return "\n".join(lines) + "\n"


# --- Replay fixtures ----------------------------------------------------------------------------


def record_fixtures(sample: list[dict], ids: dict[str, uuid.UUID], doc_ids: list[str]) -> None:
    """Save the real responses (the last attempt of each step) plus the parsed blocks they were
    made from, so tests can replay them without the API. Synthetic data only; no keys."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import LlmCall, TextBlock

    RECORD_DIR.mkdir(exist_ok=True)
    by_id = {d["doc_id"]: d for d in sample}
    with session_scope() as session:
        for doc_id in doc_ids:
            document_id = ids[doc_id]
            blocks = session.scalars(
                select(TextBlock)
                .where(TextBlock.document_id == document_id)
                .order_by(TextBlock.page, TextBlock.seq)
            ).all()
            calls = session.scalars(
                select(LlmCall).where(LlmCall.document_id == document_id).order_by(LlmCall.id)
            ).all()
            last = {c.step: c for c in calls}  # the final attempt of each step
            entry = by_id[doc_id]
            fixture = {
                "doc_id": doc_id,
                "scanned": entry["scanned"],
                "truth": {
                    "doc_type": entry["doc_type"],
                    "subtype": entry["subtype"],
                    "fields": entry["fields"],
                },
                "page_count": max(b.page for b in blocks),
                "blocks": [
                    {
                        "page": b.page,
                        "text": b.text,
                        "source": b.source,
                        "ocr_confidence": b.ocr_confidence,
                    }
                    for b in blocks
                ],
                "responses": {
                    step: {
                        "model": c.model,
                        "stop_reason": c.stop_reason,
                        "raw_output": c.raw_output,
                        "usage": {
                            "input_tokens": c.input_tokens,
                            "output_tokens": c.output_tokens,
                            "cache_write_tokens": c.cache_write_tokens,
                            "cache_read_tokens": c.cache_read_tokens,
                        },
                    }
                    for step, c in last.items()
                },
            }
            (RECORD_DIR / f"{doc_id}.json").write_text(json.dumps(fixture, indent=2) + "\n")
            print(f"recorded {RECORD_DIR / (doc_id + '.json')}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-spend", type=Decimal, default=Decimal("1.00"))
    parser.add_argument("--dry-run", action="store_true", help="upload + parse + worst case only")
    parser.add_argument("--report-only", action="store_true", help="no API calls; rebuild report")
    parser.add_argument(
        "--run", type=uuid.UUID, help="with --report-only: count only this run's cost"
    )
    parser.add_argument(
        "--record",
        nargs="*",
        metavar="DOC_ID",
        help="save replay fixtures for these docs (default: a chosen five)",
    )
    args = parser.parse_args(argv)

    _use_local_database()
    env = _env()
    api = f"http://localhost:{env.get('API_PORT') or 8000}"
    sample = pick_sample()
    print(f"sample: {len(sample)} documents, {sum(d['scanned'] for d in sample)} scanned")
    for d in sample:
        print(
            f"  {d['doc_id']:20} {d['doc_type']:16} {d['subtype'] or '-':17}"
            f" {'scanned' if d['scanned'] else 'digital'}"
        )
    ids = upload_and_parse(sample, api)
    print(f"all {len(ids)} parsed")
    print(
        f"worst case if every call used its full max_tokens: ${worst_case(ids, sample):.4f}"
        f" (cap ${args.max_spend})"
    )
    if args.dry_run:
        return

    run_id = args.run
    if not args.report_only:
        run_id = run_extraction(ids, args.max_spend)
    report = build_report(sample, ids, run_id)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "extract_preview.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    (REPORTS / "extract_preview.md").write_text(write_markdown(report))
    print(f"report: {REPORTS / 'extract_preview.md'}")
    if args.record is not None:
        chosen = args.record or _default_recordings(sample)
        record_fixtures(sample, ids, chosen)


def _default_recordings(sample: list[dict]) -> list[str]:
    """A digital NDA, a scanned NDA, a side letter, a legal notice, and the two special NDAs."""

    def first(doc_type: str, scanned: bool) -> str:
        return next(
            d["doc_id"]
            for d in sample
            if d["doc_type"] == doc_type
            and d["scanned"] == scanned
            and d["doc_id"].startswith("syn_")
        )

    return [
        first("nda", False),
        first("nda", True),
        first("side_letter", False),
        first("legal_notice", False),
        *SPECIAL,
    ]


if __name__ == "__main__":
    main()
