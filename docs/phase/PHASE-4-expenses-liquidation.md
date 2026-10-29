# Phase 4: Expenses, documents, review, verification and liquidation (stages 4–6)

| | |
|---|---|
| **Branch** | `feat/phase-4-expenses-liquidation` (from the committed Phase 3 branch) |
| **Built as** | Backend and frontend together, against the **API contract in §3** |
| **Architecture references** | Arch PDF §7.3 (transition table), §9 (overage, settlement), §11.3 #10–12, §11.4 (uploads), §13.2 (Action Panel); decisions D4, D5, D6, D9 |
| **Requirements covered** | FR-09, FR-10, FR-11, FR-12, FR-13, FR-14, FR-15 (settlement), FR-16 (stages 4–6), FR-21 (expense events), FR-23 (liquidation), FR-24 (files) |
| **Builds on** | Phase 3 code: `fund_requests/workflow.py` (`TRANSITIONS`, `allowed_actions`, `apply`, `ensure_version`), `policies.py`, `service.py`, `finance/service.py`, `finance/budget.py` (`adjust_overage`, `settle`, `overage_for`, `settlement`), request pages in `frontend/src/pages/requests/` |
| **Out of scope** | Anything not in the architecture: no S3, no virus scan, no reports/export, no multi-level approval |

---

## 1. Rules this phase implements (from the architecture, not new)

| Rule | Source |
|---|---|
| Every expense item needs a proof document (PDF, JPG or PNG, checked by file **content**, ≤ `MAX_UPLOAD_MB`) | D6, arch §11.4 #23 |
| Expense total above the requested amount needs a justification | FR-10, D4 |
| The part of the total above what was disbursed (the overage) must fit the project's available budget and is **reserved** at submission; it is paid as a top-up at liquidation | D4, arch §9.1 |
| Approver reviews each item; final approval only when every item is approved; any rejection sends the request back to Stage 4 | FR-11, FR-12 |
| Items approved in an earlier round stay approved and are locked; rejected items are editable; new items can be added | D9 |
| Items Finance returns go back to approval PENDING, so they pass Stage 5 again before Finance sees them | D9, problem statement §5 |
| Liquidation only when every item is verified; underspend needs a balance-return reference; one liquidation per request | D5, FR-14, FR-23 |
| Files are stored under random keys, served only through an endpoint that checks the parent request's visibility, as downloads | arch §11.4 #22–23 |
| Same locking, version and audit rules as Phase 3 | arch §11.2 |

---

## 2. Transitions added in this phase

Every write below takes the request `version` and increments it (item-level actions too), so stale screens get a 409.

| From | Action | Who | To | Guards and side effects |
|---|---|---|---|---|
| PENDING_EXPENSE_SUBMISSION, EXPENSE_RETURNED | `save_expenses` | `expense.submit`; the requestor | *(unchanged)* | Upsert editable items; locked items may not change (422) |
| PENDING_EXPENSE_SUBMISSION, EXPENSE_RETURNED | `submit_expenses` | `expense.submit`; the requestor | PENDING_EXPENSE_APPROVAL | ≥ 1 item; every item has ≥ 1 document; no expense date in the future; total > requested ⇒ justification; lock project → `budget.adjust_overage(overage_for(total, disbursed_total))` (422 if over budget); items with approval REJECTED or verification RETURNED reset to approval PENDING + verification PENDING; audit `expenses_submitted` (snapshot: items, total); notify approver |
| PENDING_EXPENSE_APPROVAL | `review_item` | `expense.review`; the assigned approver | *(unchanged)* | Item must not be APPROVED already; decision APPROVED / REJECTED; reason required for REJECTED; audit `item_approved` / `item_rejected` |
| PENDING_EXPENSE_APPROVAL | `send_back_expenses` | `expense.review`; the assigned approver | EXPENSE_RETURNED | No item PENDING and ≥ 1 REJECTED; audit `expenses_sent_back`; notify requestor |
| PENDING_EXPENSE_APPROVAL | `approve_expenses` | `expense.review`; the assigned approver | PENDING_VERIFICATION | Every item APPROVED; audit `expenses_approved`; notify Finance (active FINANCE + SUPER_ADMIN with finance ops) |
| PENDING_VERIFICATION | `verify_item` | `expense.verify` | *(unchanged)* | Item verification must not be VERIFIED already; decision VERIFIED / RETURNED; reason required for RETURNED; audit `item_verified` / `item_returned` |
| PENDING_VERIFICATION | `return_expenses` | `expense.verify` | EXPENSE_RETURNED | No item verification PENDING and ≥ 1 RETURNED; RETURNED items get approval PENDING (pass Stage 5 again); audit `expenses_returned`; notify requestor |
| PENDING_VERIFICATION | `liquidate` | `request.liquidate` | LIQUIDATED | Every item VERIFIED; `verified_total = Σ items`; `disbursed = disbursed_total`; verified < disbursed ⇒ `balance_return_ref` required (422); lock project → `budget.settle(verified, disbursed)`; insert `liquidations` (unique per request → 409 on duplicate); audit `liquidated`; notify requestor and approver |

