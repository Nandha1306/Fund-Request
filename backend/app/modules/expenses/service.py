"""Expense use cases for stages 4–6 (phase 4 §2): documents, save / submit expenses, item review by the
approver, item verification by Finance.  Liquidation lives in ``finance/service.py``.

Same shape as ``fund_requests/service.py``: find the request (404 if invisible), lock the project then
the request, check version (409) / object rule (403) / transition (409) / item rules (422), change money
only through ``budget`` and the status only through ``workflow.apply``, exactly one ``db.commit()``.

Uploaded files are written **before** the database transaction and deleted again if it fails; a deleted
document's file is removed only after the commit.
"""

import re
import unicodedata
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import BinaryIO

import filetype
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import storage
from app.core.config import get_settings
from app.core.enums import ApprovalStatus, RequestStatus, VerificationStatus
from app.core.errors import BusinessRuleViolation, Conflict, InvalidFile, NotFound
from app.core.money import ZERO, q2
from app.modules.accounts.models import User
from app.modules.audit import service as audit
from app.modules.expenses.models import Document, ExpenseItem
from app.modules.expenses.schemas import ExpenseItemIn, ExpensesSave, ItemReviewBody, ItemVerifyBody
from app.modules.finance import budget
from app.modules.fund_requests import policies, workflow
from app.modules.fund_requests.models import FundRequest
from app.modules.fund_requests.service import (
    _finance_recipients,
    disbursed_total,
    inr,
    lock_visible,
)
from app.modules.fund_requests.workflow import Action

ALLOWED_TYPES = {"application/pdf": "PDF", "image/jpeg": "JPG", "image/png": "PNG"}
SNIFF_BYTES = 8192

EMPTY_FILE = "The file is empty"
WRONG_TYPE = "Only PDF, JPG and PNG files are accepted"
DOCS_NOT_EDITABLE = "Documents can only be changed while the expenses are being prepared"
DOCUMENT_NOT_FOUND = "Document not found"
ITEM_NOT_FOUND = "Expense item not found"
ITEM_LOCKED = "Expense item {id} is approved and locked; it cannot be changed"
DOCUMENT_LOCKED = "This document belongs to an approved expense item and cannot be removed"
MISSING_PROOF = "Every expense item needs at least one proof document"
FUTURE_EXPENSE = "Expense dates cannot be in the future"
JUSTIFICATION_REQUIRED = "A justification is required when the expenses exceed the requested amount"
ALREADY_APPROVED = "This expense item is already approved"
ALREADY_VERIFIED = "This expense item is already verified"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(UTC)


def _items(db: Session, request_id: int) -> list[ExpenseItem]:
    """The request's items, ordered by id, refreshed from the database (call under the request lock)."""
    return list(
        db.scalars(
            select(ExpenseItem)
            .where(ExpenseItem.request_id == request_id)
            .order_by(ExpenseItem.id)
            .execution_options(populate_existing=True)
        )
    )


def _documents(db: Session, request_id: int) -> list[Document]:
    return list(
        db.scalars(
            select(Document)
            .where(Document.request_id == request_id)
            .order_by(Document.id)
            .execution_options(populate_existing=True)
        )
    )


def _item_or_404(items: list[ExpenseItem], item_id: int) -> ExpenseItem:
    item = next((i for i in items if i.id == item_id), None)
    if item is None:
        raise NotFound(ITEM_NOT_FOUND)
    return item


def _total(items: list[ExpenseItem]) -> Decimal:
    return q2(sum((q2(i.amount) for i in items), ZERO))


def _items_snapshot(items: list[ExpenseItem]) -> list[dict]:
    return [
        {
            "id": i.id,
            "description": i.description,
            "amount": str(q2(i.amount)),
            "expense_date": i.expense_date.isoformat(),
            "category": i.category,
            "approval_status": i.approval_status,
            "verification_status": i.verification_status,
        }
        for i in items
    ]


def _item_snapshot(item: ExpenseItem) -> dict:
    return {"item_id": item.id, "description": item.description, "amount": str(q2(item.amount))}


def _guard(action: Action, items: list[ExpenseItem]) -> None:
    message = workflow.item_guard(action, items)
    if message is not None:
        raise BusinessRuleViolation(message)


def _ensure_documents_editable(actor: User, request: FundRequest) -> None:
    """403 unless the requestor; 409 unless the request is in Stage 4 (the ``save_expenses`` statuses)."""
    workflow.ensure_party(actor, request, Action.SAVE_EXPENSES)
    if (RequestStatus(request.status), Action.SAVE_EXPENSES) not in workflow.TRANSITIONS:
        raise Conflict(DOCS_NOT_EDITABLE, details={"status": request.status})


