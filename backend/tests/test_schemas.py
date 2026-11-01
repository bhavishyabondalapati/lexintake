import json
from pathlib import Path

import anthropic
import pytest
from pydantic import ValidationError

from app.extraction.schemas import (
    FIELD_MODELS,
    SUBTYPES,
    Classification,
    DocType,
    NdaFields,
    Subtype,
    cited_model,
    field_model,
)
from tests.conftest import FIXTURES

TYPES = json.loads((FIXTURES / "manifest_types.json").read_text())["documents"]
FULL_MANIFEST = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "manifest.json"


def _key(entry):
    return DocType(entry["doc_type"]), Subtype(entry["subtype"]) if entry["subtype"] else None


def test_every_type_and_subtype_has_a_field_model():
    expected = {(t, s) for t, subs in SUBTYPES.items() for s in (subs or (None,))}
    assert set(FIELD_MODELS) == expected
    assert {_key(e) for e in TYPES} == expected  # the fixture covers them all


@pytest.mark.parametrize("entry", TYPES, ids=lambda e: f"{e['doc_type']}-{e['subtype']}")
def test_field_model_matches_the_manifest_key_for_key(entry):
    model = field_model(*_key(entry))

    assert set(model.model_fields) == set(entry["fields"])
    # Round trip: the manifest validates, and dumps back to exactly the same JSON.
    assert model.model_validate(entry["fields"]).model_dump(mode="json") == entry["fields"]


def test_nda_field_names_include_fund_party():
    assert list(NdaFields.model_fields) == [
        "counterparty",
        "fund_party",
        "effective_date",
        "mutual",
        "term_months",
        "governing_law",
        "non_solicit_months",
        "non_compete_months",
        "residuals_clause",
        "return_destroy_days",
    ]


def test_absent_clause_is_null_and_false_is_not_allowed_for_true_or_absent():
    fields = next(e["fields"] for e in TYPES if e["doc_type"] == "nda")
    NdaFields.model_validate({**fields, "residuals_clause": None})
    with pytest.raises(ValidationError):
        NdaFields.model_validate({**fields, "residuals_clause": False})
    with pytest.raises(ValidationError):
        NdaFields.model_validate({**fields, "counterparty": None})  # always present in an NDA


@pytest.mark.skipif(not FULL_MANIFEST.exists(), reason="run `make synthetic` for the full manifest")
def test_all_500_manifest_entries_validate():
    for entry in json.loads(FULL_MANIFEST.read_text())["documents"]:
        model = field_model(*_key(entry))
        assert model.model_validate(entry["fields"]).model_dump(mode="json") == entry["fields"]


def _answer(model, overrides=None):
    answer = {
        name: {"value": None, "page": None, "quote": None, "confidence": 0.9}
        for name in model.model_fields
    }
    return answer | (overrides or {})


def test_cited_model_has_the_same_keys_and_flattens_to_values():
    cited = cited_model(NdaFields)
    answer = _answer(
        cited,
        {
            "term_months": {"value": 24, "page": 1, "quote": "two (2) years", "confidence": 0.9},
            "residuals_clause": {"value": False, "page": None, "quote": None, "confidence": 0.8},
        },
    )

    result = cited.model_validate(answer, context={"page_count": 1})

    assert list(cited.model_fields) == list(NdaFields.model_fields)
    assert result.values()["term_months"] == 24
    assert result.values()["residuals_clause"] is None  # false is read as "absent"


def test_a_value_without_a_quote_is_invalid():
    cited = cited_model(NdaFields)
    bad = {"value": 24, "page": 1, "quote": " ", "confidence": 0.9}
    with pytest.raises(ValidationError, match="needs a page and a quote"):
        cited.model_validate(_answer(cited, {"term_months": bad}))


def test_a_page_outside_the_document_is_invalid():
    cited = cited_model(NdaFields)
    bad = {"value": 24, "page": 3, "quote": "two (2) years", "confidence": 0.9}
    with pytest.raises(ValidationError, match="page 3"):
        cited.model_validate(_answer(cited, {"term_months": bad}), context={"page_count": 2})


def test_confidence_must_be_between_0_and_1():
    cited = cited_model(NdaFields)
    bad = {"value": 24, "page": 1, "quote": "two (2) years", "confidence": 1.5}
    with pytest.raises(ValidationError):
        cited.model_validate(_answer(cited, {"term_months": bad}))


@pytest.mark.parametrize(
    ("doc_type", "subtype", "ok"),
    [
        ("nda", None, True),
        ("nda", "invoice", False),
        ("investor_notice", "capital_call", True),
        ("investor_notice", None, False),
        ("investor_notice", "subpoena", False),
    ],
)
def test_classification_subtype_must_fit_the_type(doc_type, subtype, ok):
    data = {"doc_type": doc_type, "subtype": subtype, "confidence": 0.9, "page": 1, "quote": "x"}
    if ok:
        Classification.model_validate(data)
    else:
        with pytest.raises(ValidationError):
            Classification.model_validate(data)


@pytest.mark.parametrize("model", [Classification, *map(cited_model, FIELD_MODELS.values())])
def test_api_schema_is_accepted_by_the_sdk_transform(model):
    schema = anthropic.transform_schema(model)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(model.model_fields)