**Editable item** = approval PENDING or REJECTED, **or** verification RETURNED. An item approved (and not returned by Finance) is locked.

---

## 3. API contract

All under `/api`; money = 2-decimal strings; same error envelope and codes as Phase 3.

### 3.1 Shapes

```jsonc
// DocumentOut
{ "id": 5, "original_name": "invoice-112.pdf", "mime_type": "application/pdf", "size_bytes": 81234,
  "expense_item_id": 9, "uploaded_by_name": "Requestor A", "created_at": "..." }

// ExpenseItemOut
{
  "id": 9, "description": "Bus tickets", "amount": "1200.00", "expense_date": "2026-10-21",
  "category": "Travel", "comment": null,
  "approval_status": "PENDING", "approval_reason": null, "approved_by_name": null, "approved_at": null,
  "verification_status": "PENDING", "verification_reason": null, "verified_by_name": null, "verified_at": null,
  "editable": true,
  "documents": [DocumentOut]
}

// LiquidationOut
{ "verified_total": "32000.00", "disbursed_total": "30000.00", "balance_returned": "0.00",
  "balance_return_ref": null, "top_up_amount": "2000.00", "completed_by_name": "Finance Officer", "completed_at": "..." }

// RequestDetail (Phase 3 shape) gains:
{
  "overspend_justification": null,
  "expense_total": "32000.00",          // Σ all items
  "approved_total": "0.00",             // Σ items with approval APPROVED
  "verified_total": "0.00",             // Σ items with verification VERIFIED
  "overage_reserved": "2000.00",
  "expense_items": [ExpenseItemOut],    // ordered by id
  "unattached_documents": [DocumentOut],// uploaded but not yet linked to an item (requestor's draft uploads)
  "liquidation": LiquidationOut | null
}
```

### 3.2 Endpoints

