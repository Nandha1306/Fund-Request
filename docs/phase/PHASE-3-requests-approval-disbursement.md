# Phase 3: Request workflow, stages 1–3 (raise, approve, disburse)

| | |
|---|---|
| **Branch** | `feat/phase-3-request-approval-disbursement` (stacked on `feat/frontend-foundation`) |
| **Built as** | Backend and frontend together, against the **API contract in §3** (the single source of truth for both sides) |
| **Architecture references** | Architecture PDF §7 (state machine, transition table), §8 (layer path, error contract), §9 (budget events), §11 (concurrency and edge cases), §13.2 (Action Panel) |
| **Requirements covered** | FR-03, FR-05, FR-06, FR-07, FR-08, FR-16 (stages 1–3), FR-17, FR-19, FR-21 (submission, approval, rejection, disbursement), FR-22 (requests), FR-23 (disbursement) |
| **Out of scope** | Stages 4–6 (expenses, documents, item review, verification, liquidation) are Phase 4. Do not build them. |

---

## 1. Rules this phase implements (from the architecture, not new)

| Rule | Source |
|---|---|
| Budget is reserved on submit and released when the request goes back to Stage 1 or is withdrawn | D1, arch §9.1 |
| One approver per request; Finance / Super Admin can reassign **only while PENDING_APPROVAL** | D2, FR-17 |
| A request returned after payout keeps its disbursement; only the shortfall can be paid later; a zero shortfall skips Stage 3 | D3 |
| Withdraw only from RETURNED_FOR_CORRECTION and only if nothing was disbursed | D10 |
| Inactive project blocks new requests **and** Stage 1 resubmissions | D11 |
| Invisible request → 404; visible but not allowed → 403 | D12 |
| Every transition writes an audit row: actor, time, from_status, to_status, reason, snapshot | arch §5, §10.2 |
| Lock order is always **project, then request**; every money change goes through `finance/budget.py` | arch §11.2 #1, #7 |
| A reason is mandatory on every reject / return | problem statement §5 |

---

## 2. Statuses and transitions used in this phase

| From | Action | Who (permission + object rule) | To | Side effects |
|---|---|---|---|---|
| *(new)* | `submit` | `request.create`; requestor is a member of an ACTIVE project; approver is an active APPROVER and not the requestor | PENDING_APPROVAL | lock project; `budget.reserve(amount)`; audit `submitted`; notify approver |
| RETURNED_FOR_CORRECTION | `resubmit` | `request.resubmit`; the requestor; project still ACTIVE; approver valid | PENDING_APPROVAL | lock project; `budget.reserve(reservation_for_submit(amount, disbursed_total))`; audit `resubmitted`; notify approver |
| RETURNED_FOR_CORRECTION | `withdraw` | `request.withdraw`; the requestor; `disbursed_total == 0` | WITHDRAWN | `budget.release`; audit `withdrawn`; notify approver |
| PENDING_APPROVAL | `approve` | `request.approve`; the assigned approver | PENDING_DISBURSEMENT, **or** PENDING_EXPENSE_SUBMISSION when `disbursed_total >= amount_requested` (D3 skip) | `amount_approved = amount_requested`; audit `approved` (+ system audit `disbursement_skipped` on skip); notify requestor and Finance |
| PENDING_APPROVAL | `reject` (reason) | `request.approve`; the assigned approver | RETURNED_FOR_CORRECTION | `budget.release`; audit `rejected`; notify requestor |
| PENDING_APPROVAL | `reassign` (approver_id) | `request.reassign` | PENDING_APPROVAL | new approver active APPROVER, ≠ requestor, ≠ current approver; audit `reassigned` with old/new; notify both approvers |
| PENDING_DISBURSEMENT | `disburse` | `request.disburse`; `Idempotency-Key` header | PENDING_EXPENSE_SUBMISSION | amount = `amount_approved − disbursed_total` (> 0); insert `disbursements`; `budget.disburse`; audit `disbursed`; notify requestor |
| PENDING_DISBURSEMENT | `return` (reason) | `request.return` | RETURNED_FOR_CORRECTION | `budget.release`; audit `returned`; notify requestor |
| PENDING_EXPENSE_SUBMISSION | `return` (reason) | `request.return` | RETURNED_FOR_CORRECTION | disbursement row **kept**, money stays outstanding (D3); audit `returned`; notify requestor |

Anything not in this table → **409**. Audit `entity` for requests is `"fund_requests"`.

---

## 3. API contract (both sides build exactly this)

