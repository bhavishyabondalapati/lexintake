"""Parse quality report: how much of the ground truth survives parsing?

For every synthetic document, parse it with the same DoclingParser the worker uses, then look
for the manifest's evidence strings in the parsed text:

- digital PDFs (text layer): exact match after collapsing whitespace, which is how the manifest
  promises the evidence appears. A second, "normalized" rate (unicode NFKC, straight quotes,
  joined hyphenation) helps explain any misses.
- scanned PDFs (OCR): fuzzy match (rapidfuzz partial_ratio, 0-100), reported at 80 / 90 / 95.

Deskew experiment (scanned docs only): parse each scan three ways and compare the fuzzy scores:
  as-is      what the worker does today
  oracle     rotated back by the known -scan_rotation_deg (the best deskew could ever do)
  estimated  angle found by a projection-profile search (what production could actually do)

Writes data/reports/parse_quality.{json,md}.

    make parse-quality
    PYTHONPATH=backend uv run --project backend --group parse python -m scripts.parse_quality \
        --limit 50 --skip-deskew
"""

import argparse
import io
import json
import re
import statistics
import tempfile
import time
import unicodedata
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image
from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "data" / "synthetic"
REPORTS = ROOT / "data" / "reports"

SCAN_DPI = 150  # the generator rasterized scans at this resolution (scripts/synth/render.py)
THRESHOLDS = (80, 90, 95)
SEARCH_DEG = 3.0
SEARCH_STEP = 0.1


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    text = re.sub(r"(\w)-\s+(\w)", r"\1\2", text)  # words hyphenated across lines
    return collapse(text)


def full_text(result) -> str:
    return collapse(" ".join(b.text for b in result.blocks))


def fuzzy_scores(evidence: dict[str, str], text: str) -> dict[str, float]:
    return {f: round(fuzz.partial_ratio(collapse(e), text), 1) for f, e in evidence.items()}


# --- deskew ---------------------------------------------------------------------------------


def render_pages(pdf_path: Path) -> list[Image.Image]:
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        return [
            pdf[i].render(scale=SCAN_DPI / 72, grayscale=True).to_pil().convert("L")
            for i in range(len(pdf))
        ]
    finally:
        pdf.close()


def estimate_correction(image: Image.Image) -> float:
    """Find the rotation that makes text lines most horizontal.

    Projection profile: when lines are level, summing ink along each row gives sharp peaks
    (text lines) and valleys (gaps), so the row sums have the highest variance.
    """
    small = image.reduce(2)  # ~75 DPI is plenty to see lines, and 4x faster
    best_angle, best_score = 0.0, -1.0
    for angle in np.arange(-SEARCH_DEG, SEARCH_DEG + 1e-9, SEARCH_STEP):
        rotated = small.rotate(float(angle), resample=Image.BILINEAR, fillcolor=255)
        ink = 255 - np.asarray(rotated, dtype=np.float32)
        score = float(ink.sum(axis=1).var())
        if score > best_score:
            best_angle, best_score = float(angle), score
    return round(best_angle, 2)


def rotated_pdf(pages: list[Image.Image], angles: list[float], out: Path) -> Path:
    images = [
        page.rotate(angle, resample=Image.BICUBIC, fillcolor=255)
        for page, angle in zip(pages, angles, strict=True)
    ]
    buf = io.BytesIO()
    images[0].save(buf, "PDF", resolution=SCAN_DPI, save_all=True, append_images=images[1:])
    out.write_bytes(buf.getvalue())
    return out


# --- report ---------------------------------------------------------------------------------


def rate(hits: list[bool]) -> float:
    return round(100 * sum(hits) / len(hits), 2) if hits else float("nan")


def bucket(rotation: float) -> str:
    r = abs(rotation)
    return "<1°" if r < 1 else "1-2°" if r < 2 else ">2°"


