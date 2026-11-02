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
    """Return the ids of the rules the fields break, in playbook order.

    A field missing from the dict is treated like null (clause absent).
    """
    return [rule.id for rule in playbook.rules if not rule.passes(fields.get(rule.field))]


PASS, DEVIATION, UNKNOWN = "pass", "deviation", "unknown"


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
    if status not in ("verified", "absent"):
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


def check_playbook(playbook: Playbook, extracted: dict[str, dict[str, Any]]) -> list[RuleResult]:
    """One result per rule, in playbook order.

    `extracted` maps field name -> {value, status, page, quote, flags, value_in_quote}: the shape
    stored in extractions.fields. Only a verified value (or a verified absence) is judged. Anything
    else, including a field that is missing from the dict, is `unknown`: never a pass.
    """
    results = []
    for rule in playbook.rules:
        entry = extracted.get(rule.field)
        value = entry.get("value") if entry else None
        page = entry.get("page") if entry else None
        quote = entry.get("quote") if entry else None

        def result(outcome: str, reason: str) -> RuleResult:
            return RuleResult(rule.id, rule.field, outcome, value, page, quote, reason)

        doubt = _untrusted_reason(entry)
        if doubt:
            results.append(result(UNKNOWN, doubt))
        elif value is None and rule.check == "one_of":
            results.append(result(UNKNOWN, "no value to compare with the allowed list"))
        elif rule.passes(value):
            results.append(result(PASS, _describe(rule, value)))
        else:
            results.append(result(DEVIATION, _describe(rule, value)))
    return results