All paths under `/api`. Money is a decimal **string** with 2 places. Errors use the existing envelope `{"error": {"code", "message", "details"}}`: 400 invalid input, 401, 403, 404, 409 wrong state / stale version / duplicate, 422 business rule.

### 3.1 Shapes

```jsonc
// UserRef
{ "id": 4, "full_name": "Requestor A", "email": "req.a@demo.local" }

// ProjectRef
{ "id": 1, "code": "PRJ-001", "name": "Rural Health Camp", "status": "ACTIVE" }

// RequestSummary  (list rows)
{
  "id": 12, "ref_no": "FR-2026-0012", "status": "PENDING_APPROVAL",
  "project": ProjectRef, "requestor": UserRef, "approver": UserRef,
  "amount_requested": "30000.00", "amount_approved": null,
  "created_at": "...", "updated_at": "..."
}

// Disbursement
{ "id": 3, "amount": "30000.00", "txn_ref": "NEFT-88231", "disbursed_on": "2026-10-29",
  "notes": null, "created_by_name": "Finance Officer", "created_at": "..." }

// RequestDetail  = RequestSummary + :
{
  "purpose": "...", "supporting_info": null,
  "reserved_amount": "30000.00",
  "disbursed_total": "0.00",          // sum of disbursements
  "disbursable_amount": "0.00",       // approved − disbursed_total while PENDING_DISBURSEMENT, else "0.00"
  "version": 3,
  "disbursements": [Disbursement],
  "allowed_actions": ["approve", "reject"]   // for the CURRENT user; see 3.3
}

// HistoryEntry  (audit rows of this request, oldest first)
{ "id": 91, "action": "rejected", "from_status": "PENDING_APPROVAL", "to_status": "RETURNED_FOR_CORRECTION",
  "reason": "Split travel and lodging", "actor_name": "Approver A", "snapshot": {...}, "created_at": "..." }

// NotificationOut
{ "id": 7, "type": "request_submitted", "message": "FR-2026-0012 from Requestor A needs your approval (₹30,000.00)",
  "request_id": 12, "read_at": null, "created_at": "..." }
```

### 3.2 Endpoints

| Method + path | Body / query | Success | Notes |
|---|---|---|---|
| `POST /requests` | `{project_id, approver_id, amount_requested, purpose, supporting_info?}` | 201 `RequestDetail` | `purpose` 1–2000 chars, `supporting_info` ≤ 4000; amount = `Money`. 422: project not active / not a member ("You can only raise requests on active projects you are a member of"), invalid approver, amount above available (`details.available`) |
| `GET /requests` | `status` (repeatable), `project_id`, `requestor_id`, `approver_id`, `date_from`, `date_to` (created_at, inclusive), `q` (ref_no or purpose ILIKE), `mine=action`, `page`, `size` | 200 `Page[RequestSummary]` | Visibility filter always applied. Newest first (`created_at DESC, id DESC`). `mine=action` → see 3.4 |
| `GET /requests/{id}` | — | 200 `RequestDetail` | 404 if not visible |
| `PUT /requests/{id}` | `{version, approver_id, amount_requested, purpose, supporting_info?}` | 200 `RequestDetail` | = **resubmit** |
| `POST /requests/{id}/approve` | `{version}` | 200 `RequestDetail` | |
| `POST /requests/{id}/reject` | `{version, reason}` | 200 `RequestDetail` | `reason` 1–1000 chars after strip, else 400 |
| `POST /requests/{id}/reassign` | `{version, approver_id}` | 200 `RequestDetail` | |
| `POST /requests/{id}/withdraw` | `{version}` | 200 `RequestDetail` | |
| `POST /requests/{id}/disburse` | header `Idempotency-Key` (8–100 chars, **400 if missing**); `{version, txn_ref, disbursed_on, notes?}` | 200 `RequestDetail` | `txn_ref` 1–100 stripped; `disbursed_on` not in the future (422). **Same key again → 200 with the current detail, no second row.** `txn_ref` already used → 409 |
| `POST /requests/{id}/return` | `{version, reason}` | 200 `RequestDetail` | |
| `GET /requests/{id}/history` | — | 200 `list[HistoryEntry]` | same visibility as the request |
| `GET /notifications` | `unread_only` (bool), `page`, `size` | 200 `Page[NotificationOut]` | own notifications only, newest first |
| `GET /notifications/unread-count` | — | 200 `{"count": 3}` | |
| `POST /notifications/{id}/read` | — | 204 | 404 if not the caller's |
| `POST /notifications/read-all` | — | 204 | |

