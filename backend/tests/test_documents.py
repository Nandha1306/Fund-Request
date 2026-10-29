"""Proof documents (phase 4 §3.2, arch §11.4): type checked by content, size limits, random storage keys,
download as an attachment through the parent request's visibility, upload / delete rules."""

import hashlib
import io

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.enums import ApprovalStatus
from app.core.storage import FileTooLarge, LocalStorage
from app.modules.audit.models import AuditLog
from app.modules.expenses.models import Document, ExpenseItem
from app.modules.expenses.router import content_disposition
from app.modules.expenses.service import clean_filename
from tests.conftest import EXE_BYTES, JPG_BYTES, PDF_BYTES, PNG_BYTES


def _audit(db, request_id) -> list[tuple[str, dict]]:
    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.entity == "fund_requests", AuditLog.entity_id == request_id)
        .order_by(AuditLog.id)
    )
    return [(r.action, r.snapshot) for r in rows if r.action.startswith("document_")]


# ---------------------------------------------------------------------------
# Upload: accepted types, content sniffing, size
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "name", "mime"),
    [
        (PDF_BYTES, "invoice-112.pdf", "application/pdf"),
        (JPG_BYTES, "bill.jpg", "image/jpeg"),
        (PNG_BYTES, "receipt.png", "image/png"),
    ],
)
def test_pdf_jpg_png_are_accepted_and_stored_under_a_random_key(
    db_session, flow, people, media_root, content, name, mime
):
    rid = flow.at_stage4()["id"]
    body = flow.uploaded(rid, content=content, name=name)
    assert body["original_name"] == name and body["mime_type"] == mime
    assert body["size_bytes"] == len(content) and body["expense_item_id"] is None
    assert body["uploaded_by_name"] == "Requestor A" and body["created_at"]

    row = db_session.get(Document, body["id"])
    assert row.sha256 == hashlib.sha256(content).hexdigest()
    assert len(row.storage_key) == 32 and name not in row.storage_key
    assert (media_root / row.storage_key).read_bytes() == content
    assert flow.ok(flow.get(people.requestor, rid))["unattached_documents"] == [body]
    assert _audit(db_session, rid) == [
        (
            "document_uploaded",
            {
                "document_id": body["id"],
                "original_name": name,
                "expense_item_id": None,
                "mime_type": mime,
                "size_bytes": len(content),
            },
        )
    ]


def test_the_declared_content_type_and_extension_are_ignored(flow):
    rid = flow.at_stage4()["id"]
    body = flow.uploaded(rid, content=PNG_BYTES, name="scan.pdf", content_type="application/pdf")
    assert body["mime_type"] == "image/png"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (EXE_BYTES, "Only PDF, JPG and PNG files are accepted"),
        (b"just some text pretending to be a PDF", "Only PDF, JPG and PNG files are accepted"),
        (b"GIF89a" + bytes(20), "Only PDF, JPG and PNG files are accepted"),
        (b"", "The file is empty"),
    ],
)
def test_wrong_content_or_empty_file_is_400_invalid_file(db_session, flow, media_root, content, message):
    rid = flow.at_stage4()["id"]
    resp = flow.upload(rid, content=content, name="invoice.pdf", content_type="application/pdf")
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "invalid_file"
    assert resp.json()["error"]["message"] == message
    assert db_session.scalar(select(Document.id)) is None
    assert not media_root.exists() or not any(media_root.iterdir())


