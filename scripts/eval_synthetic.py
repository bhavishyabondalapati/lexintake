"""Evaluate the pipeline on the synthetic manifest (free: reads the database, calls no API).

Reads what a batch run stored (extractions, llm_calls), compares it with the manifest, picks the
confidence thresholds on a calibration half, and reports on the other (test) half.

    PYTHONPATH=backend:. uv run --project backend python -m scripts.eval_synthetic --run RUN_ID

Two ideas of "a real problem", both reported:
- pipeline error: the document's type is wrong, or at least one extracted field is wrong;
- needs a human: a pipeline error, or a true playbook deviation, or an out-of-scope document
  (those are routed to review whatever the thresholds are).
Thresholds are calibrated against pipeline errors only.
"""

import argparse
import json
import os
import random
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPLIT_SEED = 20261102
BASE_FIELD, BASE_OCR = 0.8, 0.9  # the thresholds the run used
GRID = [0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 0.97, 0.99, 1.01]
# Reasons that depend on the thresholds. Everything else routes to review whatever they are.
SOFT = {"low_field_confidence"}


@dataclass
class FieldRow:
    name: str
    truth: object
    got: object
    status: str
    confidence: float
    source: str | None
    ok: bool
    # for a wrong field: "ocr" (copied faithfully from damaged text), "model_missed" (null) or
    # "model_wrong" (a value the text does not contain)
    cause: str | None = None


@dataclass
class Row:
    doc_id: str
    scanned: bool
    truth_type: str
    truth_subtype: str | None
    got_type: str | None
    got_subtype: str | None
    status: str
    fields: list[FieldRow] = field(default_factory=list)
    reason_codes: set[str] = field(default_factory=set)
    truth_deviations: list[str] | None = None
    got_deviations: list[str] = field(default_factory=list)
    got_unknown: list[str] = field(default_factory=list)
    cost: Decimal = Decimal(0)
    truncated: bool = False

    @property
    def cls_ok(self) -> bool:
        return self.got_type == self.truth_type and (
            self.truth_subtype is None or self.got_subtype == self.truth_subtype
        )

    @property
    def error(self) -> bool:
        """Pipeline error: wrong type or at least one wrong field."""
        return not self.cls_ok or any(not f.ok for f in self.fields)

    @property
    def human(self) -> bool:
        """Needs a human: an error, a real deviation, or a type the pipeline doesn't handle."""
        return self.error or bool(self.truth_deviations) or self.truth_type == "other"


def reviewed(row: Row, t_field: float, t_ocr: float) -> bool:
    """Would this document go to review at these thresholds? Everything that doesn't depend on
    a threshold is taken from the stored reasons."""
    if row.reason_codes - SOFT:
        return True
    return any(
        f.status != "missing" and f.confidence < (t_ocr if f.source == "ocr" else t_field)
        for f in row.fields
    )


# --- comparing with the manifest ----------------------------------------------------------------


def same(truth, got) -> bool:
    if truth is None or got is None:
        return truth is None and got is None
    if isinstance(truth, bool) or isinstance(got, bool):
        return truth is got
    if isinstance(truth, int | float) and isinstance(got, int | float):
        return float(truth) == float(got)
    return str(truth).strip() == str(got).strip()


def calibrate(rows: list[Row]) -> tuple[tuple[float, float], list[dict]]:
    """Fewest reviews with no pipeline error getting through, on these rows. Ties go to the higher
    (more careful) thresholds. If no grid point lets nothing through, the fewest escapes win."""
    table = []
    for t_field in GRID:
        for t_ocr in GRID:
            flagged = [reviewed(r, t_field, t_ocr) for r in rows]
            escaped = sum(r.error and not f for r, f in zip(rows, flagged, strict=True))
            table.append(
                {"field": t_field, "ocr": t_ocr, "reviews": sum(flagged), "escaped": escaped}
            )
    best = min(table, key=lambda t: (t["escaped"], t["reviews"], -t["field"], -t["ocr"]))
    return (best["field"], best["ocr"]), table