`version` on every write: must equal the request's current `version`, otherwise **409** `"This request was changed by someone else. Reload and try again."` Every successful write increments `version`.

### 3.3 `allowed_actions` (server-side, so the UI never offers what the backend would refuse)

Computed for the current user from status + permission + object rule, using the §2 table:

| Value | When |
|---|---|
| `resubmit`, `withdraw` | status RETURNED_FOR_CORRECTION, user is the requestor (withdraw only if `disbursed_total == 0`) |
| `approve`, `reject` | status PENDING_APPROVAL, user is the assigned approver |
| `reassign` | status PENDING_APPROVAL, user has `request.reassign` |
| `disburse` | status PENDING_DISBURSEMENT, user has `request.disburse` |
| `return` | status PENDING_DISBURSEMENT or PENDING_EXPENSE_SUBMISSION, user has `request.return` |

### 3.4 `mine=action` ("waiting on me")

| User | Requests included |
|---|---|
| requestor of the request | RETURNED_FOR_CORRECTION, PENDING_EXPENSE_SUBMISSION, EXPENSE_RETURNED |
| assigned approver | PENDING_APPROVAL, PENDING_EXPENSE_APPROVAL |
| has `request.disburse` | PENDING_DISBURSEMENT, PENDING_VERIFICATION (any request) |

Union of the rows that apply to the caller.

### 3.5 Notifications written in this phase

| Event | Recipients | `type` |
|---|---|---|
| submitted / resubmitted | approver | `request_submitted` |
| approved | requestor; and every active FINANCE user + SUPER_ADMIN with `can_finance_ops` (unless skipped to Stage 4) | `request_approved` |
| rejected | requestor | `request_rejected` |
| reassigned | new approver and old approver | `request_reassigned` |
| disbursed | requestor | `request_disbursed` |
| returned | requestor | `request_returned` |
| withdrawn | approver | `request_withdrawn` |

Messages include the ref and the amount where relevant, for example `FR-2026-0012 was approved by Approver A`.

### 3.6 Reference numbers

`ref_no = FR-{YYYY}-{n:04d}` where `n` comes from a PostgreSQL **sequence** `fund_request_ref_seq` (new Alembic migration). Unique under concurrency without locking.

---

## 4. Backend tasks

| File | Content |
|---|---|
| `alembic/versions/…_fund_request_ref_sequence.py` | `CREATE SEQUENCE fund_request_ref_seq` / `DROP SEQUENCE` |
| `app/modules/fund_requests/workflow.py` | `Action` enum; `TRANSITIONS: dict[(RequestStatus, Action), Transition(to, permission)]` = the §2 table; `allowed_actions(user, request, disbursed_total)`; `apply(db, request, action, to_status, *, actor_id, expected_version, reason=None, snapshot=None)` → checks the pair is in the table (409), the version (409), sets status, `version += 1`, writes the audit row |
| `app/modules/fund_requests/policies.py` | `visible_requests_filter(user)` (view_all → all, else requestor or approver); `get_visible_request_or_404(db, user, id, *, lock=False)`; `waiting_on_filter(user)` (§3.4) |
| `app/modules/fund_requests/schemas.py` | §3.1 shapes and the request bodies (`version` required on writes) |
| `app/modules/fund_requests/service.py` | `create`, `resubmit`, `approve`, `reject`, `reassign`, `withdraw`. Each: lock project (`budget.lock_project`), then lock the request (`with_for_update` + `populate_existing`), check object rule (403), call `workflow.apply`, money via `budget.*`, notifications via `audit.notify`, **one `db.commit()`** |
| `app/modules/finance/service.py` | `disburse` (idempotency key lookup first → return current detail; amount = approved − disbursed_total; `txn_ref` IntegrityError → 409), `return_to_requestor` |
| `app/modules/fund_requests/router.py` | the request endpoints of §3.2 |
| `app/modules/audit/router.py` / `notifications` | notification endpoints (own module file `app/modules/audit/notifications_router.py` is fine); `subject_label` for `fund_requests` = `"FR-2026-0012 · PRJ-001"` |
| `app/main.py` | register the new routers |