def summarize(rows: list[dict], deskew: list[dict]) -> dict:
    digital = [r for r in rows if not r["scanned"]]
    scanned = [r for r in rows if r["scanned"]]
    exact = [hit for r in digital for hit in r["exact"].values()]
    normalized = [hit for r in digital for hit in r["normalized"].values()]
    scores = [s for r in scanned for s in r["fuzzy"].values()]

    def per_page(group: list[dict]) -> float:
        pages = sum(r["pages"] for r in group)
        return round(sum(r["seconds"] for r in group) / pages, 2) if pages else float("nan")

    summary = {
        "documents": len(rows),
        "digital": {
            "documents": len(digital),
            "evidence_strings": len(exact),
            "exact_hit_rate": rate(exact),
            "normalized_hit_rate": rate(normalized),
            "docs_with_every_string_found": sum(all(r["exact"].values()) for r in digital),
            "wrong_source_docs": sum(r["ocr_blocks"] > 0 for r in digital),
            "seconds_per_page": per_page(digital),
        },
        "scanned": {
            "documents": len(scanned),
            "evidence_strings": len(scores),
            "mean_fuzzy_score": round(statistics.fmean(scores), 2) if scores else None,
            **{f"hit_rate_at_{t}": rate([s >= t for s in scores]) for t in THRESHOLDS},
            "wrong_source_docs": sum(r["text_layer_blocks"] > 0 for r in scanned),
            "mean_ocr_confidence": round(
                statistics.fmean(r["ocr_confidence"] for r in scanned if r["ocr_confidence"]), 4
            )
            if scanned
            else None,
            "seconds_per_page": per_page(scanned),
        },
        "failures": [r["doc_id"] for r in rows if r.get("error")],
    }
    if deskew:
        variants = {}
        for variant in ("as_is", "oracle", "estimated"):
            vs = [s for d in deskew for s in d[variant].values()]
            by_bucket = {}
            for b in ("<1°", "1-2°", ">2°"):
                bs = [s for d in deskew if d["bucket"] == b for s in d[variant].values()]
                by_bucket[b] = rate([s >= 90 for s in bs])
            variants[variant] = {
                "mean_fuzzy_score": round(statistics.fmean(vs), 2),
                **{f"hit_rate_at_{t}": rate([s >= t for s in vs]) for t in THRESHOLDS},
                "hit_rate_at_90_by_rotation": by_bucket,
            }
        errors = [d["angle_error"] for d in deskew]
        summary["deskew"] = {
            "documents": len(deskew),
            "variants": variants,
            "estimated_angle_error_deg": {
                "mean": round(statistics.fmean(errors), 2),
                "max": round(max(errors), 2),
            },
            "bucket_sizes": {
                b: sum(d["bucket"] == b for d in deskew) for b in ("<1°", "1-2°", ">2°")
            },
        }
    return summary


