"""NDA playbook: load the rules from YAML and evaluate a document's fields against them.

Phase 1 uses this to label synthetic NDAs. Phase 4 extends it into the full checker
(pass / deviation / unknown, with evidence).
"""

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
