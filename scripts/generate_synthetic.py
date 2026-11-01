"""Generate a synthetic legal "mailroom": PDFs plus a ground-truth manifest.

    PYTHONPATH=backend uv run --project backend python -m scripts.generate_synthetic [--seed 42]

Writes data/synthetic/docs/syn_00001.pdf ... and data/synthetic/manifest.json. No LLM calls: every
document comes from templates, so it is free, and the same seed always gives the same documents.

The mix covers NDAs, side letters, investor notices, legal notices/claims and other mail. About 20%
are scanned-looking PDFs with no text layer. A few are tricky on purpose: exact duplicates,
near-duplicates with one changed clause, and NDAs with unusual titles. NDA terms are chosen against
playbook/nda_playbook.yaml, and each NDA's "deviations" come from evaluating that playbook.
"""

import argparse
import copy
import hashlib
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from faker import Faker

from app.playbook import DEFAULT_PLAYBOOK_PATH, Playbook, evaluate, load_playbook
from scripts.synth.fields import SAMPLERS, flip_one_rule, sample_nda
from scripts.synth.render import page_count, render_pdf, scan_pdf
from scripts.synth.templates import UNUSUAL_NDA_TITLES, build

GENERATOR_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parents[1]
MIN_DOCS = 20

DOC_TYPE_SHARES = {
    "nda": 0.40,
    "side_letter": 0.15,
    "investor_notice": 0.20,
    "legal_notice": 0.10,
    "other": 0.15,
}
DUPLICATE_SHARE = 0.01  # 5 of 500
NEAR_DUPLICATE_SHARE = 0.006  # 3 of 500
UNUSUAL_TITLE_SHARE = 0.006  # 3 of 500
TWO_DEVIATION_CHANCE = 0.3


@dataclass
class Planned:
    """One document before rendering."""

    key: int
    doc_type: str
    subtype: str | None
    fields: dict[str, Any]
    variant_seed: int  # drives wording, names and scan noise for this document
    scanned: bool = False
    rotation_deg: float | None = None
    title: str | None = None  # set only to force an unusual title
    tricky: str | None = None
    source: int | None = None  # key of the original, for duplicates and near-duplicates
    changed_field: str | None = None


def split_counts(total: int, shares: dict[str, float]) -> dict[str, int]:
    """Split total by shares using largest remainders, so the counts add up exactly."""
    raw = {name: total * share for name, share in shares.items()}
    counts = {name: int(value) for name, value in raw.items()}
    leftovers = sorted(raw, key=lambda name: raw[name] - counts[name], reverse=True)
    for name in leftovers[: total - sum(counts.values())]:
        counts[name] += 1
    return counts


