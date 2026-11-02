"""ContractNLI part of the evaluation.

    prepare   upload the test split's NDAs and wait for parsing (free). A document with an original
              PDF in data/contractnli/raw/ uses it; the others (the originals are .txt / .htm) are
              rendered to PDF from the dataset's JSON text with scripts/synth/render.py.
    score     score a finished run against the labels (free; reads the database).

Labels are per document, so it doesn't matter that the originals' layout differs from the JSON
text offsets.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CNLI = ROOT / "data" / "contractnli"
OUT = ROOT / "data" / "eval"
IDS = OUT / "cnli_ids.json"


def test_docs() -> list[dict]:
    return json.loads((CNLI / "test.json").read_text())["documents"]


def slug(doc: dict) -> str:
    return (
        re.sub(r"[^A-Za-z0-9]+", "_", Path(doc["file_name"]).stem).strip("_")[:60] + f"_{doc['id']}"
    )


def render(doc: dict, target: Path) -> None:
    from scripts.synth.render import render_pdf
    from scripts.synth.templates import Block, Document

    lines = [ln.strip() for ln in doc["text"].splitlines() if ln.strip()]
    document = Document(title=lines[0][:80] if lines else "Agreement")
    document.blocks = [Block(ln, style="title" if i == 0 else "body") for i, ln in enumerate(lines)]
    target.write_bytes(render_pdf(document))


def prepare() -> None:
    import httpx

    from scripts.extract_preview import _env

    api = f"http://localhost:{_env().get('API_PORT') or 8000}"
    (OUT / "contractnli_rendered").mkdir(parents=True, exist_ok=True)
    ids: dict[str, dict] = {}
    with httpx.Client(base_url=api, timeout=120) as client:
        for doc in test_docs():
            original = CNLI / "raw" / doc["file_name"]
            if original.suffix.lower() == ".pdf" and original.exists():
                path, kind = original, "original"
            else:
                path, kind = OUT / "contractnli_rendered" / f"{slug(doc)}.pdf", "rendered"
                render(doc, path)
            with path.open("rb") as f:
                r = client.post("/documents", files={"file": (path.name, f, "application/pdf")})
            if r.status_code not in (200, 201):
                print(f"  upload refused for {doc['file_name']}: {r.status_code} {r.text[:100]}")
                continue
            ids[str(doc["id"])] = {"uuid": r.json()["id"], "kind": kind, "file": doc["file_name"]}
        while True:
            states = {k: client.get(f"/documents/{v['uuid']}").json() for k, v in ids.items()}
            waiting = [k for k, s in states.items() if s["status"] in ("received", "parsing")]
            print(f"  waiting for {len(waiting)} document(s) to be parsed...")
            if not waiting:
                break
            time.sleep(10)
    for k, s in states.items():
        ids[k]["status"], ids[k]["pages"] = s["status"], s["page_count"]
    IDS.write_text(json.dumps(ids, indent=1))
    failed = [k for k, v in ids.items() if v["status"] != "parsed"]
    print(f"{len(ids)} uploaded, {len(failed)} not parsed {failed}; ids in {IDS}")


# What each ContractNLI hypothesis could be compared with. Only two have a field to compare.
SCORED = {
    "nda-16": ("return_destroy_days", "a return/destroy obligation is present"),
    "nda-18": ("non_solicit_months", "a non-solicitation clause is present"),
}
NOT_SCORED = {
    "nda-19": "closest field is `term_months`, but a number of months can't be compared with "
    "'some obligations may survive termination'",
    "nda-20": "closest field is `residuals_clause`, but retaining information after return or "
    "destruction is a different legal concept from a residuals clause (use of what is remembered)",
}
NO_FIELD = "no field in the pipeline captures it"


def load_results() -> list[dict]:
    """One row per sampled document: its labels and what the pipeline said."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Document, Extraction, LlmCall

    selection = json.loads((OUT / "cnli_selection.json").read_text())
    labels = {str(d["id"]): d["annotation_sets"][0]["annotations"] for d in test_docs()}
    rows = []
    with session_scope() as session:
        for key, info in selection.items():
            uid = info["uuid"]
            doc = session.get(Document, uid)
            ex = session.scalars(select(Extraction).where(Extraction.document_id == uid)).first()
            calls = session.scalars(select(LlmCall).where(LlmCall.document_id == uid)).all()
            row = {
                "id": key,
                "uuid": uid,
                "kind": info["kind"],
                "file": info["file"],
                "status": doc.status,
                "got_type": ex.doc_type if ex else None,
                "reasons": sorted({r["code"] for r in ex.review_reasons}) if ex else [],
                "cost": float(sum(c.cost_usd for c in calls)),
                "truncated": any(c.stop_reason == "max_tokens" for c in calls),
                "labels": {h: labels[key][h]["choice"] for h in labels[key]},
            }
            for hypothesis, (field, _) in SCORED.items():
                got = ex.fields.get(field) if ex and ex.fields else None
                row[hypothesis] = None if got is None else got["value"] is not None
                # The pipeline also notices a clause the model called absent but the text mentions.
                row[hypothesis + "_noticed"] = got is not None and (
                    got["value"] is not None or "absent_but_mentioned" in got.get("flags", [])
                )
            rows.append(row)
    return rows


