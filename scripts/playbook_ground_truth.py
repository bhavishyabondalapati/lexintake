"""Check the playbook checker against the synthetic manifest's ground truth.

For every synthetic NDA, feed the manifest's fields (as verified evidence, or as verified
absences) to `check_playbook`. The rules it flags as deviations must be exactly the manifest's
"deviations", and no rule may come back "unknown". Free: no API, no database.

    make playbook-truth        # the full data/synthetic/manifest.json
"""

import json
import sys
from pathlib import Path

from app.playbook import DEVIATION, UNKNOWN, check_playbook, load_playbook

MANIFEST = Path("data/synthetic/manifest.json")


def as_extracted(doc: dict) -> dict[str, dict]:
    """Manifest fields in the shape of extractions.fields, all verified."""
    out = {}
    for name, value in doc["fields"].items():
        quote = doc["evidence"].get(name)
        out[name] = {
            "value": value,
            "status": "verified" if value is not None else "absent",
            "page": 1,
            "quote": quote,
        }
    return out


def mismatches(docs: list[dict]) -> tuple[int, list[str]]:
    """(number of NDAs checked, descriptions of every disagreement)."""
    playbook = load_playbook()
    problems = []
    ndas = [d for d in docs if d["doc_type"] == "nda"]
    for d in ndas:
        results = check_playbook(playbook, as_extracted(d))
        got = [r.rule_id for r in results if r.outcome == DEVIATION]
        unknown = [r.rule_id for r in results if r.outcome == UNKNOWN]
        if got != d["deviations"] or unknown:
            problems.append(
                f"{d['doc_id']}: checker {got} unknown {unknown} manifest {d['deviations']}"
            )
    return len(ndas), problems


def main() -> int:
    docs = json.loads(MANIFEST.read_text())["documents"]
    n, problems = mismatches(docs)
    print(f"{n} NDAs checked, {len(problems)} mismatches")
    for p in problems[:20]:
        print(" ", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
