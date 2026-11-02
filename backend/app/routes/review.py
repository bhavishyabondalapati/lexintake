"""Review endpoints: the queue, one document with its evidence, approve, override a field.

There is no login yet, so every action is recorded with the actor "reviewer".
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AuditLog, Document, Extraction, Status, TextBlock
from app.playbook import check_playbook, load_playbook
from app.routes.documents import DocumentOut, get_blob_store
from app.status import InvalidTransition, audit, transition
from app.storage.blobs import BlobStore

router = APIRouter(prefix="/review", tags=["review"])
ACTOR = "reviewer"
APPROVABLE = {Status.NEEDS_REVIEW, Status.EXTRACTED}


class Flag(BaseModel):
    kind: str  # injection | deviation | unknown | unverified | other
    label: str


class QueueItem(DocumentOut):
    label: str  # the counterparty (or investor, claimant...) when known, else the filename
    flags: list[Flag]


class Box(BaseModel):
    page: int
    x0: float
    y0: float
    x1: float
    y1: float


class FieldOut(BaseModel):
    name: str
    value: Any
    page: int | None
    quote: str | None
    confidence: float | None
    status: str
    source: str | None  # text_layer | ocr
    boxes: list[Box]  # where the quote sits on the page (points, top-left origin)
    original_value: Any = None
    overridden: bool = False


class ReviewDetail(DocumentOut):
    label: str
    fields: list[FieldOut]
    playbook_results: list[dict] | None
    review_reasons: list[dict]
    audit: list[dict]


class OverrideIn(BaseModel):
    value: Any = None
    note: str = ""


# The first of these fields that has a value names the document.
LABEL_FIELDS = ("counterparty", "investor", "claimant", "vendor", "publisher")
# Worst first. Documents that are not waiting for review sort after all of them.
SEVERITY = {"injection": 0, "deviation": 1, "unknown": 2, "unverified": 2, "other": 3}


def display_label(fields: dict[str, Any], filename: str) -> str:
    for name in LABEL_FIELDS:
        value = (fields.get(name) or {}).get("value")
        if value:
            return str(value)
    return filename


def _words(code: str) -> str:
    return code.replace("_", " ")


def flags_for(extraction: Extraction | None) -> list[Flag]:
    """What is wrong with a document, in words: the rule that failed, the field that could not be
    verified, a suspected injection."""
    if extraction is None:
        return []
    rules = {r.id: r.label for r in load_playbook().rules}
    reasons = extraction.review_reasons or []
    flagged_fields = {r["field"] for r in reasons if r["code"] != "playbook_unknown"}
    flags = []
    for r in reasons:
        if r["code"] == "suspected_prompt_injection":
            flags.append(Flag(kind="injection", label="Suspected prompt injection"))
    for r in extraction.playbook_results or []:
        if r["outcome"] == "deviation":
            flags.append(Flag(kind="deviation", label=rules.get(r["rule_id"], r["rule_id"])))
        elif r["outcome"] == "unknown" and r["field"] not in flagged_fields:
            flags.append(
                Flag(kind="unknown", label=f"Unknown: {rules.get(r['rule_id'], r['rule_id'])}")
            )
    for r in reasons:
        code, field = r["code"], r["field"]
        if code in ("suspected_prompt_injection", "playbook_deviation", "playbook_unknown"):
            continue
        if code in ("field_unverified", "value_not_in_quote", "ocr_match_below_trust"):
            flags.append(Flag(kind="unverified", label=f"Unverified: {_words(field or '')}"))
        elif code in ("low_field_confidence", "field_missing", "absent_but_mentioned"):
            flags.append(
                Flag(kind="other", label=f"{_words(code).capitalize()}: {_words(field or '')}")
            )
        else:
            flags.append(Flag(kind="other", label=_words(code).capitalize()))
    seen, unique = set(), []
    for f in flags:  # a field can fail several checks; say it once
        if f.label not in seen:
            seen.add(f.label)
            unique.append(f)
    return unique


def _document(session: Session, document_id: uuid.UUID) -> Document:
    doc = session.get(Document, document_id, with_for_update=True)
    if doc is None:
        raise HTTPException(404, "document not found")
    return doc


@router.get("/documents", response_model=list[QueueItem])
def queue(
    session: Annotated[Session, Depends(get_session)], status: str | None = None, limit: int = 200
) -> list[QueueItem]:
    query = (
        select(Document, Extraction)
        .outerjoin(Extraction, Extraction.document_id == Document.id)
        .order_by(Document.created_at.desc())
        .limit(min(limit, 500))
    )
    if status:
        query = query.where(Document.status == status)
    items = []
    for doc, extraction in session.execute(query):
        flags = flags_for(extraction)
        items.append(
            QueueItem(
                **DocumentOut.model_validate(doc).model_dump(),
                label=display_label(extraction.fields if extraction else {}, doc.filename),
                flags=flags,
            )
        )

    # Needs-review documents first by severity (injection, deviations, unknown/unverified, the
    # rest), then newest first. The query is already newest first and the sort is stable.
    def severity(item: QueueItem) -> int:
        if item.status != Status.NEEDS_REVIEW:
            return len(SEVERITY)
        return min((SEVERITY[f.kind] for f in item.flags), default=len(SEVERITY))

    items.sort(key=severity)
    return items


@router.get("/counts", response_model=dict[str, int])
def counts(session: Annotated[Session, Depends(get_session)]) -> dict[str, int]:
    rows = session.execute(select(Document.status, func.count()).group_by(Document.status))
    return {status: n for status, n in rows}


@router.get("/documents/{document_id}", response_model=ReviewDetail)
def detail(document_id: uuid.UUID, session: Annotated[Session, Depends(get_session)]):
    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    extraction = session.scalars(select(Extraction).where(Extraction.document_id == doc.id)).first()
    stored = extraction.fields if extraction else {}
    block_ids = {b for f in stored.values() for b in f.get("block_ids", [])}
    blocks = {
        b.id: b for b in session.scalars(select(TextBlock).where(TextBlock.id.in_(block_ids)))
    }
    fields = [
        FieldOut(
            name=name,
            value=f.get("value"),
            page=f.get("page"),
            quote=f.get("quote"),
            confidence=f.get("confidence"),
            status=f.get("status", ""),
            source=f.get("source"),
            boxes=[
                Box(page=b.page, x0=b.x0, y0=b.y0, x1=b.x1, y1=b.y1)
                for b in (blocks.get(i) for i in f.get("block_ids", []))
                if b is not None
            ],
            original_value=f.get("original_value"),
            overridden=f.get("status") == "overridden",
        )
        for name, f in stored.items()
    ]
    log = session.scalars(
        select(AuditLog).where(AuditLog.document_id == doc.id).order_by(AuditLog.id)
    ).all()
    return ReviewDetail(
        **DocumentOut.model_validate(doc).model_dump(),
        label=display_label(stored, doc.filename),
        fields=fields,
        playbook_results=extraction.playbook_results if extraction else None,
        review_reasons=extraction.review_reasons if extraction else [],
        audit=[
            {
                "id": a.id,
                "from_status": a.from_status,
                "to_status": a.to_status,
                "actor": a.actor,
                "detail": a.detail,
                "created_at": a.created_at.isoformat(),
            }
            for a in log
        ],
    )


@router.get("/documents/{document_id}/file")
def file(
    document_id: uuid.UUID,
    session: Annotated[Session, Depends(get_session)],
    blobs: Annotated[BlobStore, Depends(get_blob_store)],
) -> FileResponse:
    doc = session.get(Document, document_id)
    if doc is None or not blobs.exists(doc.sha256):
        raise HTTPException(404, "file not found")
    return FileResponse(blobs.path(doc.sha256), media_type="application/pdf", filename=doc.filename)


@router.post("/documents/{document_id}/approve", response_model=DocumentOut)
def approve(document_id: uuid.UUID, session: Annotated[Session, Depends(get_session)]):
    doc = _document(session, document_id)
    if Status(doc.status) not in APPROVABLE:
        raise HTTPException(409, f"a {doc.status} document can't be approved")
    try:
        transition(session, doc, Status.APPROVED, ACTOR, {"event": "approve"})
    except InvalidTransition as e:  # pragma: no cover - guarded above
        raise HTTPException(409, str(e)) from e
    # Commit before answering: the session dependency would only commit after the response is
    # sent, and the page asks for the document again the moment it gets the answer.
    session.commit()
    return doc


@router.post("/documents/{document_id}/fields/{name}/override", response_model=ReviewDetail)
def override(
    document_id: uuid.UUID,
    name: str,
    body: OverrideIn,
    session: Annotated[Session, Depends(get_session)],
):
    """Replace a field's value. The old value stays in the entry and the audit log, the playbook
    is checked again with the new value (a person has verified it), and the status is unchanged."""
    doc = _document(session, document_id)
    extraction = session.scalars(
        select(Extraction).where(Extraction.document_id == doc.id).with_for_update()
    ).first()
    if extraction is None or name not in extraction.fields:
        raise HTTPException(404, "field not found")
    if Status(doc.status) in {Status.APPROVED, Status.AUTO_APPROVED}:
        raise HTTPException(409, "an approved document can't be edited")

    old = extraction.fields[name]
    new = dict(old) | {
        "value": body.value,
        "status": "overridden",
        "original_value": old.get("original_value", old.get("value")),
        "flags": [],
    }
    extraction.fields = extraction.fields | {name: new}  # a new dict, so JSONB change is seen
    if extraction.doc_type == "nda":
        results = check_playbook(load_playbook(), extraction.fields)
        extraction.playbook_results = [r.to_json() for r in results]
    audit(
        session,
        doc,
        ACTOR,
        {
            "event": "override",
            "field": name,
            "old": old.get("value"),
            "new": body.value,
            "note": body.note,
        },
        from_status=doc.status,
        to_status=doc.status,
    )
    session.commit()
    return detail(document_id, session)