### Backend tests (real PostgreSQL)
| File | Proves |
|---|---|
| `test_workflow_table.py` | every (status, action) pair: allowed ones reach the §2 target, all others 409; `allowed_actions` per role |
| `test_requests_api.py` | create (201, reserved, ledger RESERVE, audit, notification); non-member / inactive project / self or invalid approver / over budget → 422; reject → resubmit → approve; reassign (only PENDING_APPROVAL, notifies both); withdraw (blocked after payout); disburse (amount = shortfall, ledger DISBURSE, status); return from both statuses; stale version 409; missing reason 400; history oldest first; `mine=action` per role; filters and pagination |
| `test_request_visibility.py` | other requestor → 404; other approver → 404 until reassigned; Finance sees all; history 404 when invisible |
| `test_return_after_payout.py` | D3: disburse → return → resubmit same amount → approve skips to PENDING_EXPENSE_SUBMISSION; one disbursement row; `reconcile()` matches |
| `test_concurrency.py` | real commits (own fixture, TRUNCATE cleanup), `threading.Barrier`: two requests for 15,000 and 10,000 against 20,000 available → exactly one 201, one 422, reserved = 15,000; two disbursements with **different** keys → one succeeds, one 409, one row; same key twice → one row; approve vs reassign → exactly one wins |
| `test_notifications_api.py` | own list only, unread count, mark one / all read, 404 on someone else's |

---

## 5. Frontend tasks

Reuse the existing design system only: `PageHeader`, `Panel`, `Badges`, `Budget`, `States`, `ActivityList`, `lib/format.ts`, `lib/labels.ts`, `theme.ts`, `styles.css`. Same table, toolbar, empty-state and modal patterns as the Projects and Users screens. No new colours outside `theme.ts`.

| File | Content |
|---|---|
| `src/api/types.ts`, `src/api/endpoints.ts` | §3.1 types; `requestsApi` (list, get, create, resubmit, approve, reject, reassign, withdraw, disburse with `Idempotency-Key` = `crypto.randomUUID()` generated **once per disburse dialog**, return, history); `notificationsApi` |
| `src/lib/labels.ts` | request status labels + whose turn; audit labels for request actions and `fund_requests` entity |
| `src/components/RequestStatusBadge.tsx` (or in `Badges.tsx`) | status dot + label (Awaiting approval, Returned, Awaiting disbursement, Awaiting expenses, Withdrawn, …) |
| `src/components/StageTracker.tsx` | six stages (Request, Approval, Disbursement, Expenses, Expense review, Liquidation); current one highlighted; returned statuses marked; stages 4–6 simply shown as upcoming |
| `src/pages/requests/RequestsPage.tsx` | `/requests`: tabs **Waiting on me** (`mine=action`) / **All requests**; filters status, project, search; table Ref · Project · Requestor · Approver · Amount · Status · Updated; row click → detail; **New request** button when `request.create` |
| `src/pages/requests/RequestFormPage.tsx` | `/requests/new` and resubmit mode: project select (eligible projects from `GET /projects`, showing available), approver select (`GET /users/approvers`), amount (₹ input like the project form; warns above available), purpose, supporting info; server errors on fields |
| `src/pages/requests/RequestDetailPage.tsx` | `/requests/:id`: header ref + status, crumbs, project link; `StageTracker`; figure strip Requested · Approved · Disbursed · Reserved; details (purpose, supporting info, requestor, approver, dates); **Action panel** rendered only from `allowed_actions`: Approve (confirm), Reject (reason modal), Reassign (approver select), Disburse (amount read-only = `disbursable_amount`, txn ref, date, notes), Return (reason), Resubmit (→ form in resubmit mode), Withdraw (confirm); disbursements table; history timeline. On **409** show "This request changed. Reload" and refetch |
| `src/components/NotificationBell.tsx` | in the sidebar account area: unread count (poll every 30 s), dropdown of latest notifications, click → mark read and open the request, "Mark all read" |
| `src/components/AppLayout.tsx`, `src/App.tsx` | **Requests** nav item for everyone; routes `/requests`, `/requests/new`, `/requests/:id` (lazy) |
| `src/pages/DashboardPage.tsx` | a **Waiting on you** panel (`mine=action`, 6 rows) for every role, above the current content |
| `src/pages/projects/ProjectDetailPage.tsx` | **Requests** tab (requests of this project) |

---

## 6. Definition of done
- [ ] `alembic upgrade head` adds the sequence; `downgrade -1` removes it.
- [ ] Every endpoint in §3.2 behaves as specified, including 403 vs 404 and 409 on stale versions.
- [ ] Concurrency tests pass on real PostgreSQL; `reconcile()` matches after every scenario.
- [ ] `pytest`, `ruff check .`, `npm run typecheck`, `npm run build` all clean.
- [ ] In the browser: requestor raises → approver rejects → requestor resubmits → approver approves → Finance disburses; Finance returns after payout → resubmit → approve skips disbursement; each role sees only its actions; no console errors at 1536×701.
