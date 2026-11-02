"""NDA playbook: load the rules from YAML and check a document's fields against them.

`evaluate` is the plain yes/no check on trusted values (the synthetic generator labels NDAs with
it). `check_playbook` is the real checker: pass / deviation / unknown, with the evidence behind
each answer. It uses the same `Rule.passes`, so the two can never disagree about a trusted value.
"""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, model_validator

DEFAULT_PLAYBOOK_PATH = Path(__file__).resolve().parents[2] / "playbook" / "nda_playbook.yaml"


class Rule(BaseModel):
    id: str
    field: str
    check: Literal["max", "one_of", "absent", "present"]
    limit: int | None = None
    allowed: list[str] | None = None
    description: str = ""

    @model_validator(mode="after")
    def params_match_check(self) -> "Rule":
        if self.check == "max" and self.limit is None:
            raise ValueError(f"rule {self.id!r}: check 'max' needs a 'limit'")
        if self.check == "one_of" and not self.allowed:
            raise ValueError(f"rule {self.id!r}: check 'one_of' needs a non-empty 'allowed' list")
        return self

    def passes(self, value: Any) -> bool:
        """Null means the clause is absent from the document."""
        match self.check:
            case "max":
                return value is None or value <= self.limit
            case "one_of":
                return value in self.allowed
            case "absent":
                return value is None
            case "present":
                return value is not None


class Playbook(BaseModel):
    version: int
    document_type: str
    rules: list[Rule]

    @model_validator(mode="after")
    def unique_rule_ids(self) -> "Playbook":
        ids = [rule.id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule ids must be unique")
        return self


def load_playbook(path: Path = DEFAULT_PLAYBOOK_PATH) -> Playbook:
    return Playbook.model_validate(yaml.safe_load(Path(path).read_text()))


def evaluate(playbook: Playbook, fields: dict[str, Any]) -> list[str]:
    """Return the ids of the rules plain trusted values break, in playbook order.

    A thin wrapper over `check_playbook`, so the rules are interpreted in one place. The values
    count as verified; a missing field or null means the clause is absent. A null where a value is
    required (governing law) can't be confirmed, so it counts as a break here too.
    """
    entries = {
        rule.field: {"value": fields.get(rule.field), "status": "verified"}
        if fields.get(rule.field) is not None
        else _ABSENT
        for rule in playbook.rules
    }
    results = check_playbook(playbook, entries)
    return [r.rule_id for r in results if r.outcome != PASS]


PASS, DEVIATION, UNKNOWN = "pass", "deviation", "unknown"
_ABSENT = {"value": None, "status": "absent"}


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    field: str
    outcome: Literal["pass", "deviation", "unknown"]
    value: Any
    page: int | None
    quote: str | None
    reason: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _untrusted_reason(entry: dict[str, Any] | None) -> str | None:
    """Why this extracted field can't be relied on, or None if it can."""
    if entry is None:
        return "field was not extracted"
    status = entry.get("status")
    if status == "unverified":
        return "the quote for this field was not found in the document"
    if status == "missing":
        return "a required field was left empty"
    if status not in ("verified", "absent", "overridden"):
        return f"unrecognised field status {status!r}"
    if status == "verified" and entry.get("value_in_quote") is False:
        return "the value does not appear in its quote"
    if "absent_but_mentioned" in (entry.get("flags") or []):
        return "marked absent, but the text mentions this clause"
    return None


def _describe(rule: Rule, value: Any) -> str:
    match rule.check:
        case "max":
            return f"{value} vs limit {rule.limit}" if value is not None else "no such clause"
        case "one_of":
            return f"{value!r} vs allowed {rule.allowed}"
        case "absent":
            return "clause must not be present"
        case "present":
            return "clause must be present"


def _check_rule(rule: Rule, entry: dict[str, Any] | None) -> RuleResult:
    value = entry.get("value") if entry else None
    page = entry.get("page") if entry else None
    quote = entry.get("quote") if entry else None

    def result(outcome: str, reason: str) -> RuleResult:
        return RuleResult(rule.id, rule.field, outcome, value, page, quote, reason)

    doubt = _untrusted_reason(entry)
    if doubt:
        return result(UNKNOWN, doubt)
    if value is None and rule.check == "one_of":
        return result(UNKNOWN, "no value to compare with the allowed list")
    if rule.passes(value):
        return result(PASS, _describe(rule, value))
    return result(DEVIATION, _describe(rule, value))


def check_playbook(playbook: Playbook, extracted: dict[str, dict[str, Any]]) -> list[RuleResult]:
    """One result per rule, in playbook order.

    `extracted` maps field name -> {value, status, page, quote, flags, value_in_quote}: the shape
    stored in extractions.fields. Only a verified value (or a verified absence) is judged. Anything
    else, including a field that is missing from the dict, is `unknown`: never a pass.
    """
    return [_check_rule(rule, extracted.get(rule.field)) for rule in playbook.rules]


def self_check() -> int:
    """Load the playbook and run the checker on a standard NDA. Used by the compose smoke test to
    prove the playbook file is inside the container and the checker works there."""
    playbook = load_playbook()
    standard = {
        "term_months": 24,
        "governing_law": "Delaware",
        "non_solicit_months": 12,
        "non_compete_months": None,
        "residuals_clause": None,
        "return_destroy_days": 30,
    }
    extracted = {
        k: {
            "value": v,
            "status": "verified" if v is not None else "absent",
            "page": 1,
            "quote": "x",
        }
        for k, v in standard.items()
    }
    outcomes = {r.outcome for r in check_playbook(playbook, extracted)}
    print(f"playbook v{playbook.version}: {len(playbook.rules)} rules, standard NDA -> {outcomes}")
    return 0 if outcomes == {PASS} else 1


if __name__ == "__main__":
    raise SystemExit(self_check())