def _editable_item_for_upload(db: Session, request_id: int, item_id: int) -> ExpenseItem:
    item = db.get(ExpenseItem, item_id, populate_existing=True)
    if item is None or item.request_id != request_id:
        raise BusinessRuleViolation(ITEM_NOT_FOUND, details={"expense_item_id": item_id})
    if not workflow.is_item_editable(item):
        raise BusinessRuleViolation(ITEM_LOCKED.format(id=item_id), details={"expense_item_id": item_id})
    return item


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def clean_filename(name: str | None) -> str:
    """The base name only, without control characters, at most 255 characters (it is data, never a path)."""
    base = re.split(r"[\\/]", name or "")[-1]
    base = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C").strip().strip(".")
    if not base:
        return "document"
    if len(base) > 255:
        stem, dot, ext = base.rpartition(".")
        base = f"{stem[: 254 - len(ext)]}.{ext}" if dot and len(ext) <= 10 else base[:255]
    return base


def _sniff(stream: BinaryIO) -> str:
    """The MIME type from the file **content**; 400 ``invalid_file`` unless PDF, JPEG or PNG."""
    head = stream.read(SNIFF_BYTES)
    if not head:
        raise InvalidFile(EMPTY_FILE)
    kind = filetype.guess(head)
    if kind is None or kind.mime not in ALLOWED_TYPES:
        raise InvalidFile(WRONG_TYPE, details={"detected_type": kind.mime if kind else None})
    stream.seek(0)
    return kind.mime


def upload_document(
    db: Session,
    actor: User,
    request_id: int,
    stream: BinaryIO,
    filename: str | None,
    expense_item_id: int | None,
) -> Document:
    # Cheap checks first, so a caller who may not upload never writes to disk.
    request = policies.get_visible_request_or_404(db, actor, request_id)
    _ensure_documents_editable(actor, request)
    if expense_item_id is not None:
        _editable_item_for_upload(db, request.id, expense_item_id)
    mime = _sniff(stream)

    settings = get_settings()
    store = storage.get_storage()
    try:
        key, size, sha256 = store.save(stream, settings.max_upload_mb * 1024 * 1024)
    except storage.FileTooLarge:
        raise InvalidFile(
            f"The file is larger than the {settings.max_upload_mb} MB limit",
            details={"max_upload_mb": settings.max_upload_mb},
        ) from None
    if size == 0:  # defensive: the sniff already read some bytes
        store.delete(key)
        raise InvalidFile(EMPTY_FILE)

    try:
        with db.begin_nested():
            request = policies.lock_request(db, request_id)
            _ensure_documents_editable(actor, request)
            if expense_item_id is not None:
                _editable_item_for_upload(db, request.id, expense_item_id)
            document = Document(
                request_id=request.id,
                expense_item_id=expense_item_id,
                storage_key=key,
                original_name=clean_filename(filename),
                mime_type=mime,
                size_bytes=size,
                sha256=sha256,
                uploaded_by=actor.id,
            )
            db.add(document)
            db.flush()
            audit.record(
                db,
                entity=workflow.ENTITY,
                entity_id=request.id,
                action="document_uploaded",
                actor_id=actor.id,
                snapshot={
                    "document_id": document.id,
                    "original_name": document.original_name,
                    "expense_item_id": expense_item_id,
                    "mime_type": mime,
                    "size_bytes": size,
                },
            )
        db.commit()
    except BaseException:
        store.delete(key)
        raise
    return document


def visible_document(db: Session, actor: User, document_id: int) -> Document:
    """The document, if its parent request is visible to *actor*; 404 otherwise."""
    document = db.get(Document, document_id)
    if document is None:
        raise NotFound(DOCUMENT_NOT_FOUND)
    try:
        policies.get_visible_request_or_404(db, actor, document.request_id)
    except NotFound:
        raise NotFound(DOCUMENT_NOT_FOUND) from None
    return document


def open_document(db: Session, actor: User, document_id: int) -> tuple[Document, Iterator[bytes]]:
    """The document row and its content in chunks (the file is closed when the iterator ends)."""
    document = visible_document(db, actor, document_id)
    try:
        handle = storage.get_storage().open(document.storage_key)
    except FileNotFoundError:
        raise NotFound("The document file is missing") from None

    def chunks() -> Iterator[bytes]:
        with handle:
            while chunk := handle.read(storage.CHUNK_SIZE):
                yield chunk

    return document, chunks()


