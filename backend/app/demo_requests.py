"""Demo fund requests in every workflow status, built through the real services.

Run with:  python -m app.seed --with-requests

Going through the services (not raw inserts) means budgets, ledger rows, audit history,
notifications and stored proof files are exactly what the app would produce. Local only;
does nothing if requests already exist.
"""

import io
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.expenses import service as expenses
from app.modules.expenses.schemas import ExpenseItemIn, ExpensesSave, ItemReviewBody, ItemVerifyBody
from app.modules.finance import service as finance
from app.modules.fund_requests import service as requests
from app.modules.fund_requests.models import FundRequest
from app.modules.fund_requests.schemas import (
    DisburseBody,
    LiquidateBody,
    ReasonBody,
    RequestCreate,
    VersionBody,
)
from app.modules.projects.models import Project

# A minimal valid PDF, so the content-type check accepts it like a real receipt.
_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


class _Demo:
    def __init__(self, db: Session) -> None:
        self.db = db
        users = {u.email: u for u in db.scalars(select(User))}
        self.req_a = users["req.a@demo.local"]
        self.req_b = users["req.b@demo.local"]
        self.approver = users["approver.a@demo.local"]
        self.finance = users["finance@demo.local"]
        projects = {p.code: p for p in db.scalars(select(Project))}
        self.prj1 = projects["PRJ-001"]
        self.prj2 = projects["PRJ-002"]

    def _version(self, rid: int) -> int:
        return self.db.get(FundRequest, rid).version

    def raise_(self, who: User, project: Project, amount: str, purpose: str) -> int:
        body = RequestCreate(
            project_id=project.id,
            approver_id=self.approver.id,
            amount_requested=Decimal(amount),
            purpose=purpose,
        )
        return requests.create(self.db, who, body).id

    def approve(self, rid: int) -> None:
        requests.approve(self.db, self.approver, rid, VersionBody(version=self._version(rid)))

    def reject(self, rid: int, reason: str) -> None:
        requests.reject(self.db, self.approver, rid, ReasonBody(version=self._version(rid), reason=reason))

    def disburse(self, rid: int, days_ago: int = 6) -> None:
        body = DisburseBody(
            version=self._version(rid),
            txn_ref=f"NEFT-{uuid.uuid4().hex[:8].upper()}",
            disbursed_on=date.today() - timedelta(days=days_ago),
            notes="Paid to the requestor's salary account",
        )
        finance.disburse(self.db, self.finance, rid, body, str(uuid.uuid4()))

    def expenses(
        self, who: User, rid: int, items: list[tuple[str, str, str]], justification: str | None = None
    ) -> list[int]:
        """items: (description, amount, category); one PDF proof each. Submits and returns item ids."""
        payload = []
        for n, (description, amount, category) in enumerate(items):
            doc = expenses.upload_document(self.db, who, rid, io.BytesIO(_PDF), f"receipt-{n + 1}.pdf", None)
            payload.append(
                ExpenseItemIn(
                    description=description,
                    amount=Decimal(amount),
                    category=category,
                    expense_date=date.today() - timedelta(days=3 + n),
                    document_ids=[doc.id],
                )
            )
        expenses.save_expenses(
            self.db,
            who,
            rid,
            ExpensesSave(
                version=self._version(rid),
                items=payload,
                overspend_justification=justification,
            ),
        )
        expenses.submit_expenses(self.db, who, rid, self._version(rid))
        return [i.id for i in requests.expense_items(self.db, rid)]

    def review(self, rid: int, item_id: int, decision: str, reason: str | None = None) -> None:
        body = ItemReviewBody(version=self._version(rid), decision=decision, reason=reason)
        expenses.review_item(self.db, self.approver, rid, item_id, body)

    def verify(self, rid: int, item_id: int) -> None:
        body = ItemVerifyBody(version=self._version(rid), decision="VERIFIED")
        expenses.verify_item(self.db, self.finance, rid, item_id, body)

    def through_review(self, rid: int, item_ids: list[int]) -> None:
        for item_id in item_ids:
            self.review(rid, item_id, "APPROVED")
        expenses.approve_expenses(self.db, self.approver, rid, self._version(rid))

    def through_verification(self, rid: int, item_ids: list[int]) -> None:
        for item_id in item_ids:
            self.verify(rid, item_id)


