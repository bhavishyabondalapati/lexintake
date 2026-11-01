"""Turn ground-truth fields into document text, recording the exact evidence for each field.

Evidence is the exact text in the document that states a field. Every non-null field gets one, and
absent clauses (null) get none. The tests check each evidence string really appears in the PDF.

Each builder draws all of its random choices (wording variants, names, addresses) at the top,
before looking at which clauses are present. That way a near-duplicate NDA with one changed clause
gets exactly the same text everywhere else.

Plain ASCII only (straight quotes, no dashes or symbols), so text extracted from the PDF matches
the evidence character for character.
"""

# Contract wording reads better as long string lines than chopped to fit 100 columns.
# ruff: noqa: E501

import random
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from faker import Faker

ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
).split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()

NDA_TITLES = [
    "Confidentiality Agreement",
    "Non-Disclosure Agreement",
    "Confidentiality and Non-Disclosure Agreement",
]
MUTUAL_NDA_TITLES = ["Mutual Non-Disclosure Agreement", "Mutual Confidentiality Agreement"]
UNUSUAL_NDA_TITLES = [
    "Evaluation Material Letter Agreement",
    "Mutual Non-Use and Confidentiality Undertaking",
    "Agreement Regarding the Exchange of Proprietary Information",
]
PROJECT_NAMES = ["Falcon", "Granite", "Juniper", "Meridian", "Atlas", "Copper", "Harbor", "Summit"]


@dataclass
class Block:
    text: str
    style: str = "body"  # body | title | heading | line | small
    heading: str = ""  # bold run-in heading, e.g. "3. Term."


@dataclass
class Document:
    title: str
    blocks: list[Block] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)

    def add(self, *parts: str | tuple[str, ...], style: str = "body", heading: str = "") -> None:
        """Add a paragraph made of parts. A part (text, "field_a", ...) is evidence for those fields."""
        texts = []
        for part in parts:
            if isinstance(part, tuple):
                text, *fields = part
                for name in fields:
                    self.evidence.setdefault(name, text)
            else:
                text = part
            texts.append(text)
        self.blocks.append(Block(" ".join(texts), style, heading))


def words(n: int) -> str:
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + (f"-{ONES[n % 10]}" if n % 10 else "")
    return str(n)


def counted(n: int, unit: str) -> str:
    """counted(30, "day") -> "thirty (30) days", the way contracts write numbers."""
    return f"{words(n)} ({n}) {unit}{'' if n == 1 else 's'}"


def long_date(iso_date: str) -> str:
    d = date.fromisoformat(iso_date)
    return f"{d:%B} {d.day}, {d.year}"


def usd(amount: int) -> str:
    return f"${amount:,.2f}"


def law_phrase(law: str) -> str:
    if law == "England and Wales":
        return law
    if law == "Cayman Islands":
        return "the Cayman Islands"
    return f"the State of {law}"


def entity_type(name: str) -> str:
    return "limited liability company" if name.endswith("LLC") else "corporation"


def address_lines(fake: Faker) -> list[str]:
    return [fake.street_address(), f"{fake.city()}, {fake.state_abbr()} {fake.zipcode()}"]


def signature(doc: Document, party: str, name: str, title: str) -> None:
    doc.add(party, style="line")
    doc.add("By: ______________________________", style="line")
    doc.add(f"Name: {name}", style="line")
    doc.add(f"Title: {title}", style="small")


def number_sections(doc: Document, sections: list[tuple[str, list]]) -> None:
    for i, (heading, parts) in enumerate(sections, start=1):
        doc.add(*parts, heading=f"{i}. {heading}.")


# --- NDA ----------------------------------------------------------------------------------------