def delete_document(db: Session, actor: User, document_id: int) -> None:
    document = visible_document(db, actor, document_id)
    with db.begin_nested():
        request = policies.lock_request(db, document.request_id)
        document = db.get(Document, document_id, populate_existing=True)
        if document is None:  # deleted by a concurrent call
            raise NotFound(DOCUMENT_NOT_FOUND)
        _ensure_documents_editable(actor, request)
        if document.expense_item_id is not None:
            item = db.get(ExpenseItem, document.expense_item_id, populate_existing=True)
            if item is not None and not workflow.is_item_editable(item):
                raise BusinessRuleViolation(DOCUMENT_LOCKED, details={"expense_item_id": item.id})
        key = document.storage_key
        snapshot = {
            "document_id": document.id,
            "original_name": document.original_name,
            "expense_item_id": document.expense_item_id,
        }
        db.delete(document)
        db.flush()
        audit.record(
            db,
            entity=workflow.ENTITY,
            entity_id=request.id,
            action="document_deleted",
            actor_id=actor.id,
            snapshot=snapshot,
        )
    db.commit()
    try:
        storage.get_storage().delete(key)
    except OSError:  # the row is gone; an orphan file is harmless
        pass


# ---------------------------------------------------------------------------
# Stage 4: save and submit expenses
# ---------------------------------------------------------------------------

def _unchanged(item: ExpenseItem, row: ExpenseItemIn, doc_ids: set[int]) -> bool:
    return (
        item.description == row.description
        and q2(item.amount) == q2(row.amount)
        and item.expense_date == row.expense_date
        and item.category == row.category
        and item.comment == row.comment
        and doc_ids == set(row.document_ids)
    )


def _validate_save(
    data: ExpensesSave, items: dict[int, ExpenseItem], docs: dict[int, Document], locked: set[int]
) -> None:
    seen_items: set[int] = set()
    doc_owner: dict[int, int | None] = {}  # document id → item id in the payload (None = new item)
    for row in data.items:
        if row.id is not None:
            if row.id not in items:
                raise BusinessRuleViolation(
                    f"Expense item {row.id} is not on this request", details={"item_id": row.id}
                )
            if row.id in seen_items:
                raise BusinessRuleViolation(
                    f"Expense item {row.id} is listed twice", details={"item_id": row.id}
                )
            seen_items.add(row.id)
        for doc_id in row.document_ids:
            if doc_id not in docs:
                raise BusinessRuleViolation(
                    f"Document {doc_id} does not belong to this request", details={"document_id": doc_id}
                )
            if doc_id in doc_owner:
                raise BusinessRuleViolation(
                    f"Document {doc_id} is attached to more than one item", details={"document_id": doc_id}
                )
            doc_owner[doc_id] = row.id

    for row in data.items:
        if row.id in locked:
            current = {d.id for d in docs.values() if d.expense_item_id == row.id}
            if not _unchanged(items[row.id], row, current):
                raise BusinessRuleViolation(ITEM_LOCKED.format(id=row.id), details={"item_id": row.id})
    for doc_id, owner in doc_owner.items():
        current = docs[doc_id].expense_item_id
        if current in locked and owner != current:
            raise BusinessRuleViolation(
                f"Document {doc_id} belongs to an approved expense item", details={"document_id": doc_id}
            )


def save_expenses(db: Session, actor: User, request_id: int, data: ExpensesSave) -> FundRequest:
    """Upsert the editable items: update by id, create without id, delete editable items left out.
    Locked (approved) items are left untouched; their documents stay attached."""
    with db.begin_nested():
        _, request = lock_visible(db, actor, request_id)
        workflow.ensure_version(request, data.version)
        workflow.ensure_party(actor, request, Action.SAVE_EXPENSES)
        workflow.check(request, Action.SAVE_EXPENSES, data.version)

        items = {i.id: i for i in _items(db, request.id)}
        docs = {d.id: d for d in _documents(db, request.id)}
        locked = {i.id for i in items.values() if not workflow.is_item_editable(i)}
        _validate_save(data, items, docs, locked)

        # Detach every document of an editable item; the payload re-attaches the ones it lists.
        for d in docs.values():
            if d.expense_item_id is not None and d.expense_item_id not in locked:
                d.expense_item_id = None
        db.flush()
        listed = {row.id for row in data.items if row.id is not None}
        for item in items.values():
            if item.id not in locked and item.id not in listed:
                db.delete(item)
        db.flush()

        targets: list[tuple[ExpenseItemIn, ExpenseItem]] = []
        for row in data.items:
            if row.id in locked:
                continue
            item = items[row.id] if row.id is not None else ExpenseItem(request_id=request.id)
            item.description = row.description
            item.amount = q2(row.amount)
            item.expense_date = row.expense_date
            item.category = row.category
            item.comment = row.comment
            if row.id is None:
                db.add(item)
            targets.append((row, item))
        db.flush()
        for row, item in targets:
            for doc_id in row.document_ids:
                docs[doc_id].expense_item_id = item.id
        if "overspend_justification" in data.model_fields_set:
            request.overspend_justification = data.overspend_justification
        db.flush()

        remaining = _items(db, request.id)
        workflow.apply(
            db,
            request,
            Action.SAVE_EXPENSES,
            actor_id=actor.id,
            expected_version=data.version,
            snapshot={"items": _items_snapshot(remaining), "total": str(_total(remaining))},
        )
    db.commit()
    return request