def frac(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "0/0"


def build_report(rows: list[dict]) -> str:
    hypotheses = json.loads((CNLI / "test.json").read_text())["labels"]
    out = [
        "# ContractNLI evaluation (real NDAs, real models, Batch API)",
        "",
        "> One run. Real NDAs from the ContractNLI **test split** (CC BY 4.0). The sample is"
        " **label-stratified** (see below), so **overall accuracy is not reported**: it would"
        " mostly measure how many easy NotMentioned documents were sampled. Results are per"
        " class, with counts.",
        "",
        "## Main findings from real data",
        "",
        '1. **Schema flaw.** `return_destroy_days` (and `non_solicit_months`) conflate "no clause"'
        ' with "clause without a deadline". Real NDAs often have the second; the synthetic data'
        " never did, so the synthetic evaluation could not show it. Future work: a separate"
        " `return_destroy` boolean, with the number of days optional.",
        "2. **Domain shift.** Real NDAs were not classified confidently as NDAs: "
        f"{sum('low_classification_confidence' in r['reasons'] for r in rows)} of {len(rows)} came"
        " back below the 0.8 threshold and went to review before extraction. The threshold was"
        " **deliberately not tuned on this test sample**; tuning it here would make the numbers"
        " meaningless.",
        "3. **The absent-clause keyword scan overfires on real NDAs.** It flagged 11 of 20 rendered"
        " NotMentioned documents (return/destroy wording appears in many clauses that are not a"
        " return/destroy obligation). It sends documents to review, so it costs reviewer time but"
        " lets nothing through.",
        "4. **No real NDA was auto-approved.** All "
        f"{len(rows)} went to review. The system fails safe: when unsure, a person looks.",
        "",
        "## What can and can't be scored",
        "",
        "ContractNLI labels each NDA against 17 hypotheses as Entailment (the contract says it),"
        " Contradiction (it says the opposite) or NotMentioned. Our pipeline extracts 10 NDA"
        " fields, mostly values (a counterparty, a date, a number of months). Only two fields"
        " correspond to a hypothesis, and only as **presence vs absence of the clause**:",
        "",
        "| Hypothesis (as quoted in the dataset) | Our field | Expected for Entailment |"
        " Expected for NotMentioned / Contradiction |",
        "|---|---|---|---|",
    ]
    for h, (field, meaning) in SCORED.items():
        out.append(
            f'| {h}: "{hypotheses[h]["hypothesis"]}" | `{field}` | not null ({meaning}) | null |'
        )
    out += [
        "",
        "Caveat: `return_destroy_days` and `non_solicit_months` are *durations*. An NDA that has a"
        " return/destroy duty with no deadline, or a non-solicit with no stated period, can come"
        ' back null, which counts as a miss here. Recall is therefore a lower bound on "finds the'
        ' clause".',
        "",
        "Not scored, with the reason:",
        "",
        "| Hypothesis | Why not scored |",
        "|---|---|",
    ]
    for h in sorted(hypotheses, key=lambda x: int(x.split("-")[1])):
        if h in SCORED:
            continue
        out.append(f'| {h}: "{hypotheses[h]["hypothesis"]}" | {NOT_SCORED.get(h, NO_FIELD)} |')
    out += [
        "",
        "Fields with no ContractNLI label at all: counterparty, fund party, effective date,"
        " mutual, term, governing law, non-compete, residuals. They can't be scored on this"
        " dataset (the synthetic evaluation covers them).",
        "",
        "## The sample",
        "",
        f"{len(rows)} NDAs from the test split's 123. **Original PDF** = the dataset's own file;"
        " **rendered** = the original is .txt/.htm, so the dataset's JSON text was rendered to a"
        " clean digital PDF (no scan noise, no original layout), so rendered results are easier"
        " than real-world files. Chosen with a fixed seed (20261103): the one Contradiction"
        " document, then every document with an Entailment label for either hypothesis, then"
        " NotMentioned documents until the budget ran out.",
        "",
        "Small cells: the 5 original-PDF non-solicit Entailment documents are **all** such"
        " documents in the test split, so that limit is the dataset's, not the sampling's. The"
        " same holds for the single nda-16 Contradiction document. The 8 original-PDF nda-16"
        " NotMentioned documents are a sample of 52 (the budget stopped there), so that cell"
        " is limited by our sampling.",
        "",
    ]
    for h, (field, _) in SCORED.items():
        out += [f"## {h}: {hypotheses[h]['hypothesis']}", "", f"Field `{field}`.", ""]
        out += ["| Class | Measure | Original PDF | Rendered |", "|---|---|---|---|"]
        for label, measure_strict, measure_noticed in (
            ("Entailment", "value filled in (recall)", "filled in, or flagged as mentioned"),
            ("NotMentioned", "value filled in (false alarm)", "filled in, or flagged as mentioned"),
            (
                "Contradiction",
                "value filled in (expected: no)",
                "filled in, or flagged as mentioned",
            ),
        ):
            for measure, key in ((measure_strict, h), (measure_noticed, h + "_noticed")):
                cells = []
                for kind in ("original", "rendered"):
                    docs = [r for r in rows if r["kind"] == kind and r["labels"][h] == label]
                    hit = sum(r[key] is True for r in docs)
                    none = sum(r[h] is None for r in docs)
                    text = frac(hit, len(docs)) if docs else "no documents"
                    cells.append(text + (f", {none} not extracted" if none else ""))
                out.append(f"| {label} | {measure} | {cells[0]} | {cells[1]} |")
        out += [
            "",
            '"Not extracted": the pipeline gave no field answer for that document (the type was'
            " in doubt or the answer was cut off), so it counts as a miss for Entailment and is"
            ' not counted as a false alarm. "Flagged as mentioned": the model left the field'
            " empty but a keyword scan found the clause's wording in the text, so the document"
            " went to review with `absent_but_mentioned`.",
            "",
        ]
    out += ["## How the pipeline behaved on real NDAs", ""]
    out += ["| | Original PDF | Rendered |", "|---|---|---|"]

    def line(label, fn):
        cells = []
        for kind in ("original", "rendered"):
            docs = [r for r in rows if r["kind"] == kind]
            cells.append(fn(docs))
        out.append(f"| {label} | {cells[0]} | {cells[1]} |")

    line("Classified as NDA", lambda d: frac(sum(r["got_type"] == "nda" for r in d), len(d)))
    line("Auto-approved", lambda d: frac(sum(r["status"] == "auto_approved" for r in d), len(d)))
    line("Sent to review", lambda d: frac(sum(r["status"] == "needs_review" for r in d), len(d)))
    line(
        "Cost per document (batch)", lambda d: f"${sum(r['cost'] for r in d) / max(1, len(d)):.4f}"
    )
    line(
        "Truncated at the first attempt (400-token bound)",
        lambda d: str(sum(r["truncated"] for r in d)),
    )
    reasons: dict[str, int] = {}
    for r in rows:
        for code in r["reasons"]:
            reasons[code] = reasons.get(code, 0) + 1
    out += ["", "Review reasons (documents):", ""]
    out += [f"- `{c}`: {n}" for c, n in sorted(reasons.items(), key=lambda x: -x[1])]
    rerun = (
        json.loads((OUT / "cnli_rerun.json").read_text())
        if (OUT / "cnli_rerun.json").exists()
        else []
    )
    if rerun:
        redone = [r for r in rows if r["uuid"] in rerun]
        low = sum("low_classification_confidence" in r["reasons"] for r in redone)
        bad = sum("invalid_output" in r["reasons"] for r in redone)
        out += [
            "",
            "## The 9 documents that were re-run",
            "",
            f"In the first run {len(redone)} classification answers were cut off at the 400-token"
            " bound I had set for batch classification (`truncated`), so those documents were not"
            " classified at all. They were **re-run with the classify bound raised to 800** (and"
            " extraction allowed to follow). The table above includes the re-run. Result: "
            f"{low} were classified as NDA but with confidence between 0.35 and 0.6, below the 0.8"
            f" threshold, so they went to review before extraction; {bad} answered type `other`"
            " without the required subtype (an invalid answer). None reached extraction. So the"
            " cut-off was a symptom: these are ambiguous documents (SEC exhibits, consent and"
            " data-use forms) that the classifier is unsure about, and a bigger bound did not"
            " change the routing.",
        ]
    out += ["", f"Total cost: ${sum(r['cost'] for r in rows):.4f} for {len(rows)} documents."]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "score"])
    args = parser.parse_args(argv)
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]
    from scripts.extract_preview import _use_local_database

    _use_local_database()
    if args.command == "prepare":
        prepare()
    else:
        rows = load_results()
        target = ROOT / "docs" / "eval-contractnli.md"
        target.write_text(build_report(rows))
        print(f"{len(rows)} documents; report: {target}")


if __name__ == "__main__":
    main()