def plan_documents(n: int, seed: int, scanned_ratio: float, playbook: Playbook) -> list[Planned]:
    """Decide every document's type, fields and tricks. Cheap: nothing is rendered."""
    if n < MIN_DOCS:
        raise ValueError(f"n must be at least {MIN_DOCS} to fit every document type and trick")
    rng = random.Random(seed)
    fake = Faker("en_US")

    n_dup = max(1, round(n * DUPLICATE_SHARE))
    n_near = max(1, round(n * NEAR_DUPLICATE_SHARE))
    n_unusual = max(1, round(n * UNUSUAL_TITLE_SHARE))
    counts = split_counts(n - n_dup - n_near, DOC_TYPE_SHARES)

    # Half the NDAs follow the playbook. The rest break one rule (sometimes two), cycling through
    # the rules so every rule gets broken somewhere.
    passing = [True] * (counts["nda"] // 2) + [False] * (counts["nda"] - counts["nda"] // 2)
    rng.shuffle(passing)
    rule_ids = [rule.id for rule in playbook.rules]
    rng.shuffle(rule_ids)

    base: list[Planned] = []
    nda_index = deviating_index = 0
    for doc_type, count in counts.items():
        for _ in range(count):
            variant_seed = rng.getrandbits(32)
            doc_rng = random.Random(variant_seed)
            fake.seed_instance(variant_seed)
            if doc_type == "nda":
                broken = set()
                if not passing[nda_index]:
                    first = rule_ids[deviating_index % len(rule_ids)]
                    broken.add(first)
                    if doc_rng.random() < TWO_DEVIATION_CHANCE:
                        broken.add(doc_rng.choice([r for r in rule_ids if r != first]))
                    deviating_index += 1
                nda_index += 1
                subtype, fields = sample_nda(doc_rng, fake, playbook, broken)
            else:
                subtype, fields = SAMPLERS[doc_type](doc_rng, fake)
            base.append(Planned(len(base), doc_type, subtype, fields, variant_seed))

    # Scanned: the same share of every type, so each type has some.
    if scanned_ratio > 0:
        for doc_type in counts:
            members = [d for d in base if d.doc_type == doc_type]
            k = min(len(members), max(1, round(len(members) * scanned_ratio)))
            for doc in rng.sample(members, k):
                doc.scanned = True
                doc.rotation_deg = round(rng.uniform(0.5, 2.5) * rng.choice([-1, 1]), 2)

    ndas = [d for d in base if d.doc_type == "nda"]
    near_sources = rng.sample([d for d in ndas if not d.scanned], n_near)
    near_keys = {d.key for d in near_sources}
    for i, doc in enumerate(rng.sample([d for d in ndas if d.key not in near_keys], n_unusual)):
        doc.title = UNUSUAL_NDA_TITLES[i % len(UNUSUAL_NDA_TITLES)]
        doc.tricky = "unusual_title"

    derived: list[Planned] = []
    for source in near_sources:
        fields = dict(source.fields)
        changed = flip_one_rule(rng, playbook, fields)
        derived.append(
            Planned(
                key=len(base) + len(derived),
                doc_type="nda",
                subtype=None,
                fields=fields,
                variant_seed=source.variant_seed,  # same wording, so only that clause differs
                tricky="near_duplicate",
                source=source.key,
                changed_field=changed,
            )
        )
    for source in rng.sample(base, n_dup):
        duplicate = copy.deepcopy(source)
        duplicate.key = len(base) + len(derived)
        duplicate.tricky = "duplicate"
        duplicate.source = source.key
        derived.append(duplicate)

    documents = base + derived
    rng.shuffle(documents)  # so duplicates don't sit next to their originals
    return documents


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clear_previous_run(out_dir: Path) -> Path:
    """Delete files from an earlier run (only ours), so the folder always matches the manifest."""
    docs_dir = out_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    for old in docs_dir.glob("syn_*.pdf"):
        old.unlink()
    (out_dir / "manifest.json").unlink(missing_ok=True)
    return docs_dir


def generate(
    out_dir: Path,
    n: int = 500,
    seed: int = 42,
    scanned_ratio: float = 0.2,
    playbook_path: Path = DEFAULT_PLAYBOOK_PATH,
) -> dict[str, Any]:
    playbook = load_playbook(playbook_path)
    plans = plan_documents(n, seed, scanned_ratio, playbook)
    docs_dir = clear_previous_run(out_dir)
    doc_ids = {plan.key: f"syn_{i:05d}" for i, plan in enumerate(plans, start=1)}

    fake = Faker("en_US")
    rendered: dict[int, tuple[bytes, Any]] = {}
    for plan in plans:
        if plan.tricky == "duplicate":
            continue
        # Offset the seed so the wording draws don't repeat the field-sampling draws.
        wording_seed = plan.variant_seed + 1
        fake.seed_instance(wording_seed)
        document = build(
            plan.doc_type, plan.subtype, plan.fields, random.Random(wording_seed), fake, plan.title
        )
        pdf = render_pdf(document)
        if plan.scanned:
            pdf = scan_pdf(pdf, np.random.default_rng(plan.variant_seed), plan.rotation_deg)
        rendered[plan.key] = (pdf, document)

    entries = []
    for plan in plans:
        pdf, document = rendered[plan.source if plan.tricky == "duplicate" else plan.key]
        doc_id = doc_ids[plan.key]
        path = docs_dir / f"{doc_id}.pdf"
        path.write_bytes(pdf)
        entries.append(
            {
                "doc_id": doc_id,
                "file": f"docs/{path.name}",
                "sha256": sha256_bytes(pdf),
                "doc_type": plan.doc_type,
                "subtype": plan.subtype,
                "title": document.title,
                "scanned": plan.scanned,
                "scan_rotation_deg": plan.rotation_deg,
                "pages": page_count(pdf),
                "fields": plan.fields,
                "evidence": document.evidence,
                # null for non-NDAs: the playbook doesn't apply to them.
                "deviations": evaluate(playbook, plan.fields) if plan.doc_type == "nda" else None,
                "tricky": plan.tricky,
                "duplicate_of": doc_ids[plan.source] if plan.tricky == "duplicate" else None,
                "near_duplicate_of": (
                    doc_ids[plan.source] if plan.tricky == "near_duplicate" else None
                ),
                "changed_field": plan.changed_field,
            }
        )

    manifest = {
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "n_docs": len(entries),
        "scanned_ratio": scanned_ratio,
        "playbook_sha256": sha256_bytes(Path(playbook_path).read_bytes()),
        "counts": {
            doc_type: sum(e["doc_type"] == doc_type for e in entries)
            for doc_type in DOC_TYPE_SHARES
        },
        "documents": entries,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--seed", type=int, default=42, help="same seed, same documents")
    parser.add_argument("--n", type=int, default=500, help="number of documents (default 500)")
    parser.add_argument("--scanned-ratio", type=float, default=0.2, help="share of scanned PDFs")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "synthetic")
    args = parser.parse_args(argv)

    manifest = generate(args.out, n=args.n, seed=args.seed, scanned_ratio=args.scanned_ratio)
    docs = manifest["documents"]
    print(f"Wrote {len(docs)} documents to {args.out / 'docs'}")
    print(f"  by type: {manifest['counts']}")
    print(f"  scanned: {sum(d['scanned'] for d in docs)}")
    tricky = [d["tricky"] for d in docs if d["tricky"]]
    print(f"  tricky:  { {t: tricky.count(t) for t in sorted(set(tricky))} }")
    print(f"Manifest: {args.out / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