def submit_expenses(db: Session, actor: User, request_id: int, version: int) -> FundRequest:
    with db.begin_nested():
        project, request = lock_visible(db, actor, request_id)
        workflow.ensure_version(request, version)
        workflow.ensure_party(actor, request, Action.SUBMIT_EXPENSES)
        workflow.check(request, Action.SUBMIT_EXPENSES, version)

        items = _items(db, request.id)
        _guard(Action.SUBMIT_EXPENSES, items)
        with_proof = {d.expense_item_id for d in _documents(db, request.id)}
        missing = [i.id for i in items if i.id not in with_proof]
        if missing:
            raise BusinessRuleViolation(MISSING_PROOF, details={"item_ids": missing})
        future = [i.id for i in items if i.expense_date > date.today()]
        if future:
            raise BusinessRuleViolation(FUTURE_EXPENSE, details={"item_ids": future})
        total = _total(items)
        requested = q2(request.amount_requested)
        if total > requested and not request.overspend_justification:
            raise BusinessRuleViolation(
                JUSTIFICATION_REQUIRED,
                details={"expense_total": str(total), "amount_requested": str(requested)},
            )

        # D4: the part above what was paid out is reserved now (422 if the project can't cover it).
        paid = disbursed_total(db, request.id)
        budget.adjust_overage(db, project, request, budget.overage_for(total, paid), actor_id=actor.id)

        # D9: rejected or Finance-returned items go through review again; approved items stay locked.
        for item in items:
            if (
                item.approval_status == ApprovalStatus.REJECTED
                or item.verification_status == VerificationStatus.RETURNED
            ):
                item.approval_status = ApprovalStatus.PENDING.value
                item.approval_reason = None
                item.approved_by = None
                item.approved_at = None
                item.verification_status = VerificationStatus.PENDING.value
                item.verification_reason = None
                item.verified_by = None
                item.verified_at = None
        db.flush()

        workflow.apply(
            db,
            request,
            Action.SUBMIT_EXPENSES,
            actor_id=actor.id,
            expected_version=version,
            snapshot={
                "items": _items_snapshot(items),
                "total": str(total),
                "disbursed_total": str(paid),
                "overage_reserved": str(q2(request.overage_reserved)),
            },
        )
        audit.notify(
            db,
            user_ids=[request.approver_id],
            type="expenses_submitted",
            message=f"{request.ref_no}: {actor.full_name} submitted expenses of {inr(total)} for your review",
            request_id=request.id,
        )
    db.commit()
    return request


# ---------------------------------------------------------------------------
# Stage 5: item review by the assigned approver
# ---------------------------------------------------------------------------

def review_item(db: Session, actor: User, request_id: int, item_id: int, data: ItemReviewBody) -> FundRequest:
    with db.begin_nested():
        _, request = lock_visible(db, actor, request_id)
        workflow.ensure_version(request, data.version)
        workflow.ensure_party(actor, request, Action.REVIEW_ITEM)
        workflow.check(request, Action.REVIEW_ITEM, data.version)
        item = _item_or_404(_items(db, request.id), item_id)
        if item.approval_status == ApprovalStatus.APPROVED:
            raise Conflict(ALREADY_APPROVED, details={"item_id": item.id})

        approved = data.decision == ApprovalStatus.APPROVED
        item.approval_status = data.decision
        item.approval_reason = None if approved else data.reason
        item.approved_by = actor.id
        item.approved_at = _now()
        db.flush()
        workflow.apply(
            db,
            request,
            Action.REVIEW_ITEM,
            actor_id=actor.id,
            expected_version=data.version,
            reason=item.approval_reason,
            snapshot=_item_snapshot(item),
            audit_action="item_approved" if approved else "item_rejected",
        )
    db.commit()
    return request