def build_nda(f: dict[str, Any], rng: random.Random, fake: Faker, title: str | None) -> Document:
    # All random draws first (see module docstring).
    title_pick = rng.randrange(6)
    term_in_years = rng.random() < 0.5
    state = fake.state()
    project = rng.choice(PROJECT_NAMES)
    fund_signer, company_signer = fake.name(), fake.name()
    fund_signer_title = rng.choice(["Managing Director", "Partner", "Principal"])
    company_signer_title = rng.choice(["Chief Executive Officer", "President", "General Counsel"])

    mutual = f["mutual"]
    if title is None:
        titles = MUTUAL_NDA_TITLES if mutual else NDA_TITLES
        title = titles[title_pick % len(titles)]
    doc = Document(title)
    doc.add(title, style="title")

    party_def = '(each, a "Party")' if mutual else '("Recipient")'
    company_def = "" if mutual else ' (the "Company")'
    doc.add(
        (
            f'This {title} (this "Agreement") is entered into as of {long_date(f["effective_date"])}'
            f' (the "Effective Date") by and between {f["fund_party"]} and {f["counterparty"]},'
            f" a {state} {entity_type(f['counterparty'])}{company_def} {party_def}.",
            "counterparty",
            "fund_party",
            "effective_date",
        )
    )
    if mutual:
        recipient, Recipient, discloser, other = (
            "each Party",
            "Each Party",
            "the disclosing Party",
            "the other Party",
        )
        disclosure = "each Party may furnish the other Party with certain information concerning its business."
    else:
        recipient = Recipient = "Recipient"
        discloser = other = "the Company"
        disclosure = "the Company intends to furnish Recipient with certain information concerning its business."
    doc.add(
        f"In connection with a possible negotiated transaction referred to as Project {project}"
        ' (the "Transaction"),',
        (disclosure, "mutual"),
    )

    months = f["term_months"]
    term = (
        counted(months // 12, "year")
        if term_in_years and months % 12 == 0
        else counted(months, "month")
    )
    sections: list[tuple[str, list]] = [
        (
            "Confidential Information",
            [
                '"Confidential Information" means all non-public information, in any form, furnished'
                f" by or on behalf of {discloser} in connection with the Transaction, together with all"
                f" notes, analyses and other materials prepared by {recipient} that contain or reflect"
                " such information. Confidential Information does not include information that is or"
                " becomes generally available to the public other than through a breach of this"
                " Agreement."
            ],
        ),
        (
            "Use and Non-Disclosure",
            [
                f"{Recipient} shall use the Confidential Information solely for the purpose of"
                " evaluating the Transaction and shall not disclose it to any person other than its"
                " affiliates, partners, employees, financing sources and advisors who need to know it"
                ' for that purpose (its "Representatives").'
            ],
        ),
        (
            "Term",
            [
                (
                    f"The obligations under this Agreement shall remain in effect for {term} from the"
                    " Effective Date.",
                    "term_months",
                )
            ],
        ),
    ]
    if f["non_solicit_months"] is not None:
        sections.append(
            (
                "Non-Solicitation",
                [
                    (
                        f"For a period of {counted(f['non_solicit_months'], 'month')} from the"
                        f" Effective Date, {recipient} shall not, directly or indirectly, solicit for"
                        f" employment any officer or management-level employee of {other} with whom"
                        " it first had contact in connection with the Transaction.",
                        "non_solicit_months",
                    ),
                    "General advertisements not targeted at such persons shall not breach this"
                    " Section.",
                ],
            )
        )
    if f["non_compete_months"] is not None:
        sections.append(
            (
                "Non-Competition",
                [
                    (
                        f"For a period of {counted(f['non_compete_months'], 'month')} from the"
                        f" Effective Date, {recipient} shall not acquire, invest in or operate any"
                        f" business that competes with the business of {other}.",
                        "non_compete_months",
                    )
                ],
            )
        )
    if f["residuals_clause"] is not None:
        sections.append(
            (
                "Residuals",
                [
                    (
                        f"Notwithstanding anything to the contrary in this Agreement, {recipient} may"
                        " use for any purpose the Residuals resulting from access to the Confidential"
                        " Information.",
                        "residuals_clause",
                    ),
                    '"Residuals" means information retained in the unaided memory of persons who'
                    " had access to the Confidential Information.",
                ],
            )
        )
    if f["return_destroy_days"] is not None:
        sections.append(
            (
                "Return or Destruction",
                [
                    (
                        f"Within {counted(f['return_destroy_days'], 'day')} after a written request"
                        f" by {discloser}, {recipient} shall return or destroy all Confidential"
                        " Information in its possession.",
                        "return_destroy_days",
                    ),
                    "Copies may be retained to the extent required by law or bona fide document"
                    " retention policies.",
                ],
            )
        )
    sections += [
        (
            "No Obligation",
            [
                "Neither Party is under any obligation to proceed with the Transaction unless and"
                " until a definitive agreement has been executed."
                if mutual
                else "Neither Recipient nor the Company is under any obligation to proceed with the"
                " Transaction unless and until a definitive agreement has been executed."
            ],
        ),
        (
            "Remedies",
            [
                "Money damages may not be a sufficient remedy for a breach of this Agreement, and"
                " the non-breaching party shall be entitled to seek specific performance and"
                " injunctive relief."
            ],
        ),
        (
            "Governing Law",
            [
                (
                    "This Agreement shall be governed by and construed in accordance with the laws of"
                    f" {law_phrase(f['governing_law'])}, without regard to its conflict of laws"
                    " principles.",
                    "governing_law",
                )
            ],
        ),
        (
            "Miscellaneous",
            [
                "This Agreement constitutes the entire agreement between the parties regarding its"
                " subject matter and may be executed in counterparts, including by electronic"
                " signature."
            ],
        ),
    ]
    number_sections(doc, sections)

    doc.add(
        "IN WITNESS WHEREOF, the parties have executed this Agreement as of the Effective Date."
    )
    signature(doc, f["fund_party"], fund_signer, fund_signer_title)
    signature(doc, f["counterparty"], company_signer, company_signer_title)
    return doc


# --- Side letter --------------------------------------------------------------------------------


def build_side_letter(
    f: dict[str, Any], rng: random.Random, fake: Faker, title: str | None
) -> Document:
    address = address_lines(fake)
    gp_signer, investor_signer = fake.name(), fake.name()
    gp = f["fund"].replace(" Fund ", " GP ").replace(", L.P.", ", LLC")

    doc = Document(title or "Side Letter Agreement")
    doc.add(gp, style="title")
    doc.add(long_date(f["effective_date"]), style="line")
    doc.add(f["investor"], style="line")
    for line in address:
        doc.add(line, style="line")
    doc.add(f"Re: {doc.title}", heading="")
    doc.add("Ladies and Gentlemen:")
    doc.add(
        (
            f'This letter agreement (this "Side Letter") is entered into as of'
            f" {long_date(f['effective_date'])} in connection with the subscription by"
            f' {f["investor"]} (the "Investor") for a limited partner interest in {f["fund"]}'
            ' (the "Partnership").',
            "investor",
            "fund",
            "effective_date",
        ),
        "Capitalized terms not defined here have the meanings given in the Partnership's limited"
        ' partnership agreement (the "Partnership Agreement").',
    )
    doc.add(
        (
            f"The Investor has subscribed for a capital commitment of {usd(f['commitment_usd'])}"
            ' to the Partnership (the "Commitment").',
            "commitment_usd",
        )
    )

    sections: list[tuple[str, list]] = []
    if f["management_fee_pct"] is not None:
        sections.append(
            (
                "Management Fee",
                [
                    (
                        "Notwithstanding the Partnership Agreement, the annual management fee payable"
                        " with respect to the Investor shall be"
                        f" {f['management_fee_pct']:.2f}% of its Commitment.",
                        "management_fee_pct",
                    )
                ],
            )
        )
    if f["mfn"] is not None:
        sections.append(
            (
                "Most Favored Nation",
                [
                    (
                        "If the General Partner enters into a side letter with another limited partner"
                        " on terms more favorable than those in this Side Letter, the Investor shall be"
                        " offered the benefit of those terms.",
                        "mfn",
                    )
                ],
            )
        )
    if f["co_investment"] is not None:
        sections.append(
            (
                "Co-Investment",
                [
                    (
                        "The General Partner shall offer the Investor the opportunity to participate"
                        " in co-investment opportunities alongside the Partnership on a no-fee,"
                        " no-carry basis.",
                        "co_investment",
                    )
                ],
            )
        )
    if f["advisory_committee_seat"] is not None:
        sections.append(
            (
                "Advisory Committee",
                [
                    (
                        "The Investor shall be entitled to appoint one representative to the Limited"
                        " Partner Advisory Committee of the Partnership.",
                        "advisory_committee_seat",
                    )
                ],
            )
        )
    sections.append(
        (
            "Confidentiality",
            [
                "The Investor shall keep the terms of this Side Letter confidential, except as"
                " required by applicable law. This Side Letter shall be governed by the laws of the"
                " State of Delaware."
            ],
        )
    )
    number_sections(doc, sections)
    doc.add("Please confirm your agreement by countersigning below.")
    signature(doc, gp, gp_signer, "Managing Member")
    signature(doc, f["investor"], investor_signer, "Authorized Signatory")
    return doc


# --- Investor notices ---------------------------------------------------------------------------


def build_investor_notice(
    f: dict[str, Any], subtype: str, rng: random.Random, fake: Faker, title: str | None
) -> Document:
    # No trailing period: the name ends a sentence in the notice text.
    portfolio_company = (
        f"{fake.last_name()} {rng.choice(['Industries', 'Health', 'Software'])} Group"
    )
    ir_contact = fake.name()
    titles = {
        "capital_call": "Capital Call Notice",
        "distribution": "Distribution Notice",
        "annual_meeting": "Notice of Annual Meeting of Limited Partners",
    }
    doc = Document(title or titles[subtype])
    doc.add(f["fund"], style="title")
    doc.add(doc.title, style="heading")
    doc.add((f"Date: {long_date(f['notice_date'])}", "notice_date"), style="line")
    doc.add((f"To: {f['investor']}", "investor"), style="small")

    if subtype == "capital_call":
        doc.add(
            (
                f'Pursuant to the limited partnership agreement of {f["fund"]} (the "Partnership"),'
                " the General Partner hereby calls capital from the limited partners.",
                "fund",
            ),
            f"The proceeds will fund the Partnership's investment in {portfolio_company} and pay"
            " Partnership expenses.",
        )
        doc.add((f"Your share of this capital call is {usd(f['amount_usd'])}.", "amount_usd"))
        doc.add(
            (
                f"Please wire the amount due no later than {long_date(f['due_date'])}.",
                "due_date",
            ),
            "Wire instructions are unchanged from those on file. Please call Investor Relations to"
            " confirm them verbally before sending any wire.",
        )
    elif subtype == "distribution":
        doc.add(
            (
                f'The General Partner of {f["fund"]} (the "Partnership") is pleased to announce a'
                " distribution to the limited partners.",
                "fund",
            ),
            "The distribution represents proceeds from the sale of the Partnership's interest in"
            f" {portfolio_company}.",
        )
        doc.add((f"Your share of this distribution is {usd(f['amount_usd'])}.", "amount_usd"))
        doc.add(
            (
                f"The distribution will be paid by wire on {long_date(f['payment_date'])} to the"
                " account on file.",
                "payment_date",
            )
        )
    else:
        doc.add(
            (
                f'The annual meeting of the limited partners of {f["fund"]} (the "Partnership")'
                f" will be held on {long_date(f['meeting_date'])} in {f['meeting_location']}.",
                "fund",
                "meeting_date",
                "meeting_location",
            ),
            "The agenda includes a review of portfolio performance and an update on the"
            " Partnership's investment strategy.",
        )
        doc.add("Please confirm your attendance by replying to this notice.")

    doc.add("Sincerely,", style="line")
    doc.add(ir_contact, style="line")
    doc.add("Investor Relations, on behalf of the General Partner", style="small")
    return doc


# --- Legal notices / claims ---------------------------------------------------------------------


def build_legal_notice(
    f: dict[str, Any], subtype: str, rng: random.Random, fake: Faker, title: str | None
) -> Document:
    firm = f"{fake.last_name()} & {fake.last_name()} LLP"
    lawyer = fake.name()
    address = address_lines(fake)
    defendant = f"{fake.last_name()} {rng.choice(['Capital', 'Holdings', 'Partners'])} LLC"
    breach = rng.choice(
        [
            "make the payments required under Section 4 of the Agreement",
            "deliver the quarterly reports required under Section 7 of the Agreement",
            "maintain the insurance coverage required under Section 9 of the Agreement",
        ]
    )

    if subtype == "demand_letter":
        doc = Document(title or "Demand for Payment")
        doc.add(firm, style="title")
        doc.add((long_date(f["notice_date"]), "notice_date"), style="line")
        doc.add("VIA EMAIL AND CERTIFIED MAIL", style="small")
        doc.add((f["respondent"], "respondent"), style="line")
        for line in address:
            doc.add(line, style="line")
        doc.add(f"Re: {doc.title}", style="heading")
        doc.add(
            (
                f'This firm represents {f["claimant"]} ("our client") in connection with amounts'
                " owed to our client under a services agreement with your company.",
                "claimant",
            )
        )
        doc.add(
            (
                f"Our client demands payment of {usd(f['amount_claimed_usd'])}, which remains"
                " outstanding despite repeated requests.",
                "amount_claimed_usd",
            )
        )
        doc.add(
            (
                f"If payment is not received by {long_date(f['response_deadline'])}, our client"
                " intends to pursue all available remedies, including filing a lawsuit, without"
                " further notice.",
                "response_deadline",
            ),
            "Nothing in this letter waives any of our client's rights or remedies, all of which are"
            " expressly reserved.",
        )
        doc.add("Sincerely,", style="line")
        doc.add(lawyer, style="line")
        doc.add(firm, style="small")
    elif subtype == "notice_of_breach":
        doc = Document(title or "Notice of Breach")
        doc.add(f["claimant"], style="title")
        doc.add(doc.title, style="heading")
        doc.add((f"Date: {long_date(f['notice_date'])}", "notice_date"), style="line")
        doc.add((f"To: {f['respondent']}", "respondent"), style="small")
        doc.add(
            (
                f"Reference is made to the agreement dated {long_date(f['agreement_date'])} between"
                f' {f["claimant"]} and {f["respondent"]} (the "Agreement").',
                "claimant",
                "agreement_date",
            )
        )
        doc.add(
            f"{f['claimant']} hereby gives notice that you are in breach of the Agreement,"
            f" including by failing to {breach}."
        )
        doc.add(
            (
                f"If the breach is not cured within {counted(f['cure_period_days'], 'day')} after"
                f" the date of this notice, {f['claimant']} may terminate the Agreement and seek"
                " damages.",
                "cure_period_days",
            )
        )
        doc.add("Sincerely,", style="line")
        doc.add(lawyer, style="line")
        doc.add(f"General Counsel, {f['claimant']}", style="small")
    else:
        doc = Document(title or "Subpoena to Produce Documents")
        doc.add((f["court"], "court"), style="title")
        doc.add(
            (f"{f['claimant']}, Plaintiff,", "claimant"),
            f"v. {defendant}, Defendant.",
            style="line",
        )
        doc.add((f"Civil Action No. {f['case_number']}", "case_number"), style="small")
        doc.add(doc.title, style="heading")
        doc.add((f"To: {f['respondent']}", "respondent"), style="small")
        doc.add(
            "YOU ARE COMMANDED to produce the following documents: all communications and"
            f" agreements relating to {defendant} for the period from January 1, 2021 to the"
            " present."
        )
        doc.add(
            (
                f"Production is due on or before {long_date(f['response_deadline'])} at the"
                f" offices of {firm}.",
                "response_deadline",
            )
        )
        doc.add((f"Date: {long_date(f['notice_date'])}", "notice_date"), style="line")
        doc.add(f"{lawyer}, {firm}, Attorneys for Plaintiff", style="small")
    return doc


# --- Other (mailroom noise) ---------------------------------------------------------------------


def build_other(
    f: dict[str, Any], subtype: str, rng: random.Random, fake: Faker, title: str | None
) -> Document:
    address = address_lines(fake)
    people = [fake.name() for _ in range(4)]
    service = rng.choice(["IT support services", "Catering services", "Translation services"])
    topic = rng.choice(["Private Credit", "Healthcare Services", "Industrial Technology"])
    split = rng.randint(30, 70)

    if subtype == "invoice":
        doc = Document(title or "Invoice")
        doc.add((f["vendor"], "vendor"), style="title")
        for line in address:
            doc.add(line, style="line")
        doc.add(doc.title, style="heading")
        doc.add((f"Invoice Number: {f['invoice_number']}", "invoice_number"), style="line")
        doc.add((f"Invoice Date: {long_date(f['invoice_date'])}", "invoice_date"), style="small")
        first = f["amount_usd"] * split // 100
        doc.add(f"{service}: {usd(first)}", style="line")
        doc.add(f"Expenses and disbursements: {usd(f['amount_usd'] - first)}", style="small")
        doc.add((f"Total Due: {usd(f['amount_usd'])}", "amount_usd"), style="line")
        doc.add((f"Payment Due: {long_date(f['due_date'])}", "due_date"), style="small")
        doc.add(
            "Please reference the invoice number on your remittance. Thank you for your business."
        )
    elif subtype == "newsletter":
        doc = Document(title or f"{topic} Market Update")
        doc.add((f["publisher"], "publisher"), style="title")
        doc.add(doc.title, style="heading")
        doc.add((f"Issue date: {long_date(f['issue_date'])}", "issue_date"), style="small")
        doc.add(
            f"Deal activity in {topic.lower()} remained steady this quarter, with sponsors"
            " focusing on add-on acquisitions over new platform investments."
        )
        doc.add(
            "Lenders continued to compete for high-quality credits, and pricing tightened modestly"
            " across the middle market."
        )
        doc.add(f"Contributors: {people[0]}, {people[1]}.", style="small")
    elif subtype == "board_minutes":
        doc = Document(title or "Minutes of a Meeting of the Board of Directors")
        doc.add(doc.title, style="title")
        doc.add(
            (
                f"Minutes of a meeting of the Board of Directors of {f['company']} held on"
                f" {long_date(f['meeting_date'])}.",
                "company",
                "meeting_date",
            )
        )
        doc.add(f"Present: {', '.join(people[:3])}. Also present: {people[3]}, Secretary.")
        doc.add(
            "The Chair called the meeting to order. A quorum being present, the minutes of the"
            " previous meeting were approved."
        )
        doc.add(
            "The Board reviewed the quarterly financial statements and approved the annual budget."
            " There being no further business, the meeting was adjourned."
        )
    else:
        doc = Document(title or f"{topic} Leadership Summit")
        doc.add(doc.title, style="title")
        doc.add(
            (
                f"Join {f['vendor']} on {long_date(f['event_date'])} for an afternoon of panels"
                f" on {topic.lower()}.",
                "vendor",
                "event_date",
            )
        )
        doc.add(f"Speakers include {people[0]} and {people[1]}. Space is limited.")
        doc.add("Register today to reserve your seat.", style="small")
    return doc


def build(
    doc_type: str,
    subtype: str | None,
    fields: dict[str, Any],
    rng: random.Random,
    fake: Faker,
    title: str | None = None,
) -> Document:
    if doc_type == "nda":
        return build_nda(fields, rng, fake, title)
    if doc_type == "side_letter":
        return build_side_letter(fields, rng, fake, title)
    builders = {
        "investor_notice": build_investor_notice,
        "legal_notice": build_legal_notice,
        "other": build_other,
    }
    return builders[doc_type](fields, subtype, rng, fake, title)