def create_demo_requests(db: Session) -> list[str]:
    """One request in each status. Returns 'REF status' lines for the seed summary."""
    if db.scalar(select(func.count()).select_from(FundRequest)):
        return []
    d = _Demo(db)

    # Stage 2: waiting for the approver
    d.raise_(d.req_a, d.prj1, "18500.00", "Medicines and consumables for health camp 3")

    # Back at Stage 1: rejected with a reason
    rid = d.raise_(d.req_b, d.prj1, "9000.00", "Tent hire for the Karnal block camp")
    d.reject(rid, "Attach the vendor's quotation and split tent hire from transport")

    # Stage 3: approved, waiting for Finance to pay
    rid = d.raise_(d.req_a, d.prj2, "45000.00", "Bookshelves and reading tables for school 1")
    d.approve(rid)

    # Stage 4: paid, waiting for expenses
    rid = d.raise_(d.req_a, d.prj1, "25000.00", "Travel and stay for the medical team, camp 4")
    d.approve(rid)
    d.disburse(rid)

    # Stage 5: expenses submitted, waiting for the approver
    rid = d.raise_(d.req_b, d.prj1, "12000.00", "Printing of health awareness leaflets")
    d.approve(rid)
    d.disburse(rid)
    d.expenses(
        d.req_b,
        rid,
        [
            ("Leaflet printing, 5,000 copies", "9800.00", "Services"),
            ("Courier to block offices", "1650.00", "Services"),
        ],
    )

    # Back at Stage 4: an item rejected by the approver
    rid = d.raise_(d.req_a, d.prj2, "20000.00", "Library books, first batch")
    d.approve(rid)
    d.disburse(rid)
    first, second = d.expenses(
        d.req_a,
        rid,
        [("120 storybooks", "14500.00", "Supplies"), ("Book covers and labels", "4200.00", "Supplies")],
    )
    d.review(rid, first, "APPROVED")
    d.review(rid, second, "REJECTED", "The bill is for a different school; attach the right one")
    expenses.send_back_expenses(db, d.approver, rid, d._version(rid))

    # Stage 6: approved expenses, waiting for Finance verification (overspend with justification)
    rid = d.raise_(d.req_a, d.prj1, "30000.00", "Diagnostic kits for camp 2")
    d.approve(rid)
    d.disburse(rid, days_ago=12)
    ids = d.expenses(
        d.req_a,
        rid,
        [
            ("Blood sugar test kits", "21000.00", "Supplies"),
            ("Blood pressure monitors", "11200.00", "Equipment"),
        ],
        justification="Kit prices rose after the supplier's October revision; quotation on the first item.",
    )
    d.through_review(rid, ids)

    # Liquidated with a top-up (spent more than paid, extra was reserved at submission)
    rid = d.raise_(d.req_b, d.prj1, "15000.00", "Volunteer travel for camp 1")
    d.approve(rid)
    d.disburse(rid, days_ago=20)
    ids = d.expenses(
        d.req_b,
        rid,
        [("Bus fares, 14 volunteers", "9600.00", "Travel"), ("Meals on camp days", "6150.00", "Meals")],
        justification="Two extra volunteers joined on the second day.",
    )
    d.through_review(rid, ids)
    d.through_verification(rid, ids)
    finance.liquidate(db, d.finance, rid, LiquidateBody(version=d._version(rid)))

    # Liquidated with a balance return (spent less than paid)
    rid = d.raise_(d.req_a, d.prj2, "10000.00", "Reading corner furniture")
    d.approve(rid)
    d.disburse(rid, days_ago=18)
    ids = d.expenses(d.req_a, rid, [("Floor mats and cushions", "7350.00", "Supplies")])
    d.through_review(rid, ids)
    d.through_verification(rid, ids)
    finance.liquidate(
        db, d.finance, rid, LiquidateBody(version=d._version(rid), balance_return_ref="UTR-552018")
    )

    # Withdrawn after a rejection
    rid = d.raise_(d.req_b, d.prj1, "6000.00", "Banners for camp publicity")
    d.reject(rid, "Publicity is covered by the district office this quarter")
    requests.withdraw(db, d.req_b, rid, VersionBody(version=d._version(rid)))

    rows = db.execute(select(FundRequest.ref_no, FundRequest.status).order_by(FundRequest.id)).all()
    return [f"{ref} {status}" for ref, status in rows]
