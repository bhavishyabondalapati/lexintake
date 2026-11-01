"""Synthetic data generator tests.

The manifest is the ground truth Phase 7 grades against, so these check it is reproducible, that it
matches the files on disk, and that every field really is stated in the document it describes.
"""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from app.playbook import evaluate, load_playbook
from scripts.generate_synthetic import generate, plan_documents
from scripts.synth.fields import OPTIONAL_NDA_FIELDS
from scripts.synth.render import pdf_text

N = 40
MIN_N = 20  # smallest run the generator allows
SEED = 7

# A word that appears in an NDA only when that clause is present (null = clause absent).
CLAUSE_KEYWORDS = {
    "non_solicit_months": "solicit",
    "non_compete_months": "compet",
    "residuals_clause": "residual",
    "return_destroy_days": "destroy",
}


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> tuple[Path, dict]:
    out = tmp_path_factory.mktemp("synthetic")
    return out, generate(out, n=N, seed=SEED)


@pytest.fixture(scope="module")
def docs(run) -> list[dict]:
    return run[1]["documents"]


@pytest.fixture(scope="module")
def texts(run, docs) -> dict[str, str]:
    """Extracted text of every document, with whitespace runs collapsed to one space."""
    out, _ = run
    return {
        d["doc_id"]: re.sub(r"\s+", " ", pdf_text((out / d["file"]).read_bytes())).strip()
        for d in docs
    }


def by_id(docs):
    return {d["doc_id"]: d for d in docs}


def file_hashes(folder: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob("docs/*.pdf")}


def test_same_seed_gives_identical_manifest_and_files(run, tmp_path):
    out, manifest = run

    again = generate(tmp_path, n=N, seed=SEED)

    assert again == manifest
    assert (tmp_path / "manifest.json").read_bytes() == (out / "manifest.json").read_bytes()
    assert file_hashes(tmp_path) == file_hashes(out)


def test_different_seed_gives_different_documents(run, tmp_path):
    _, manifest = run

    other = generate(tmp_path, n=N, seed=SEED + 1)

    assert [d["sha256"] for d in other["documents"]] != [d["sha256"] for d in manifest["documents"]]


def test_every_entry_has_its_file_and_nothing_else(run, docs):
    out, _ = run
    on_disk = file_hashes(out)

    assert len(docs) == N
    assert {Path(d["file"]).name: d["sha256"] for d in docs} == on_disk


def test_rerun_replaces_the_previous_output(tmp_path):
    generate(tmp_path, n=N, seed=SEED)
    generate(tmp_path, n=MIN_N, seed=SEED)

    assert len(list((tmp_path / "docs").glob("*.pdf"))) == MIN_N


def test_scanned_pdfs_have_no_text_layer_and_digital_ones_do(docs, texts):
    scanned = [d for d in docs if d["scanned"]]
    digital = [d for d in docs if not d["scanned"]]

    assert scanned and digital
    for d in scanned:
        assert texts[d["doc_id"]] == "", d["doc_id"]
        assert 0.5 <= abs(d["scan_rotation_deg"]) <= 2.5
    for d in digital:
        assert len(texts[d["doc_id"]]) > 200, d["doc_id"]
        assert d["scan_rotation_deg"] is None


def test_evidence_appears_verbatim_in_digital_pdfs(docs, texts):
    for d in docs:
        if d["scanned"]:
            continue  # no text layer; Phase 7 matches these fuzzily against OCR output
        for field, evidence in d["evidence"].items():
            assert evidence in texts[d["doc_id"]], f"{d['doc_id']} {field}: {evidence!r}"


def test_every_present_field_has_evidence_and_absent_ones_do_not(docs):
    for d in docs:
        present = {k for k, v in d["fields"].items() if v is not None}
        assert set(d["evidence"]) == present, d["doc_id"]


def test_absent_nda_clauses_are_really_absent(docs, texts):
    for d in docs:
        if d["doc_type"] != "nda" or d["scanned"]:
            continue
        text = texts[d["doc_id"]].lower()
        for field, keyword in CLAUSE_KEYWORDS.items():
            assert (keyword in text) == (d["fields"][field] is not None), (d["doc_id"], field)


def test_null_semantics_for_nda_clauses(docs):
    for d in docs:
        if d["doc_type"] == "nda":
            for field in OPTIONAL_NDA_FIELDS:
                assert d["fields"][field] is not False  # absent is null, never false


def test_deviations_match_the_playbook(docs):
    playbook = load_playbook()
    ndas = [d for d in docs if d["doc_type"] == "nda"]

    for d in ndas:
        assert d["deviations"] == evaluate(playbook, d["fields"]), d["doc_id"]
    for d in docs:
        if d["doc_type"] != "nda":
            assert d["deviations"] is None
    assert any(d["deviations"] == [] for d in ndas)
    broken = {rule for d in ndas for rule in d["deviations"]}
    assert broken == {rule.id for rule in playbook.rules}


def test_exact_duplicates_share_a_hash(docs):
    index = by_id(docs)
    duplicates = [d for d in docs if d["tricky"] == "duplicate"]

    assert duplicates
    for d in duplicates:
        original = index[d["duplicate_of"]]
        assert d["sha256"] == original["sha256"]
        assert d["fields"] == original["fields"]


def test_near_duplicates_differ_in_exactly_one_field(docs):
    index = by_id(docs)
    near = [d for d in docs if d["tricky"] == "near_duplicate"]

    assert near
    for d in near:
        original = index[d["near_duplicate_of"]]
        changed = [k for k in d["fields"] if d["fields"][k] != original["fields"][k]]
        assert changed == [d["changed_field"]]
        assert d["sha256"] != original["sha256"]
        assert d["deviations"] != original["deviations"]
        # Same wording everywhere else.
        for field, evidence in d["evidence"].items():
            if field != d["changed_field"]:
                assert evidence == original["evidence"][field]


def test_unusual_title_ndas(docs):
    unusual = [d for d in docs if d["tricky"] == "unusual_title"]

    assert unusual
    for d in unusual:
        assert d["doc_type"] == "nda"
        assert "Disclosure" not in d["title"] and "Confidentiality Agreement" not in d["title"]


def test_default_plan_has_the_expected_mix():
    plans = plan_documents(500, seed=42, scanned_ratio=0.2, playbook=load_playbook())
    types = Counter(p.doc_type for p in plans)
    tricky = Counter(p.tricky for p in plans if p.tricky)

    assert len(plans) == 500
    assert set(types) == {"nda", "side_letter", "investor_notice", "legal_notice", "other"}
    assert 190 <= types["nda"] <= 210
    for doc_type in types:
        scanned = [p for p in plans if p.doc_type == doc_type and p.scanned]
        assert scanned, doc_type
    assert 95 <= sum(p.scanned for p in plans) <= 105
    assert tricky == {"duplicate": 5, "near_duplicate": 3, "unusual_title": 3}


def test_manifest_is_valid_json_on_disk(run):
    out, manifest = run

    assert json.loads((out / "manifest.json").read_text()) == manifest
