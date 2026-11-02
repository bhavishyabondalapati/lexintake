"""Fill the running stack with demo documents for the review screens. NO API calls.

Uploads PDFs through the API (the worker parses them for real), then "extracts" them with the FAKE
extractor, which answers from the synthetic manifest like a perfect model. So the screens show
real PDFs, real text blocks and real playbook checks, but the extraction is not a model's work.

    make seed-demo      # needs the stack up (make dev). Safe to run again: it resets these docs.

The committed test fixtures are always used (so CI can seed). If data/synthetic/ exists, a few more
NDAs are added: some that break other rules, and a scanned one (highlighted from OCR boxes).
"""

import json
import os
import uuid

from scripts.extract_preview import (
    FIXTURES,
    SYNTHETIC,
    _env,
    _use_local_database,
    pick_sample,
    upload_and_parse,
)

FIXTURE_IDS = ["angle_brackets_nda", "injection_nda", "syn_00010", "syn_00001", "syn_00002"]


def fixture_docs() -> list[dict]:
    entries = {}
    for name in ("manifest.json", "special_fixtures.json"):
        for d in json.loads((FIXTURES / name).read_text())["documents"]:
            entries[d["doc_id"]] = d | {"path": str(FIXTURES / f"{d['doc_id']}.pdf")}
    return [entries[i] for i in FIXTURE_IDS]


def synthetic_extras() -> list[dict]:
    manifest = SYNTHETIC / "manifest.json"
    if not manifest.exists():
        return []
    taken = {d["doc_id"] for d in pick_sample()}  # the real preview's 20 stay untouched
    ndas = [
        d
        for d in json.loads(manifest.read_text())["documents"]
        if d["doc_type"] == "nda"
        and not d["tricky"]
        and d["doc_id"] not in taken | set(FIXTURE_IDS)
    ]
    picks, seen = [], set()
    for d in ndas:
        key = (d["scanned"], tuple(d["deviations"]))
        if len(d["deviations"]) <= 1 and key not in seen and len(picks) < 8:
            seen.add(key)
            picks.append(d | {"path": str(SYNTHETIC / d["file"])})
    return picks


def main() -> None:
    _use_local_database()
    # The fake never calls a model, but settings insist on model names. No key is read or needed.
    os.environ.setdefault("ANTHROPIC_MODEL_CLASSIFY", "claude-haiku-5-5")
    os.environ.setdefault("ANTHROPIC_MODEL_EXTRACT", "claude-sonnet-5-5")
    api = f"http://localhost:{_env().get('API_PORT') or 8000}"

    from app.config import get_settings
    from app.extraction.pricing import SpendGuard
    from app.extraction.run import RunContext, run_extraction
    from tests.fakes import FakeExtractor

    sample = fixture_docs() + synthetic_extras()
    ids = upload_and_parse(sample, api)
    ctx = RunContext(
        None, SpendGuard("1000"), get_settings(), run_id=uuid.uuid4(), actor="seed:demo"
    )
    for d in sample:
        ctx.extractor = FakeExtractor(d)
        result = run_extraction(ids[d["doc_id"]], ctx, force=True).result
        kind = "scanned" if d["scanned"] else "digital"
        print(f"  {d['doc_id']:20} {d['doc_type']:16} {kind:8} -> {result}")


if __name__ == "__main__":
    main()