def test_oversize_file_is_400_and_nothing_is_kept(db_session, flow, media_root, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    rid = flow.at_stage4()["id"]
    resp = flow.upload(rid, content=PDF_BYTES + bytes(1024 * 1024))
    assert resp.status_code == 400
    assert resp.json()["error"] == {
        "code": "invalid_file",
        "message": "The file is larger than the 1 MB limit",
        "details": {"max_upload_mb": 1},
    }
    assert db_session.scalar(select(Document.id)) is None
    assert not any(media_root.iterdir())
    # Exactly at the limit is fine.
    exact = PDF_BYTES + bytes(1024 * 1024 - len(PDF_BYTES))
    assert flow.uploaded(rid, content=exact)["size_bytes"] == 1024 * 1024


def test_missing_file_field_is_400(client, flow, auth_header, people):
    rid = flow.at_stage4()["id"]
    resp = client.post(f"/api/requests/{rid}/documents", headers=auth_header(people.requestor))
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "validation_error"


def test_filename_is_data_never_a_path(flow, media_root):
    rid = flow.at_stage4()["id"]
    body = flow.uploaded(rid, name="..\\..\\etc/passwd.pdf")
    assert body["original_name"] == "passwd.pdf"
    assert clean_filename("../../") == "document" and clean_filename(None) == "document"
    assert clean_filename("bad\r\nname.pdf") == "badname.pdf"
    assert len(clean_filename("x" * 300 + ".pdf")) == 255 and clean_filename("x" * 300 + ".pdf").endswith(
        ".pdf"
    )
    assert all(len(p.name) == 32 for p in media_root.iterdir())


def test_upload_onto_an_item(db_session, flow):
    rid = flow.at_stage4()["id"]
    detail = flow.with_items(rid, "1000.00")
    item_id = detail["expense_items"][0]["id"]
    body = flow.uploaded(rid, item_id=item_id, content=JPG_BYTES, name="second.jpg")
    assert body["expense_item_id"] == item_id
    docs = flow.ok(flow.get(flow.people.requestor, rid))["expense_items"][0]["documents"]
    assert [d["original_name"] for d in docs] == ["proof-0.pdf", "second.jpg"]


def test_upload_onto_a_locked_or_foreign_item_is_422(db_session, flow, media_root):
    first = flow.at_stage4()["id"]
    item_id = flow.with_items(first, "1000.00")["expense_items"][0]["id"]
    other = flow.at_stage4()["id"]
    resp = flow.upload(other, item_id=item_id)
    assert resp.status_code == 422 and resp.json()["error"]["message"] == "Expense item not found"

    db_session.get(ExpenseItem, item_id).approval_status = ApprovalStatus.APPROVED.value
    db_session.flush()
    resp = flow.upload(first, item_id=item_id)
    assert resp.status_code == 422
    assert (
        resp.json()["error"]["message"]
        == f"Expense item {item_id} is approved and locked; it cannot be changed"
    )
    assert len(list(media_root.iterdir())) == 1  # only the proof saved by with_items


# ---------------------------------------------------------------------------
# Upload: who and when
# ---------------------------------------------------------------------------


def test_only_the_requestor_can_upload(flow, people, make_user):
    from app.core.enums import Role

    rid = flow.at_stage4()["id"]
    assert flow.upload(rid, user=people.requestor2).status_code == 404  # not visible to them
    assert flow.upload(rid, user=people.approver).status_code == 403  # no expense.submit
    assert flow.upload(rid, user=people.finance).status_code == 403
    assert flow.upload(rid, user=make_user(Role.SUPER_ADMIN, can_finance_ops=True)).status_code == 403


@pytest.mark.parametrize("stage", ["PENDING_APPROVAL", "PENDING_DISBURSEMENT", "PENDING_EXPENSE_APPROVAL"])
def test_upload_outside_stage_4_is_409(db_session, flow, people, media_root, stage):
    if stage == "PENDING_APPROVAL":
        rid = flow.raised()["id"]
    elif stage == "PENDING_DISBURSEMENT":
        rid = flow.raised()["id"]
        flow.ok(flow.act(people.approver, rid, "approve"))
    else:
        rid = flow.submitted()["id"]
    resp = flow.upload(rid)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == {
        "code": "conflict",
        "message": "Documents can only be changed while the expenses are being prepared",
        "details": {"status": stage},
    }
    files = list(media_root.iterdir()) if media_root.exists() else []
    assert len(files) == (1 if stage == "PENDING_EXPENSE_APPROVAL" else 0)


def test_upload_is_allowed_after_expenses_are_returned(flow):
    body = flow.submitted("30000.00", "10000.00")
    rid, item_id = body["id"], body["expense_items"][0]["id"]
    flow.ok(flow.review(rid, item_id, "REJECTED", reason="Blurry"))
    flow.ok(flow.send_back(rid))
    assert flow.uploaded(rid, item_id=item_id)["expense_item_id"] == item_id


def test_upload_does_not_change_the_version(flow):
    rid = flow.at_stage4()["id"]
    before = flow.version(rid)
    flow.uploaded(rid)
    assert flow.version(rid) == before


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def test_download_is_an_attachment_with_the_right_type(client, flow, people, auth_header, make_user):
    from app.core.enums import Role

    body = flow.submitted()
    doc = body["expense_items"][0]["documents"][0]
    for user in (people.requestor, people.approver, people.finance, make_user(Role.SUPER_ADMIN)):
        resp = client.get(f"/api/documents/{doc['id']}", headers=auth_header(user))
        assert resp.status_code == 200
        assert resp.content == PDF_BYTES
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.headers["content-disposition"] == 'attachment; filename="proof-0.pdf"'
        assert resp.headers["x-content-type-options"] == "nosniff"


def test_download_with_a_non_ascii_name(client, flow, people, auth_header):
    rid = flow.at_stage4()["id"]
    doc = flow.uploaded(rid, content=PNG_BYTES, name="réçu 1.png")
    resp = client.get(f"/api/documents/{doc['id']}", headers=auth_header(people.requestor))
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["content-disposition"] == (
        "attachment; filename=\"r__u 1.png\"; filename*=UTF-8''r%C3%A9%C3%A7u%201.png"
    )
    # Quotes and backslashes never break out of the quoted fallback.
    assert content_disposition('a"b\\c.pdf') == (
        "attachment; filename=\"a_b_c.pdf\"; filename*=UTF-8''a%22b%5Cc.pdf"
    )


def test_download_is_404_when_the_request_is_not_visible(client, flow, people, auth_header):
    rid = flow.at_stage4()["id"]
    doc = flow.uploaded(rid)
    for user in (people.requestor2, people.approver2):
        resp = client.get(f"/api/documents/{doc['id']}", headers=auth_header(user))
        assert resp.status_code == 404
        assert resp.json()["error"]["message"] == "Document not found"
    assert client.get("/api/documents/999999", headers=auth_header(people.finance)).status_code == 404
    assert client.get(f"/api/documents/{doc['id']}").status_code == 401


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_requestor_deletes_an_unattached_document(client, db_session, flow, people, auth_header, media_root):
    rid = flow.at_stage4()["id"]
    doc = flow.uploaded(rid)
    key = db_session.get(Document, doc["id"]).storage_key
    resp = client.delete(f"/api/documents/{doc['id']}", headers=auth_header(people.requestor))
    assert resp.status_code == 204 and resp.content == b""
    assert db_session.get(Document, doc["id"]) is None
    assert not (media_root / key).exists()
    assert flow.ok(flow.get(people.requestor, rid))["unattached_documents"] == []
    assert _audit(db_session, rid)[-1] == (
        "document_deleted",
        {"document_id": doc["id"], "original_name": "invoice.pdf", "expense_item_id": None},
    )
    assert (
        client.delete(f"/api/documents/{doc['id']}", headers=auth_header(people.requestor)).status_code == 404
    )


def test_delete_a_document_of_an_editable_item(client, flow, people, auth_header):
    rid = flow.at_stage4()["id"]
    detail = flow.with_items(rid, "1000.00")
    doc_id = detail["expense_items"][0]["documents"][0]["id"]
    assert client.delete(f"/api/documents/{doc_id}", headers=auth_header(people.requestor)).status_code == 204
    assert flow.ok(flow.get(people.requestor, rid))["expense_items"][0]["documents"] == []


def test_delete_rules(client, db_session, flow, people, auth_header):
    body = flow.submitted("30000.00", "10000.00", "5000.00")
    rid = body["id"]
    approved, rejected = body["expense_items"]
    approved_doc, rejected_doc = approved["documents"][0]["id"], rejected["documents"][0]["id"]

    # Stage 5: nothing can be deleted.
    resp = client.delete(f"/api/documents/{rejected_doc}", headers=auth_header(people.requestor))
    assert resp.status_code == 409

    flow.ok(flow.review(rid, approved["id"]))
    flow.ok(flow.review(rid, rejected["id"], "REJECTED", reason="Wrong receipt"))
    flow.ok(flow.send_back(rid))

    # Others: invisible → 404, no permission → 403.
    assert (
        client.delete(f"/api/documents/{rejected_doc}", headers=auth_header(people.requestor2)).status_code
        == 404
    )
    assert (
        client.delete(f"/api/documents/{rejected_doc}", headers=auth_header(people.approver)).status_code
        == 403
    )
    # An approved (locked) item keeps its proof.
    resp = client.delete(f"/api/documents/{approved_doc}", headers=auth_header(people.requestor))
    assert resp.status_code == 422
    assert resp.json()["error"]["message"] == (
        "This document belongs to an approved expense item and cannot be removed"
    )
    # The rejected item is editable again.
    assert (
        client.delete(f"/api/documents/{rejected_doc}", headers=auth_header(people.requestor)).status_code
        == 204
    )


# ---------------------------------------------------------------------------
# LocalStorage on its own
# ---------------------------------------------------------------------------


def test_local_storage_round_trip_and_limit(tmp_path):
    store = LocalStorage(tmp_path / "files")
    key, size, digest = store.save(io.BytesIO(b"abc" * 100_000), max_bytes=300_000)
    assert (size, digest) == (300_000, hashlib.sha256(b"abc" * 100_000).hexdigest())
    with store.open(key) as fh:
        assert fh.read() == b"abc" * 100_000
    store.delete(key)
    store.delete(key)  # missing is fine
    with pytest.raises(FileTooLarge):
        store.save(io.BytesIO(bytes(300_001)), max_bytes=300_000)
    assert list((tmp_path / "files").iterdir()) == []
    with pytest.raises(ValueError):
        store.open("../secret")