def split_halves(doc_ids: list[str], seed: int = SPLIT_SEED) -> tuple[set[str], set[str]]:
    ids = sorted(doc_ids)
    random.Random(seed).shuffle(ids)
    half = len(ids) // 2
    return set(ids[:half]), set(ids[half:])


# --- loading ------------------------------------------------------------------------------------


def load_rows(run_id: uuid.UUID) -> list[Row]:
    from sqlalchemy import select

    from app.db import session_scope
    from app.extraction.doctext import load_doc_text, normalize
    from app.models import Document, Extraction, LlmCall

    manifest = {
        d["doc_id"]: d
        for d in json.loads((ROOT / "data/synthetic/manifest.json").read_text())["documents"]
    }
    ids = json.loads((ROOT / "data/eval/ids.json").read_text())
    rows, seen = [], set()
    with session_scope() as session:
        costs: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
        truncated: set[uuid.UUID] = set()
        for c in session.scalars(select(LlmCall).where(LlmCall.run_id == run_id)):
            costs[c.document_id] += c.cost_usd
            if c.stop_reason == "max_tokens":
                truncated.add(c.document_id)
        for doc_id, uid in ids.items():
            uuid_ = uuid.UUID(uid)
            if uuid_ in seen:
                continue  # an exact duplicate of a document already counted
            seen.add(uuid_)
            truth = manifest[doc_id]
            doc = session.get(Document, uuid_)
            ex = session.scalars(select(Extraction).where(Extraction.document_id == uuid_)).first()
            text = normalize(" ".join(p.norm_text for p in load_doc_text(session, uuid_).pages))
            row = Row(
                doc_id=doc_id,
                scanned=truth["scanned"],
                truth_type=truth["doc_type"],
                truth_subtype=truth["subtype"],
                got_type=ex.doc_type if ex else None,
                got_subtype=ex.doc_subtype if ex else None,
                status=doc.status,
                truth_deviations=truth["deviations"],
                cost=costs[uuid_],
                truncated=uuid_ in truncated,
            )
            if ex:
                row.reason_codes = {r["code"] for r in ex.review_reasons}
                for r in ex.playbook_results or []:
                    if r["outcome"] == "deviation":
                        row.got_deviations.append(r["rule_id"])
                    elif r["outcome"] == "unknown":
                        row.got_unknown.append(r["rule_id"])
                for name, f in ex.fields.items():
                    ok = same(truth["fields"].get(name), f["value"])
                    cause = None
                    if not ok:
                        value = f["value"]
                        if value is None:
                            cause = "model_missed"  # the model found nothing
                        elif isinstance(value, str) and normalize(value) in text:
                            # Exactly what the document's (OCR'd) text says: the model copied
                            # damaged text faithfully.
                            cause = "ocr"
                        else:
                            cause = "model_wrong"
                    row.fields.append(
                        FieldRow(
                            name,
                            truth["fields"].get(name),
                            f["value"],
                            f["status"],
                            f["confidence"],
                            f.get("source"),
                            ok,
                            cause,
                        )
                    )
            rows.append(row)
    return rows


# --- numbers ------------------------------------------------------------------------------------


