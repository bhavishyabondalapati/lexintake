"""Sample the ground-truth fields for each document type.

Every value here ends up both in the manifest and in the document text, so this module decides what
the "right answer" is for each synthetic document.

Null semantics: None means the clause is absent from the document. A field that doesn't apply to a
document type is simply not in its dict.
"""

import random
from datetime import date, timedelta
from typing import Any

from faker import Faker

from app.playbook import Playbook, Rule

# Fixed range, never relative to today, so the same seed always gives the same dates.
DATE_START = date(2022, 1, 1)
DATE_END = date(2025, 12, 31)

# Fictional fund families. Any resemblance to a real firm is coincidental.
FUND_FAMILIES = [
    "Kestrel Ridge Capital",
    "Tamarind Harbor Partners",
    "Bellhaven Equity Partners",
    "Ostrander Hill Capital",
    "Larchmont Vale Partners",
]
ROMAN = ["II", "III", "IV", "V", "VI", "VII"]

INDUSTRIES = [
    "Logistics",
    "Health",
    "Software",
    "Foods",
    "Industrial",
    "Materials",
    "Analytics",
    "Packaging",
    "Dental Partners",
    "Energy Services",
    "Robotics",
    "Aerospace",
]
SUFFIXES = ["Inc.", "Corp.", "Holdings, Inc.", "LLC", "LLC"]

GOVERNING_LAWS = [
    "Delaware",
    "New York",
    "California",
    "Texas",
    "Florida",
    "England and Wales",
    "Cayman Islands",
]

# Values the generator may pick for each NDA term the playbook checks.
NDA_POOLS: dict[str, list[Any]] = {
    "term_months": [6, 12, 18, 24, 36, 48, 60],
    "governing_law": GOVERNING_LAWS,
    "non_solicit_months": [6, 9, 12, 18, 24],
    "non_compete_months": [6, 12, 18],
    "residuals_clause": [True],
    "return_destroy_days": [10, 15, 30, 45],
}
# Clauses that may be missing from an NDA (null = absent). The rest are always there.
OPTIONAL_NDA_FIELDS = {
    "non_solicit_months",
    "non_compete_months",
    "residuals_clause",
    "return_destroy_days",
}


def iso(d: date) -> str:
    return d.isoformat()


def random_date(rng: random.Random, start: date = DATE_START, end: date = DATE_END) -> date:
    return start + timedelta(days=rng.randrange((end - start).days + 1))


def later(rng: random.Random, start: str, low: int, high: int) -> str:
    return iso(date.fromisoformat(start) + timedelta(days=rng.randint(low, high)))


def fund_name(rng: random.Random) -> str:
    return f"{rng.choice(FUND_FAMILIES)} Fund {rng.choice(ROMAN)}, L.P."


def management_company(rng: random.Random) -> str:
    return f"{rng.choice(FUND_FAMILIES)} Management, LLC"


def company(rng: random.Random, fake: Faker) -> str:
    return f"{fake.last_name()} {rng.choice(INDUSTRIES)} {rng.choice(SUFFIXES)}"


def investor_name(rng: random.Random, fake: Faker) -> str:
    patterns = [
        f"{fake.last_name()} Family Office LLC",
        f"{fake.city()} Municipal Employees' Pension Fund",
        f"The {fake.last_name()} Foundation",
        f"{fake.last_name()} University Endowment Fund",
        f"{fake.last_name()} Insurance Company",
    ]
    return rng.choice(patterns)


