"""Expense endpoints (phase 4 §3.2): proof documents, save / submit expenses, item review and verification.
Liquidation is ``POST /requests/{id}/liquidate`` in ``fund_requests/router.py``."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Response, UploadFile
from fastapi.responses import StreamingResponse

from app.core.db import DbSession
from app.modules.accounts.deps import CurrentUser, require
from app.modules.accounts.models import User
from app.modules.expenses import service
from app.modules.expenses.schemas import DocumentOut, ExpensesSave, ItemReviewBody, ItemVerifyBody
from app.modules.fund_requests import service as requests
from app.modules.fund_requests.schemas import RequestDetail, VersionBody

router = APIRouter(tags=["expenses"])


def content_disposition(name: str) -> str:
    """``attachment; filename="…"`` with an ASCII fallback, plus ``filename*`` for non-ASCII names."""
    fallback = "".join(ch if 32 <= ord(ch) < 127 and ch not in '"\\' else "_" for ch in name) or "document"
    value = f'attachment; filename="{fallback}"'
    if fallback != name:
        value += f"; filename*=UTF-8''{quote(name, safe='')}"
    return value


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@router.post("/requests/{request_id}/documents", response_model=DocumentOut, status_code=201)
def upload_document(
    request_id: int,
    db: DbSession,
    actor: Annotated[User, require("expense.submit")],
    file: Annotated[UploadFile, File()],
    expense_item_id: Annotated[int | None, Form()] = None,
) -> DocumentOut:
    """Upload a proof document (PDF, JPG or PNG, checked by content), optionally onto an editable item."""
    document = service.upload_document(db, actor, request_id, file.file, file.filename, expense_item_id)
    return requests.document_out(document, {actor.id: actor.full_name})


@router.get(
    "/documents/{document_id}",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/pdf": {}, "image/jpeg": {}, "image/png": {}}}},
)
def download_document(document_id: int, db: DbSession, user: CurrentUser) -> StreamingResponse:
    """The file as a download; 404 unless the parent request is visible to the caller."""
    document, chunks = service.open_document(db, user, document_id)
    return StreamingResponse(
        chunks,
        media_type=document.mime_type,
        headers={
            "Content-Disposition": content_disposition(document.original_name),
            "Content-Length": str(document.size_bytes),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(
    document_id: int, db: DbSession, actor: Annotated[User, require("expense.submit")]
) -> Response:
    service.delete_document(db, actor, document_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Stage 4: the requestor's expenses
# ---------------------------------------------------------------------------

@router.put("/requests/{request_id}/expenses", response_model=RequestDetail)
def save_expenses(
    request_id: int, body: ExpensesSave, db: DbSession, actor: Annotated[User, require("expense.submit")]
) -> RequestDetail:
    """Save the draft: update items with ``id``, create the rest, delete editable items left out."""
    return requests.to_detail(db, actor, service.save_expenses(db, actor, request_id, body))


@router.post("/requests/{request_id}/expenses/submit", response_model=RequestDetail)
def submit_expenses(
    request_id: int, body: VersionBody, db: DbSession, actor: Annotated[User, require("expense.submit")]
) -> RequestDetail:
    return requests.to_detail(db, actor, service.submit_expenses(db, actor, request_id, body.version))


# ---------------------------------------------------------------------------
# Stage 5: the approver's review
# ---------------------------------------------------------------------------

@router.post("/requests/{request_id}/items/{item_id}/review", response_model=RequestDetail)
def review_item(
    request_id: int,
    item_id: int,
    body: ItemReviewBody,
    db: DbSession,
    actor: Annotated[User, require("expense.review")],
) -> RequestDetail:
    return requests.to_detail(db, actor, service.review_item(db, actor, request_id, item_id, body))


@router.post("/requests/{request_id}/expenses/send-back", response_model=RequestDetail)
def send_back_expenses(
    request_id: int, body: VersionBody, db: DbSession, actor: Annotated[User, require("expense.review")]
) -> RequestDetail:
    return requests.to_detail(db, actor, service.send_back_expenses(db, actor, request_id, body.version))


@router.post("/requests/{request_id}/expenses/approve", response_model=RequestDetail)
def approve_expenses(
    request_id: int, body: VersionBody, db: DbSession, actor: Annotated[User, require("expense.review")]
) -> RequestDetail:
    return requests.to_detail(db, actor, service.approve_expenses(db, actor, request_id, body.version))


# ---------------------------------------------------------------------------
# Stage 6: Finance verification
# ---------------------------------------------------------------------------

@router.post("/requests/{request_id}/items/{item_id}/verify", response_model=RequestDetail)
def verify_item(
    request_id: int,
    item_id: int,
    body: ItemVerifyBody,
    db: DbSession,
    actor: Annotated[User, require("expense.verify")],
) -> RequestDetail:
    return requests.to_detail(db, actor, service.verify_item(db, actor, request_id, item_id, body))


@router.post("/requests/{request_id}/expenses/return", response_model=RequestDetail)
def return_expenses(
    request_id: int, body: VersionBody, db: DbSession, actor: Annotated[User, require("expense.verify")]
) -> RequestDetail:
    return requests.to_detail(db, actor, service.return_expenses(db, actor, request_id, body.version))