def markdown(summary: dict, rows: list[dict]) -> str:
    d, s = summary["digital"], summary["scanned"]
    lines = [
        "# Parse quality (synthetic set)",
        "",
        "| | Documents | Evidence strings | Result | Wrong source | Seconds / page |",
        "|---|---|---|---|---|---|",
        f"| Digital (exact) | {d['documents']} | {d['evidence_strings']} | "
        f"{d['exact_hit_rate']}% found (normalized: {d['normalized_hit_rate']}%) | "
        f"{d['wrong_source_docs']} | {d['seconds_per_page']} |",
        f"| Scanned (fuzzy) | {s['documents']} | {s['evidence_strings']} | "
        f"≥90: {s['hit_rate_at_90']}%, ≥95: {s['hit_rate_at_95']}%, ≥80: {s['hit_rate_at_80']}%, "
        f"mean {s['mean_fuzzy_score']} | {s['wrong_source_docs']} | {s['seconds_per_page']} |",
        "",
    ]
    misses = [
        (r["doc_id"], f, r["evidence"][f])
        for r in rows
        if not r["scanned"]
        for f, hit in r["exact"].items()
        if not hit
    ]
    if misses:
        lines += ["## Digital misses", ""]
        lines += [f"- `{doc}` {field}: {text[:120]!r}" for doc, field, text in misses[:30]]
        lines.append("")
    if "deskew" in summary:
        dk = summary["deskew"]
        lines += [
            f"## Deskew experiment ({dk['documents']} scanned docs)",
            "",
            "| Variant | Mean score | ≥80 | ≥90 | ≥95 | ≥90 by rotation (<1° / 1-2° / >2°) |",
            "|---|---|---|---|---|---|",
        ]
        for name, v in dk["variants"].items():
            b = v["hit_rate_at_90_by_rotation"]
            lines.append(
                f"| {name} | {v['mean_fuzzy_score']} | {v['hit_rate_at_80']}% | "
                f"{v['hit_rate_at_90']}% | {v['hit_rate_at_95']}% | "
                f"{b['<1°']}% / {b['1-2°']}% / {b['>2°']}% |"
            )
        err = dk["estimated_angle_error_deg"]
        lines += [
            "",
            f"Estimated angle error: mean {err['mean']}°, max {err['max']}°. "
            f"Docs per bucket: {dk['bucket_sizes']}.",
            "",
        ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit", type=int, help="only the first N documents")
    parser.add_argument("--skip-deskew", action="store_true")
    parser.add_argument("--device", default="auto", help="auto (mps on a Mac), cpu, ...")
    args = parser.parse_args()

    from app.parsing.docling_parser import DoclingParser  # heavy import, after argparse

    manifest = json.loads((SYNTHETIC / "manifest.json").read_text())
    docs = manifest["documents"][: args.limit]
    docling = DoclingParser(device=args.device)
    docling.warm_up()

    rows: list[dict] = []
    started = time.monotonic()
    for i, doc in enumerate(docs, 1):
        path = SYNTHETIC / doc["file"]
        t = time.monotonic()
        row = {k: doc[k] for k in ("doc_id", "doc_type", "scanned", "pages", "evidence")}
        row["scan_rotation_deg"] = doc["scan_rotation_deg"]
        try:
            result = docling.parse(path)
        except Exception as e:  # report it, keep going
            row.update(error=f"{type(e).__name__}: {e}", seconds=0, exact={}, normalized={})
            row.update(fuzzy={}, ocr_blocks=0, text_layer_blocks=0, ocr_confidence=None)
            rows.append(row)
            continue
        row["seconds"] = round(time.monotonic() - t, 2)
        text = full_text(result)
        norm_text = normalize(text)
        row["exact"] = {f: collapse(e) in text for f, e in doc["evidence"].items()}
        row["normalized"] = {f: normalize(e) in norm_text for f, e in doc["evidence"].items()}
        row["fuzzy"] = fuzzy_scores(doc["evidence"], text) if doc["scanned"] else {}
        row["ocr_blocks"] = sum(b.source == "ocr" for b in result.blocks)
        row["text_layer_blocks"] = sum(b.source == "text_layer" for b in result.blocks)
        confs = [b.ocr_confidence for b in result.blocks if b.ocr_confidence is not None]
        row["ocr_confidence"] = round(statistics.fmean(confs), 4) if confs else None
        rows.append(row)
        if i % 50 == 0:
            print(f"parsed {i}/{len(docs)} ({time.monotonic() - started:.0f}s)", flush=True)

    deskew: list[dict] = []
    if not args.skip_deskew:
        scanned = [r for r in rows if r["scanned"] and not r.get("error")]
        print(f"deskew experiment on {len(scanned)} scanned docs...", flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            for row in scanned:
                pages = render_pages(SYNTHETIC / f"docs/{row['doc_id']}.pdf")
                truth = -row["scan_rotation_deg"]
                estimates = [estimate_correction(p) for p in pages]
                variants = {"as_is": row["fuzzy"]}
                for name, angles in (("oracle", [truth] * len(pages)), ("estimated", estimates)):
                    pdf = rotated_pdf(pages, angles, Path(tmp) / f"{name}.pdf")
                    variants[name] = fuzzy_scores(row["evidence"], full_text(docling.parse(pdf)))
                deskew.append(
                    {
                        "doc_id": row["doc_id"],
                        "rotation_deg": row["scan_rotation_deg"],
                        "bucket": bucket(row["scan_rotation_deg"]),
                        "estimated_correction": estimates,
                        "angle_error": round(max(abs(e - truth) for e in estimates), 2),
                        **variants,
                    }
                )

    summary = summarize(rows, deskew)
    summary["seconds_total"] = round(time.monotonic() - started, 1)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "parse_quality.json").write_text(
        json.dumps({"summary": summary, "documents": rows, "deskew": deskew}, indent=2)
    )
    report = markdown(summary, rows)
    (REPORTS / "parse_quality.md").write_text(report)
    print(report)
    print(f"wrote {REPORTS / 'parse_quality.json'} and parse_quality.md")


if __name__ == "__main__":
    main()