| Method + path | Body | Success | Notes |
|---|---|---|---|
| `POST /requests/{id}/documents` | multipart: `file`; optional form field `expense_item_id` | 201 `DocumentOut` | Requestor only, status PENDING_EXPENSE_SUBMISSION or EXPENSE_RETURNED, item (if given) must be editable. Type sniffed with `filetype` → only `application/pdf`, `image/jpeg`, `image/png`; empty or > `MAX_UPLOAD_MB` → **400** code `invalid_file` with a clear message. No `version` (uploads don't change the request) |
| `GET /documents/{id}` | — | 200 file stream | `Content-Disposition: attachment; filename="…"`, correct `Content-Type`; **404** if the parent request is not visible |
| `DELETE /documents/{id}` | — | 204 | Requestor, while the request is editable and the document is unattached or on an editable item |
| `PUT /requests/{id}/expenses` | `{version, items: [{id?, description, amount, expense_date, category?, comment?, document_ids: [..]}], overspend_justification?}` | 200 `RequestDetail` | = `save_expenses`. Items with `id` are updated (must be editable); items without `id` are created; editable items missing from the list are deleted (their documents become unattached); locked items are left untouched and may be omitted. `document_ids` must belong to this request. `description` 1–500, `amount` = `Money`, `category` ≤ 100, `comment` ≤ 1000 |
| `POST /requests/{id}/expenses/submit` | `{version}` | 200 `RequestDetail` | |
| `POST /requests/{id}/items/{item_id}/review` | `{version, decision: "APPROVED" \| "REJECTED", reason?}` | 200 `RequestDetail` | reason 1–1000 when REJECTED (400 if missing) |
| `POST /requests/{id}/expenses/send-back` | `{version}` | 200 `RequestDetail` | approver |
| `POST /requests/{id}/expenses/approve` | `{version}` | 200 `RequestDetail` | approver: final approval |
| `POST /requests/{id}/items/{item_id}/verify` | `{version, decision: "VERIFIED" \| "RETURNED", reason?}` | 200 `RequestDetail` | reason 1–1000 when RETURNED |
| `POST /requests/{id}/expenses/return` | `{version}` | 200 `RequestDetail` | Finance send-back |
| `POST /requests/{id}/liquidate` | `{version, balance_return_ref?}` | 200 `RequestDetail` | `balance_return_ref` 1–100, required when verified < disbursed |

### 3.3 `allowed_actions` additions (only when the guard is met, so the UI shows only usable buttons)

| Value | When |
|---|---|
| `edit_expenses` | status PENDING_EXPENSE_SUBMISSION or EXPENSE_RETURNED, user is the requestor (`expense.submit`) |
| `submit_expenses` | same, and ≥ 1 item exists |
| `review_items` | status PENDING_EXPENSE_APPROVAL, user is the assigned approver, ≥ 1 item not APPROVED |
| `send_back_expenses` | same status/user, no item PENDING, ≥ 1 REJECTED |
| `approve_expenses` | same status/user, every item APPROVED |
| `verify_items` | status PENDING_VERIFICATION, `expense.verify`, ≥ 1 item not VERIFIED |
| `return_expenses` | same, no verification PENDING, ≥ 1 RETURNED |
| `liquidate` | status PENDING_VERIFICATION, `request.liquidate`, every item VERIFIED |

(Phase 3's `return` on PENDING_EXPENSE_SUBMISSION stays.)

### 3.4 Notifications
`expenses_submitted` → approver · `expenses_sent_back` → requestor · `expenses_approved` → Finance · `expenses_returned` → requestor · `request_liquidated` → requestor and approver. Messages carry the ref and the relevant amount.

### 3.5 Audit actions (entity `fund_requests`)
`expenses_submitted`, `item_approved`, `item_rejected`, `expenses_sent_back`, `expenses_approved`, `item_verified`, `item_returned`, `expenses_returned`, `liquidated` (snapshot: verified_total, disbursed_total, balance_returned, balance_return_ref, top_up_amount), plus `document_uploaded` / `document_deleted` (snapshot: document id, original_name).

---

## 4. Backend tasks

| File | Content |
|---|---|
| `app/core/storage.py` | `Storage` protocol (`save(stream, max_bytes) -> (key, size, sha256)`, `open(key)`, `delete(key)`); `LocalStorage(media_root)`; keys `uuid4().hex`; the original filename is never used as a path; reads in chunks and stops above the size limit |
| `app/modules/expenses/schemas.py`, `service.py`, `router.py` | documents (upload / download / delete), `save_expenses`, `submit_expenses`, `review_item`, `send_back_expenses`, `approve_expenses`, `verify_item`, `return_expenses`; files saved before the DB transaction and deleted again if it fails |
| `app/modules/finance/service.py` | `liquidate` |
| `app/modules/fund_requests/workflow.py` | new `Action`s + `TRANSITIONS` rows of §2; `allowed_actions` extended per §3.3 |
| `app/modules/fund_requests/schemas.py` / detail builder | §3.1 additions to `RequestDetail` |
| `app/main.py` | register the new routers |

### Backend tests (real PostgreSQL)
| File | Proves |
|---|---|
| `test_documents.py` | PDF/JPG/PNG accepted; an `.exe` renamed to `.pdf` rejected (content check); empty and oversize rejected; download as attachment with the right type; other requestor / other approver → 404; upload outside Stage 4 → 409/403; delete rules |
| `test_expenses_api.py` | save draft (create / update / delete); locked items can't change; submit guards (no items, missing proof, future date, justification needed above requested); overage reserved (and 422 when the project can't cover it); review / send-back / final-approve guards; approved items stay locked across rounds |
| `test_verification_liquidation.py` | verify / return guards; Finance return resets approval to PENDING (item passes Stage 5 again); liquidate overspend (TOPUP) and underspend (RETURN + ref required); duplicate liquidation → 409; `reconcile()` equals project totals after every step |
| `test_full_flow.py` | all six stages through the API, with the project buckets checked against arch §9.2 after every stage; history and notifications |
| extend `test_concurrency.py` | two parallel liquidations → exactly one succeeds, one `liquidations` row |
| extend `test_workflow_table.py` | the §2 pairs |

---

## 5. Frontend tasks

Same design system and patterns as the Phase 3 request pages (`PageHeader`, `Panel`, `Badges`, `StageTracker`, `RequestDialogs`, `cache.ts`, `lib/format.ts`). No new colours outside `theme.ts`.

| Area | Content |
|---|---|
| `src/api/types.ts`, `endpoints.ts` | §3.1 shapes; `expensesApi` (upload via `FormData`, download via `api.get(..., {responseType: 'blob'})` + object URL so the auth header is sent, delete, save, submit, review, sendBack, approveAll, verify, returnAll, liquidate) |
| `pages/requests/ExpensesPanel.tsx` | Expenses section on the request detail page, shown from Stage 4 on: items table (date, description, category, amount, proof links, approval status, verification status, reasons), totals row (Expenses · Requested · Disbursed · Difference) |
| `pages/requests/ExpenseEditor.tsx` | when `edit_expenses`: add / edit / remove editable items (drawer or inline form: description, amount ₹, date, category select [Travel, Lodging, Meals, Supplies, Services, Equipment, Other], comment, proof upload with antd `Upload` + `customRequest`), locked items shown read-only with a lock note, live total vs requested and disbursed, justification field appears when total > requested, **Save draft** and **Submit expenses** (only when `submit_expenses`) |
| Review mode | when `review_items`: per-item Approve / Reject (reason modal); footer buttons **Send back** / **Approve expenses** shown only when in `allowed_actions` |
| Verification mode | when `verify_items` / `return_expenses` / `liquidate`: per-item Verify / Return (reason); **Return to requestor**; **Liquidate** dialog showing verified vs disbursed, balance-return ref field when underspent, top-up note when overspent |
| Liquidation summary | when LIQUIDATED: panel with verified total, disbursed, balance returned (+ ref) or top-up, completed by / at |
| Figure strip | add Expenses and Verified once Stage 4 has items |
| 409 handling | same "This request changed — Reload" state as Phase 3 |

---

## 6. Definition of done
- [ ] Every endpoint in §3.2 behaves as specified, including 400 / 403 / 404 / 409 / 422 cases.
- [ ] `reconcile()` matches project totals after every stage; overspend and underspend both settle correctly (arch §9.2).
- [ ] `pytest`, `ruff check .`, `npm run typecheck`, `npm run build` clean.
- [ ] In the browser, one request goes through all six stages: expenses with uploads → an item rejected → corrected → approved → Finance returns an item → it passes Stage 5 again → verified → liquidated (once overspent with top-up, once underspent with balance return); each role sees only its actions; no console errors at 1536×701.