def send_back_expenses(db: Session, actor: User, request_id: int, version: int) -> FundRequest:
    with db.begin_nested():
        _, request = lock_visible(db, actor, request_id)
        workflow.ensure_version(request, version)
        workflow.ensure_party(actor, request, Action.SEND_BACK_EXPENSES)
        workflow.check(request, Action.SEND_BACK_EXPENSES, version)
        items = _items(db, request.id)
        _guard(Action.SEND_BACK_EXPENSES, items)

        rejected = [i for i in items if i.approval_status == ApprovalStatus.REJECTED]
        workflow.apply(
            db,
            request,
            Action.SEND_BACK_EXPENSES,
            actor_id=actor.id,
            expected_version=version,
            snapshot={"rejected_item_ids": [i.id for i in rejected], "rejected_total": str(_total(rejected))},
        )
        audit.notify(
            db,
            user_ids=[request.requestor_id],
            type="expenses_sent_back",
            message=f"{request.ref_no}: {actor.full_name} sent your expenses back; "
            f"{len(rejected)} item(s) of {inr(_total(rejected))} were rejected",
            request_id=request.id,
        )
    db.commit()
    return request


def approve_expenses(db: Session, actor: User, request_id: int, version: int) -> FundRequest:
    with db.begin_nested():
        _, request = lock_visible(db, actor, request_id)
        workflow.ensure_version(request, version)
        workflow.ensure_party(actor, request, Action.APPROVE_EXPENSES)
        workflow.check(request, Action.APPROVE_EXPENSES, version)
        items = _items(db, request.id)
        _guard(Action.APPROVE_EXPENSES, items)

        total = _total(items)
        workflow.apply(
            db,
            request,
            Action.APPROVE_EXPENSES,
            actor_id=actor.id,
            expected_version=version,
            snapshot={"approved_total": str(total)},
        )
        audit.notify(
            db,
            user_ids=_finance_recipients(db),
            type="expenses_approved",
            message=f"{request.ref_no}: expenses of {inr(total)} were approved by {actor.full_name} "
            "and await verification",
            request_id=request.id,
        )
    db.commit()
    return request


# ---------------------------------------------------------------------------
# Stage 6: item verification by Finance
# ---------------------------------------------------------------------------

def verify_item(db: Session, actor: User, request_id: int, item_id: int, data: ItemVerifyBody) -> FundRequest:
    with db.begin_nested():
        _, request = lock_visible(db, actor, request_id)
        workflow.ensure_version(request, data.version)
        workflow.check(request, Action.VERIFY_ITEM, data.version)
        item = _item_or_404(_items(db, request.id), item_id)
        if item.verification_status == VerificationStatus.VERIFIED:
            raise Conflict(ALREADY_VERIFIED, details={"item_id": item.id})

        verified = data.decision == VerificationStatus.VERIFIED
        item.verification_status = data.decision
        item.verification_reason = None if verified else data.reason
        item.verified_by = actor.id
        item.verified_at = _now()
        db.flush()
        workflow.apply(
            db,
            request,
            Action.VERIFY_ITEM,
            actor_id=actor.id,
            expected_version=data.version,
            reason=item.verification_reason,
            snapshot=_item_snapshot(item),
            audit_action="item_verified" if verified else "item_returned",
        )
    db.commit()
    return request


def return_expenses(db: Session, actor: User, request_id: int, version: int) -> FundRequest:
    """Finance send-back.  Returned items go back to approval PENDING, so they pass Stage 5 again (D9)."""
    with db.begin_nested():
        _, request = lock_visible(db, actor, request_id)
        workflow.ensure_version(request, version)
        workflow.check(request, Action.RETURN_EXPENSES, version)
        items = _items(db, request.id)
        _guard(Action.RETURN_EXPENSES, items)

        returned = [i for i in items if i.verification_status == VerificationStatus.RETURNED]
        for item in returned:
            item.approval_status = ApprovalStatus.PENDING.value
            item.approval_reason = None
            item.approved_by = None
            item.approved_at = None
        db.flush()
        workflow.apply(
            db,
            request,
            Action.RETURN_EXPENSES,
            actor_id=actor.id,
            expected_version=version,
            snapshot={"returned_item_ids": [i.id for i in returned], "returned_total": str(_total(returned))},
        )
        audit.notify(
            db,
            user_ids=[request.requestor_id],
            type="expenses_returned",
            message=f"{request.ref_no}: {actor.full_name} returned {len(returned)} expense item(s) of "
            f"{inr(_total(returned))} for correction",
            request_id=request.id,
        )
    db.commit()
    return request
