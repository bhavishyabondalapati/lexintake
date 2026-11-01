"""What the model must return, as Pydantic models.

Two layers:
- **Value models** (`NdaFields`, `CapitalCallFields`, ...) have exactly the keys and types of the
  synthetic manifest's `fields`, so a result can be compared with the ground truth key for key.
  Null means the clause is absent, as in the manifest.
- **Cited models** are generated from the value models. Each field becomes
  `{value, page, quote, confidence}`, because every value has to point at the text it came from.
  This is what the model fills in; `values()` flattens it back to the manifest shape.

The field descriptions double as the field definitions in the prompt, so the two can't drift.
"""

import types
from datetime import date
from enum import StrEnum
from typing import Any, ClassVar, Literal, Union, get_args, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    create_model,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = 1


class DocType(StrEnum):
    NDA = "nda"
    SIDE_LETTER = "side_letter"
    INVESTOR_NOTICE = "investor_notice"
    LEGAL_NOTICE = "legal_notice"
    OTHER = "other"


class Subtype(StrEnum):
    CAPITAL_CALL = "capital_call"
    DISTRIBUTION = "distribution"
    ANNUAL_MEETING = "annual_meeting"
    DEMAND_LETTER = "demand_letter"
    NOTICE_OF_BREACH = "notice_of_breach"
    SUBPOENA = "subpoena"
    INVOICE = "invoice"
    NEWSLETTER = "newsletter"
    BOARD_MINUTES = "board_minutes"
    MARKETING_FLYER = "marketing_flyer"


# Which subtypes each type has. NDAs and side letters have none (subtype is null).
SUBTYPES: dict[DocType, tuple[Subtype, ...]] = {
    DocType.NDA: (),
    DocType.SIDE_LETTER: (),
    DocType.INVESTOR_NOTICE: (Subtype.CAPITAL_CALL, Subtype.DISTRIBUTION, Subtype.ANNUAL_MEETING),
    DocType.LEGAL_NOTICE: (Subtype.DEMAND_LETTER, Subtype.NOTICE_OF_BREACH, Subtype.SUBPOENA),
    DocType.OTHER: (
        Subtype.INVOICE,
        Subtype.NEWSLETTER,
        Subtype.BOARD_MINUTES,
        Subtype.MARKETING_FLYER,
    ),
}

DATE = "Date in ISO format YYYY-MM-DD."
USD = "Whole US dollars as an integer, e.g. 1250000 for $1,250,000.00."
# A clause that is either there (true) or not (null). The manifest never uses false for these.
TrueOrAbsent = Literal[True] | None


# --- Value models: exactly the manifest's fields ------------------------------------------------


