import pytest
from pydantic import ValidationError

from app.playbook import Playbook, Rule, check_playbook, evaluate, load_playbook

STANDARD_NDA = {
    "term_months": 24,
    "governing_law": "Delaware",
    "non_solicit_months": 12,
    "non_compete_months": None,
    "residuals_clause": None,
    "return_destroy_days": 30,
}


def test_real_playbook_loads():
    playbook = load_playbook()

    assert playbook.document_type == "nda"
    assert {rule.check for rule in playbook.rules} == {"max", "one_of", "absent", "present"}


def test_standard_nda_passes_every_rule():
    assert evaluate(load_playbook(), STANDARD_NDA) == []


@pytest.mark.parametrize(
    ("change", "broken_rule"),
    [
        ({"term_months": 36}, "max_term"),
        ({"governing_law": "California"}, "governing_law_allowed"),
        ({"non_solicit_months": 18}, "non_solicit_max"),
        ({"non_compete_months": 6}, "no_non_compete"),
        ({"residuals_clause": True}, "no_residuals"),
        ({"return_destroy_days": None}, "return_destroy_required"),
    ],
)
def test_each_rule_catches_its_deviation(change, broken_rule):
    assert evaluate(load_playbook(), STANDARD_NDA | change) == [broken_rule]


def test_null_semantics():
    max_rule = Rule(id="r", field="f", check="max", limit=12)
    one_of_rule = Rule(id="r", field="f", check="one_of", allowed=["Delaware"])

    # null = clause absent: no non-solicit is fine under a "max" rule...
    assert max_rule.passes(None)
    # ...but a required value that is missing can't be confirmed as allowed.
    assert not one_of_rule.passes(None)
    assert Rule(id="r", field="f", check="absent").passes(None)
    assert not Rule(id="r", field="f", check="present").passes(None)


def test_missing_field_counts_as_absent():
    fields = {k: v for k, v in STANDARD_NDA.items() if k != "return_destroy_days"}

    assert evaluate(load_playbook(), fields) == ["return_destroy_required"]


def test_max_rule_without_limit_is_rejected():
    with pytest.raises(ValidationError):
        Rule(id="r", field="f", check="max")


def test_unknown_check_is_rejected():
    with pytest.raises(ValidationError):
        Rule(id="r", field="f", check="between", limit=3)


def test_duplicate_rule_ids_are_rejected():
    rule = {"id": "same", "field": "f", "check": "absent"}

    with pytest.raises(ValidationError):
        Playbook(version=1, document_type="nda", rules=[rule, rule])


# --- check_playbook: pass / deviation / unknown with evidence -----------------------------------



def _entries(values: dict) -> dict:
    return {
        k: {
            "value": v,
            "status": "verified" if v is not None else "absent",
            "page": 1,
            "quote": f"quote for {k}" if v is not None else None,
        }
        for k, v in values.items()
    }


def _outcomes(extracted: dict) -> dict[str, str]:
    return {r.rule_id: r.outcome for r in check_playbook(load_playbook(), extracted)}


def test_checker_standard_nda_all_pass():
    assert set(_outcomes(_entries(STANDARD_NDA)).values()) == {"pass"}


@pytest.mark.parametrize(
    ("change", "broken_rule"),
    [
        ({"term_months": 36}, "max_term"),
        ({"governing_law": "California"}, "governing_law_allowed"),
        ({"non_solicit_months": 18}, "non_solicit_max"),
        ({"non_compete_months": 6}, "no_non_compete"),
        ({"residuals_clause": True}, "no_residuals"),
        ({"return_destroy_days": None}, "return_destroy_required"),
    ],
)
def test_checker_deviation_carries_evidence(change, broken_rule):
    results = check_playbook(load_playbook(), _entries(STANDARD_NDA | change))
    deviations = [r for r in results if r.outcome == "deviation"]

    assert [r.rule_id for r in deviations] == [broken_rule]
    assert deviations[0].reason
    if broken_rule != "return_destroy_required":  # an absent clause has no quote to show
        assert deviations[0].page == 1 and deviations[0].quote


@pytest.mark.parametrize("status", ["unverified", "missing"])
def test_unverified_or_missing_field_is_unknown_never_pass(status):
    extracted = _entries(STANDARD_NDA)
    extracted["term_months"] = {"value": 12, "status": status, "page": 1, "quote": "x"}

    assert _outcomes(extracted)["max_term"] == "unknown"


def test_field_not_extracted_is_unknown():
    extracted = _entries(STANDARD_NDA)
    del extracted["governing_law"]

    assert _outcomes(extracted)["governing_law_allowed"] == "unknown"


def test_absent_but_mentioned_and_value_not_in_quote_are_unknown():
    extracted = _entries(STANDARD_NDA)
    extracted["non_compete_months"]["flags"] = ["absent_but_mentioned"]
    extracted["term_months"]["value_in_quote"] = False

    outcomes = _outcomes(extracted)
    assert outcomes["no_non_compete"] == "unknown"
    assert outcomes["max_term"] == "unknown"


def test_null_governing_law_is_unknown():
    assert _outcomes(_entries(STANDARD_NDA | {"governing_law": None}))["governing_law_allowed"] == (
        "unknown"
    )