def money(rng: random.Random, low: int, high: int, step: int) -> int:
    return rng.randrange(low // step, high // step + 1) * step


# --- NDA: terms driven by the playbook ----------------------------------------------------------


def value_choices(rule: Rule, pool: list[Any], optional: bool, passing: bool) -> list[Any]:
    """The values for rule.field that make the rule pass (or break), given null = absent."""
    match rule.check:
        case "max":
            ok = [v for v in pool if v <= rule.limit] + ([None] if optional else [])
            bad = [v for v in pool if v > rule.limit]
        case "one_of":
            ok = [v for v in pool if v in rule.allowed]
            bad = [v for v in pool if v not in rule.allowed]
        case "absent":
            ok, bad = [None], list(pool)
        case "present":
            ok, bad = list(pool), [None]
    choices = ok if passing else bad
    if not choices:
        state = "passing" if passing else "breaking"
        raise ValueError(f"no {state} value for rule {rule.id!r} in pool {pool}")
    return choices


def sample_nda_terms(rng: random.Random, playbook: Playbook, broken: set[str]) -> dict[str, Any]:
    """Pick each playbook-checked term so that exactly the rules in `broken` fail."""
    rules = {rule.field: rule for rule in playbook.rules}
    terms = {}
    for field, pool in NDA_POOLS.items():
        optional = field in OPTIONAL_NDA_FIELDS
        rule = rules.get(field)
        if rule is None:
            choices = pool + ([None] if optional else [])
        else:
            choices = value_choices(rule, pool, optional, passing=rule.id not in broken)
        terms[field] = rng.choice(choices)
    return terms


def flip_one_rule(rng: random.Random, playbook: Playbook, fields: dict[str, Any]) -> str:
    """Change one playbook-checked field so its rule flips (pass <-> break). Returns the field."""
    rule = rng.choice(playbook.rules)
    passing = rule.passes(fields[rule.field])
    optional = rule.field in OPTIONAL_NDA_FIELDS
    choices = value_choices(rule, NDA_POOLS[rule.field], optional, passing=not passing)
    fields[rule.field] = rng.choice(choices)
    return rule.field


def sample_nda(
    rng: random.Random, fake: Faker, playbook: Playbook, broken: set[str]
) -> tuple[None, dict[str, Any]]:
    fund_party = rng.choice([fund_name(rng), management_company(rng)])
    fields = {
        "counterparty": company(rng, fake),
        "fund_party": fund_party,
        "effective_date": iso(random_date(rng)),
        "mutual": rng.random() < 0.4,
    }
    fields |= sample_nda_terms(rng, playbook, broken)
    return None, fields


# --- Other document types -----------------------------------------------------------------------


def sample_side_letter(rng: random.Random, fake: Faker) -> tuple[None, dict[str, Any]]:
    fields = {
        "investor": investor_name(rng, fake),
        "fund": fund_name(rng),
        "effective_date": iso(random_date(rng)),
        "commitment_usd": money(rng, 5_000_000, 150_000_000, 250_000),
        "management_fee_pct": rng.choice([None, 1.0, 1.25, 1.5]),
        "mfn": rng.choice([True, None]),
        "co_investment": rng.choice([True, None]),
        "advisory_committee_seat": rng.choice([True, None]),
    }
    if all(fields[k] is None for k in ("management_fee_pct", "mfn", "co_investment")):
        fields["mfn"] = True  # a side letter always grants something
    return None, fields


def sample_investor_notice(rng: random.Random, fake: Faker) -> tuple[str, dict[str, Any]]:
    subtype = rng.choices(["capital_call", "distribution", "annual_meeting"], [45, 35, 20])[0]
    notice_date = iso(random_date(rng))
    fields: dict[str, Any] = {
        "fund": fund_name(rng),
        "investor": investor_name(rng, fake),
        "notice_date": notice_date,
    }
    if subtype == "capital_call":
        fields["amount_usd"] = money(rng, 50_000, 5_000_000, 1_000)
        fields["due_date"] = later(rng, notice_date, 10, 15)
    elif subtype == "distribution":
        fields["amount_usd"] = money(rng, 25_000, 8_000_000, 1_000)
        fields["payment_date"] = later(rng, notice_date, 5, 10)
    else:
        fields["meeting_date"] = later(rng, notice_date, 20, 45)
        fields["meeting_location"] = f"{fake.city()}, {fake.state()}"
    return subtype, fields


def sample_legal_notice(rng: random.Random, fake: Faker) -> tuple[str, dict[str, Any]]:
    subtype = rng.choices(["demand_letter", "notice_of_breach", "subpoena"], [45, 35, 20])[0]
    notice_date = iso(random_date(rng))
    fields: dict[str, Any] = {
        "claimant": company(rng, fake),
        "respondent": management_company(rng),
        "notice_date": notice_date,
    }
    if subtype == "demand_letter":
        fields["amount_claimed_usd"] = money(rng, 20_000, 3_000_000, 500)
        fields["response_deadline"] = later(rng, notice_date, 10, 30)
    elif subtype == "notice_of_breach":
        fields["agreement_date"] = iso(
            random_date(rng, DATE_START, date.fromisoformat(notice_date))
        )
        fields["cure_period_days"] = rng.choice([10, 15, 30])
    else:
        district = rng.choice(["Southern District of New York", "District of Delaware"])
        fields["court"] = f"United States District Court for the {district}"
        fields["case_number"] = f"1:{notice_date[2:4]}-cv-{rng.randrange(10000):05d}"
        fields["response_deadline"] = later(rng, notice_date, 14, 30)
    return subtype, fields


def sample_other(rng: random.Random, fake: Faker) -> tuple[str, dict[str, Any]]:
    subtype = rng.choice(["invoice", "newsletter", "board_minutes", "marketing_flyer"])
    when = iso(random_date(rng))
    if subtype == "invoice":
        terms = rng.choice([15, 30, 45])
        return subtype, {
            "vendor": company(rng, fake),
            "invoice_number": f"INV-{rng.randrange(100000):06d}",
            "invoice_date": when,
            "amount_usd": money(rng, 500, 95_000, 25),
            "due_date": iso(date.fromisoformat(when) + timedelta(days=terms)),
        }
    if subtype == "newsletter":
        return subtype, {"publisher": company(rng, fake), "issue_date": when}
    if subtype == "board_minutes":
        return subtype, {"company": company(rng, fake), "meeting_date": when}
    return subtype, {"vendor": company(rng, fake), "event_date": when}


SAMPLERS = {
    "side_letter": sample_side_letter,
    "investor_notice": sample_investor_notice,
    "legal_notice": sample_legal_notice,
    "other": sample_other,
}