class Fields(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NdaFields(Fields):
    counterparty: str = Field(
        description="The other party: the company the fund is signing with (not the fund side)."
    )
    fund_party: str = Field(
        description="The fund-side party: the fund (e.g. '... Fund III, L.P.') or its management "
        "company (e.g. '... Management, LLC')."
    )
    effective_date: date = Field(description=f"The effective date of the agreement. {DATE}")
    mutual: bool = Field(
        description="true if both parties disclose and protect information (a mutual NDA); false "
        "if only one side discloses. Quote the sentence that shows who discloses to whom."
    )
    term_months: int = Field(
        description="How long the confidentiality obligations last, in months (two (2) years = 24)."
    )
    governing_law: str = Field(
        description="The governing-law jurisdiction name only, e.g. 'Delaware', 'New York', "
        "'England and Wales', 'Cayman Islands' (not 'the State of Delaware')."
    )
    non_solicit_months: int | None = Field(
        description="Length of the non-solicitation (of employees) restriction in months; null "
        "if the agreement has no non-solicit clause."
    )
    non_compete_months: int | None = Field(
        description="Length of the non-competition restriction in months; null if there is no "
        "non-compete clause."
    )
    residuals_clause: TrueOrAbsent = Field(
        description="true if the agreement has a residuals clause (lets a party use information "
        "retained in unaided memory); null if it has none."
    )
    return_destroy_days: int | None = Field(
        description="Days allowed to return or destroy confidential information after a request; "
        "null if there is no return/destroy obligation."
    )


class SideLetterFields(Fields):
    investor: str = Field(description="The investor (limited partner) the side letter is with.")
    fund: str = Field(description="The fund, e.g. '... Fund IV, L.P.'.")
    effective_date: date = Field(description=f"The date of the side letter. {DATE}")
    commitment_usd: int = Field(description=f"The investor's capital commitment. {USD}")
    management_fee_pct: float | None = Field(
        description="The management fee rate granted, as a percentage number (1.25 for 1.25%); "
        "null if the letter doesn't set a fee."
    )
    mfn: TrueOrAbsent = Field(
        description="true if most-favoured-nation rights are granted; null if not mentioned."
    )
    co_investment: TrueOrAbsent = Field(
        description="true if co-investment rights are granted; null if not mentioned."
    )
    advisory_committee_seat: TrueOrAbsent = Field(
        description="true if the investor gets an advisory committee seat; null if not mentioned."
    )


class InvestorNoticeFields(Fields):
    fund: str = Field(description="The fund sending the notice.")
    investor: str = Field(description="The investor the notice is addressed to.")
    notice_date: date = Field(description=f"The date of the notice. {DATE}")


class CapitalCallFields(InvestorNoticeFields):
    amount_usd: int = Field(description=f"The amount called from this investor. {USD}")
    due_date: date = Field(description=f"The date the capital is due. {DATE}")


class DistributionFields(InvestorNoticeFields):
    amount_usd: int = Field(description=f"The amount distributed to this investor. {USD}")
    payment_date: date = Field(description=f"The date the distribution is paid. {DATE}")


class AnnualMeetingFields(InvestorNoticeFields):
    meeting_date: date = Field(description=f"The date of the meeting. {DATE}")
    meeting_location: str = Field(description="Where the meeting is held, as 'City, State'.")


class LegalNoticeFields(Fields):
    claimant: str = Field(description="The party making the claim or demand (the sender side).")
    respondent: str = Field(description="The party the notice is addressed to / claimed against.")
    notice_date: date = Field(description=f"The date of the notice. {DATE}")


class DemandLetterFields(LegalNoticeFields):
    amount_claimed_usd: int = Field(description=f"The amount demanded. {USD}")
    response_deadline: date = Field(description=f"The deadline to pay or respond. {DATE}")


class NoticeOfBreachFields(LegalNoticeFields):
    agreement_date: date = Field(
        description=f"The date of the agreement said to be breached. {DATE}"
    )
    cure_period_days: int = Field(description="Days allowed to cure the breach.")


class SubpoenaFields(LegalNoticeFields):
    court: str = Field(description="The full name of the court, as written.")
    case_number: str = Field(description="The case number, as written (e.g. '1:24-cv-01234').")
    response_deadline: date = Field(description=f"The date by which to respond or comply. {DATE}")


class InvoiceFields(Fields):
    vendor: str = Field(description="The company issuing the invoice.")
    invoice_number: str = Field(description="The invoice number, as written.")
    invoice_date: date = Field(description=f"The invoice date. {DATE}")
    amount_usd: int = Field(
        description="The total amount due, in whole US dollars (round cents down)."
    )
    due_date: date = Field(description=f"The payment due date. {DATE}")


class NewsletterFields(Fields):
    publisher: str = Field(description="The company publishing the newsletter.")
    issue_date: date = Field(description=f"The issue date. {DATE}")


class BoardMinutesFields(Fields):
    company: str = Field(description="The company whose board met.")
    meeting_date: date = Field(description=f"The date of the board meeting. {DATE}")


class MarketingFlyerFields(Fields):
    vendor: str = Field(description="The company advertising.")
    event_date: date = Field(description=f"The date of the event being advertised. {DATE}")


FIELD_MODELS: dict[tuple[DocType, Subtype | None], type[Fields]] = {
    (DocType.NDA, None): NdaFields,
    (DocType.SIDE_LETTER, None): SideLetterFields,
    (DocType.INVESTOR_NOTICE, Subtype.CAPITAL_CALL): CapitalCallFields,
    (DocType.INVESTOR_NOTICE, Subtype.DISTRIBUTION): DistributionFields,
    (DocType.INVESTOR_NOTICE, Subtype.ANNUAL_MEETING): AnnualMeetingFields,
    (DocType.LEGAL_NOTICE, Subtype.DEMAND_LETTER): DemandLetterFields,
    (DocType.LEGAL_NOTICE, Subtype.NOTICE_OF_BREACH): NoticeOfBreachFields,
    (DocType.LEGAL_NOTICE, Subtype.SUBPOENA): SubpoenaFields,
    (DocType.OTHER, Subtype.INVOICE): InvoiceFields,
    (DocType.OTHER, Subtype.NEWSLETTER): NewsletterFields,
    (DocType.OTHER, Subtype.BOARD_MINUTES): BoardMinutesFields,
    (DocType.OTHER, Subtype.MARKETING_FLYER): MarketingFlyerFields,
}


def field_model(doc_type: DocType, subtype: Subtype | None) -> type[Fields]:
    return FIELD_MODELS[(doc_type, subtype)]


def is_optional(fields: type[Fields], name: str) -> bool:
    """True when null is a valid answer (the clause may be absent)."""
    annotation = fields.model_fields[name].annotation
    return type(None) in get_args(annotation)


def is_true_or_absent(fields: type[Fields], name: str) -> bool:
    return fields.model_fields[name].annotation == TrueOrAbsent


# --- Cited models: what the model actually returns ----------------------------------------------


class Cited(BaseModel):
    """One extracted field with its evidence. `value` is typed per field by `cited_model()`."""

    model_config = ConfigDict(extra="forbid")

    value: Any = Field(description="The value, or null if the document doesn't contain it.")
    page: int | None = Field(
        description="1-based page number the quote is on; null if value is null."
    )
    quote: str | None = Field(
        description="The exact text from the document that states the value, copied character "
        "for character (one sentence or line is enough); null if value is null."
    )
    confidence: float = Field(
        ge=0, le=1, description="How sure you are that the value is right, from 0 to 1."
    )

    @model_validator(mode="after")
    def evidence_matches_value(self) -> "Cited":
        if self.value is None:
            self.page = None  # nothing to cite for an absent clause
            self.quote = None
        elif self.page is None or not (self.quote or "").strip():
            raise ValueError("a non-null value needs a page and a quote")
        return self

    @field_validator("page")
    @classmethod
    def page_exists(cls, page: int | None, info: ValidationInfo) -> int | None:
        page_count = (info.context or {}).get("page_count")
        if page is not None and (page < 1 or (page_count and page > page_count)):
            raise ValueError(f"page {page} is not in the document (1..{page_count})")
        return page


class CitedFields(BaseModel):
    """Base for the generated cited models. `FIELDS` points back at the value model."""

    model_config = ConfigDict(extra="forbid")
    FIELDS: ClassVar[type[Fields]]

    def cited(self) -> dict[str, Cited]:
        return {name: getattr(self, name) for name in type(self).model_fields}

    def values(self) -> dict[str, Any]:
        """The plain manifest-shaped values (dates as ISO strings)."""
        return {name: c.model_dump(mode="json")["value"] for name, c in self.cited().items()}


def _api_type(annotation: Any) -> Any:
    """The value type the API is asked for. Always nullable: the model may say "not found" for any
    field instead of guessing. TrueOrAbsent is asked as a boolean (the API's JSON schema can't say
    "only true"), and false is turned into null after parsing."""
    if annotation == TrueOrAbsent:
        return bool | None
    if get_origin(annotation) in (Union, types.UnionType):
        return annotation
    return annotation | None


def _false_means_absent(cls: type, value: Any) -> Any:
    return None if value is False else value


def _cited_field_class(fields: type[Fields], name: str) -> type[Cited]:
    info = fields.model_fields[name]
    validators = {}
    if is_true_or_absent(fields, name):
        validators["false_means_absent"] = field_validator("value")(_false_means_absent)
    return create_model(
        f"{fields.__name__.removesuffix('Fields')}_{name}",
        __base__=Cited,
        __validators__=validators,
        value=(_api_type(info.annotation), Field(description=info.description)),
    )


_CITED_CACHE: dict[type[Fields], type[CitedFields]] = {}


def cited_model(fields: type[Fields]) -> type[CitedFields]:
    """`CitedNdaFields` etc.: the same keys as the value model, each wrapped in a Cited."""
    if fields not in _CITED_CACHE:
        model = create_model(
            f"Cited{fields.__name__}",
            __base__=CitedFields,
            **{name: (_cited_field_class(fields, name), ...) for name in fields.model_fields},
        )
        model.FIELDS = fields
        _CITED_CACHE[fields] = model
    return _CITED_CACHE[fields]


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_type: DocType = Field(description="The document type.")
    subtype: Subtype | None = Field(
        description="The subtype; must be one of the subtypes listed for doc_type, or null for "
        "nda and side_letter."
    )
    confidence: float = Field(ge=0, le=1, description="How sure you are, from 0 to 1.")
    page: int = Field(description="1-based page of the quote.")
    quote: str = Field(
        description="Exact text from the document that shows its type, usually the title or "
        "opening line, copied character for character."
    )

    @model_validator(mode="after")
    def subtype_fits_type(self) -> "Classification":
        allowed = SUBTYPES[self.doc_type]
        if (self.subtype is None) != (not allowed) or (allowed and self.subtype not in allowed):
            raise ValueError(f"subtype {self.subtype} doesn't fit doc_type {self.doc_type}")
        return self

    @field_validator("page")
    @classmethod
    def page_exists(cls, page: int, info: ValidationInfo) -> int:
        page_count = (info.context or {}).get("page_count")
        if page < 1 or (page_count and page > page_count):
            raise ValueError(f"page {page} is not in the document (1..{page_count})")
        return page