def frac(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "n/a"


def groups(rows: list[Row]):
    return {
        "digital": [r for r in rows if not r.scanned],
        "scanned": [r for r in rows if r.scanned],
        "all": rows,
    }


def accuracy_tables(rows: list[Row]) -> list[str]:
    g = groups(rows)
    out = ["| | Digital | Scanned | All |", "|---|---|---|---|"]

    def line(label, fn):
        out.append(
            f"| {label} | " + " | ".join(fn(g[k]) for k in ("digital", "scanned", "all")) + " |"
        )

    line("Document type + subtype correct", lambda rs: frac(sum(r.cls_ok for r in rs), len(rs)))
    ndas = lambda rs: [r for r in rs if r.truth_type == "nda"]  # noqa: E731
    line(
        "NDA recall (classified as NDA)",
        lambda rs: frac(sum(r.got_type == "nda" for r in ndas(rs)), len(ndas(rs))),
    )
    fields = lambda rs: [f for r in rs for f in r.fields]  # noqa: E731
    line("Field exact match", lambda rs: frac(sum(f.ok for f in fields(rs)), len(fields(rs))))
    nonnull = lambda rs: [f for f in fields(rs) if f.got is not None]  # noqa: E731
    line(
        "Quote verified (non-null fields)",
        lambda rs: frac(sum(f.status == "verified" for f in nonnull(rs)), len(nonnull(rs))),
    )
    for cause, label in (
        ("ocr", "Wrong field: model copied damaged OCR text faithfully"),
        ("model_missed", "Wrong field: model returned nothing (null)"),
        ("model_wrong", "Wrong field: model returned a value the text does not contain"),
    ):
        line(label, lambda rs, c=cause: str(sum(f.cause == c for f in fields(rs))))

    def agree(rs):
        n = ndas(rs)
        return frac(
            sum(
                sorted(r.got_deviations) == sorted(r.truth_deviations or []) and not r.got_unknown
                for r in n
            ),
            len(n),
        )

    line("NDA playbook result equals the manifest (no unknowns)", agree)
    return out


def routing_tables(rows: list[Row], t_field: float, t_ocr: float) -> list[str]:
    g = groups(rows)
    flagged = {r.doc_id: reviewed(r, t_field, t_ocr) for r in rows}
    out = ["| | Digital | Scanned | All |", "|---|---|---|---|"]
    out.append(
        "| Sent to review | "
        + " | ".join(frac(sum(flagged[r.doc_id] for r in g[k]), len(g[k])) for k in g)
        + " |"
    )
    out.append("")
    out.append("| Review reason | Documents |")
    out.append("|---|---|")
    counts = Counter()
    for r in rows:
        if flagged[r.doc_id]:
            codes = set(r.reason_codes - SOFT)
            if any(
                f.status != "missing" and f.confidence < (t_ocr if f.source == "ocr" else t_field)
                for f in r.fields
            ):
                codes.add("low_field_confidence")
            counts.update(codes)
    out += [f"| `{c}` | {n} |" for c, n in counts.most_common()]
    return out


def precision_recall(rows: list[Row], t_field: float, t_ocr: float) -> list[str]:
    out = [
        "| View | Review precision | Review recall |",
        "|---|---|---|",
    ]
    for label, key in (
        ("Needs a human (error, true deviation, or out of scope)", lambda r: r.human),
        ("Pipeline error only (wrong type or wrong field)", lambda r: r.error),
    ):
        sent = [r for r in rows if reviewed(r, t_field, t_ocr)]
        real = [r for r in rows if key(r)]
        both = [r for r in sent if key(r)]
        out.append(
            f"| {label} | {frac(len(both), len(sent))} sent had a real problem | "
            f"{frac(len(both), len(real))} problem documents caught |"
        )
    return out


def cost_lines(rows: list[Row]) -> list[str]:
    total = sum((r.cost for r in rows), Decimal(0))
    by = defaultdict(list)
    for r in rows:
        by[r.truth_type].append(r.cost)
    out = [
        f"Total ${total:.4f} for {len(rows)} documents, ${total / max(1, len(rows)):.5f} each.",
        "",
    ]
    out += ["| Type | Documents | Cost | Per document |", "|---|---|---|---|"]
    for t, cs in sorted(by.items()):
        out.append(f"| {t} | {len(cs)} | ${sum(cs):.4f} | ${sum(cs) / len(cs):.5f} |")
    return out


def build_report(rows: list[Row], run_id: uuid.UUID) -> str:
    cal_ids, test_ids = split_halves([r.doc_id for r in rows])
    cal = [r for r in rows if r.doc_id in cal_ids]
    test = [r for r in rows if r.doc_id in test_ids]
    (t_field, t_ocr), table = calibrate(cal)

    def errs(rs):
        return sum(r.error for r in rs)

    lines = [
        "# Synthetic evaluation (500-document manifest, real models, Batch API)",
        "",
        f"> One run (run `{run_id}`). The models take no `temperature`, so a rerun varies a little"
        " (see"
        " the README); small differences are noise. The documents are synthetic, generated from a"
        " manifest that is also the ground truth.",
        "",
        f"{len(rows)} unique documents (500 minus exact duplicates). Split by a fixed seed "
        f"({SPLIT_SEED}) into a calibration half ({len(cal)}) and a test half ({len(test)}). "
        "**Thresholds were chosen on the calibration half only; every number in the test-half "
        "sections is from the test half.**",
        "",
        "## Thresholds",
        "",
        "Calibrated against pipeline errors only (a deviation or an out-of-scope document goes "
        "to review whatever the thresholds are). Rule: the fewest reviews with no pipeline error "
        "getting through on the calibration half, ties to the higher threshold.",
        "",
        "| | Text-layer field confidence | OCR field confidence |",
        "|---|---|---|",
        f"| Before (the run's setting) | {BASE_FIELD} | {BASE_OCR} |",
        f"| Calibrated | {t_field} | {t_ocr} |",
        "",
        f"On the calibration half there are {errs(cal)} documents with a pipeline error out of "
        f"{len(cal)}. At the calibrated thresholds "
        f"{frac(sum(reviewed(r, t_field, t_ocr) for r in cal if r.error), errs(cal))} of them are "
        f"reviewed, with {sum(reviewed(r, t_field, t_ocr) for r in cal)} reviews in all (before: "
        f"{sum(reviewed(r, BASE_FIELD, BASE_OCR) for r in cal)}).",
        "",
        "## Test half: accuracy",
        "",
        *accuracy_tables(test),
        "",
        "## Test half: routing",
        "",
        f"At the calibrated thresholds (text {t_field}, OCR {t_ocr}):",
        "",
        *routing_tables(test, t_field, t_ocr),
        "",
        f"At the original thresholds ({BASE_FIELD} / {BASE_OCR}) for comparison: "
        f"{frac(sum(reviewed(r, BASE_FIELD, BASE_OCR) for r in test), len(test))} sent to review.",
        "",
        "## Test half: is the review queue right?",
        "",
        f"The test half has {errs(test)} documents with a pipeline error and "
        f"{sum(r.human for r in test)} that need a human. ",
        "",
        "Calibrated thresholds:",
        "",
        *precision_recall(test, t_field, t_ocr),
        "",
        "Original thresholds:",
        "",
        *precision_recall(test, BASE_FIELD, BASE_OCR),
        "",
        "## Cost (test half, Batch API prices)",
        "",
        *cost_lines(test),
        "",
        "## Whole set (495 documents): accuracy, cost and truncation",
        "",
        "Accuracy does not depend on the thresholds, so the whole set is shown too.",
        "",
        *accuracy_tables(rows),
        "",
        *cost_lines(rows),
        "",
        "Truncated answers (hit max_tokens): "
        + (
            ", ".join(
                f"{t}: {n}"
                for t, n in sorted(Counter(r.truth_type for r in rows if r.truncated).items())
            )
            or "none"
        ),
        "",
        "<details><summary>Threshold grid on the calibration half (reviews / pipeline errors "
        "that got through)</summary>",
        "",
        "| Text | OCR | Reviews | Escaped |",
        "|---|---|---|---|",
        *[
            f"| {t['field']} | {t['ocr']} | {t['reviews']} | {t['escaped']} |"
            for t in table
            if t["field"] == t["ocr"] or (t["field"], t["ocr"]) in {(0.8, 0.9), (t_field, t_ocr)}
        ],
        "",
        "</details>",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=uuid.UUID, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "eval-synthetic.md")
    args = parser.parse_args(argv)
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]
    from scripts.extract_preview import _use_local_database

    _use_local_database()
    os.environ.setdefault("ANTHROPIC_MODEL_CLASSIFY", "claude-haiku-5-5")
    os.environ.setdefault("ANTHROPIC_MODEL_EXTRACT", "claude-sonnet-5-5")
    rows = load_rows(args.run)
    args.out.write_text(build_report(rows, args.run))
    print(f"{len(rows)} documents; report: {args.out}")


if __name__ == "__main__":
    main()
