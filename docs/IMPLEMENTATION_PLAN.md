# Implementation Plan — Fund Request & Liquidation MVP

This is the build plan for the MVP described in
[Fund_Request_Liquidation_MVP_Architecture.pdf](Fund_Request_Liquidation_MVP_Architecture.pdf), which is based on the
[problem statement](project_fund_request_liquidation_problem_statement_revised.pdf). Section numbers like *(arch §9)*
point into the architecture PDF.

- **Time box:** 4–5 hours of build time.
- **Stack:** FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL 16, then React + TypeScript + Ant Design + TanStack Query, run with Docker Compose.
- **Order:** backend rules first, because they are what is graded. The UI comes after the whole flow already works through `/docs`.

---

## 1. Phases at a glance

| Phase | Branch | Goal | Time | Exit criteria |
|---|---|---|---|---|
| 0 | `main` | Skeleton: repo layout, FastAPI app, error contract, Alembic, React shell, Docker Compose | 15 min | `pytest` green, `npm run build` green ✅ |
| 1 | `feat/phase-1-data-model-auth` | Full database schema, first migration, demo users, JWT login, permission dependencies | 35 min | Fresh DB migrates; every demo user can log in; `/me` returns role and permissions |
| 2 | `feat/phase-2-users-projects-budget` | Invitations and user management, projects and members, budget engine and ledger, audit writer | 40 min | Budget unit tests pass, including the worked example (arch §9.2) |
| 3 | `feat/phase-3-request-approval-disbursement` | Workflow engine, stages 1–3, visibility, reassignment, concurrency | 60 min | Stages 1–3 and their rejection routes pass; parallel-reservation test passes |
| 4 | `feat/phase-4-expenses-liquidation` | Document upload, stages 4–6, liquidation with balance return or top-up | 50 min | Full six-stage API flow passes, including both expense rejection loops |
| 5 | `feat/phase-5-frontend` | Login, dashboard, requests, request detail with Action Panel, projects, users | 70 min | One request taken through all six stages in the browser by four users |
| 6 | `chore/phase-6-docs-demo` | README, sample data at every stage, end-to-end run in Docker, final review | 30 min | `docker compose up` gives a working demo from a clean machine |

**If time runs short, cut in this order** (arch §2): audit-log screen → notifications UI → advanced filters → project-member UI (seed the members instead). The workflow, the money rules and duplicate protection are never cut.

---

## 2. Working agreements

- **One branch per phase.** Create it from an up-to-date `main`. Make small commits with conventional messages (`feat:`, `fix:`, `test:`, `chore:`, `docs:`).
- **Before a phase merges into `main`:**
  - `ruff check .` and `pytest` pass in `backend/`;
  - `npm run build` passes in `frontend/`;
  - the phase's manual check (listed under each phase) has been done.
- **Merging:** `git merge --no-ff <branch>` into `main`, so each phase shows up as one unit in the history. Push or open a PR only after the phase is verified.
- **Every new business rule gets a test in the same commit.** Workflow and money rules are tested without HTTP; permissions are tested through the API.
- **Keep the AI chat history** (problem statement §12). Every design decision is also written down in the README.

### Local development loop

```bash
docker compose up -d db                      # PostgreSQL on localhost:5433
cd backend && python -m venv .venv && .venv/Scripts/activate   # (bin/activate on macOS/Linux)
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head && python -m app.seed   # seed exists from phase 1
uvicorn app.main:app --reload                # http://localhost:8000/docs
cd ../frontend && npm install && npm run dev # http://localhost:5173 (proxies /api)
```

### Test database

The tests run against a real PostgreSQL database named `fundreq_test` on the same server. The concurrency tests need real row locks, so SQLite is not an option.

- `conftest.py` creates the schema once per test session.
- Each API test runs inside a transaction that is rolled back afterwards.
- Concurrency tests use separate sessions in threads and clean up with `TRUNCATE`.

---

## 3. Shared definitions (used from phase 1 onwards)

### 3.1 Enums

| Enum | Values |
|---|---|
| `Role` | `REQUESTOR`, `APPROVER`, `FINANCE`, `SUPER_ADMIN` |
| `ProjectStatus` | `ACTIVE`, `INACTIVE` |
| `RequestStatus` | `PENDING_APPROVAL`, `RETURNED_FOR_CORRECTION`, `PENDING_DISBURSEMENT`, `PENDING_EXPENSE_SUBMISSION`, `PENDING_EXPENSE_APPROVAL`, `EXPENSE_RETURNED`, `PENDING_VERIFICATION`, `LIQUIDATED`, `WITHDRAWN` |
| `ApprovalStatus` (item) | `PENDING`, `APPROVED`, `REJECTED` |
| `VerificationStatus` (item) | `PENDING`, `VERIFIED`, `RETURNED` |
| `LedgerEntryType` | `ALLOCATE`, `RESERVE`, `RELEASE`, `DISBURSE`, `LIQUIDATE`, `RETURN`, `TOPUP` |

### 3.2 Permission catalogue (arch §12)

Code checks **permission names**, never role names. The mapping lives in `app/modules/accounts/permissions.py`.

| Permission | Requestor | Approver | Finance | Super Admin |
|---|:-:|:-:|:-:|:-:|
| `request.create` / `request.resubmit` / `request.withdraw` | ✓ | | | |
| `expense.submit` | ✓ | | | |
| `request.approve` / `expense.review` | | ✓ | | |
| `project.manage` / `project.members` | | | ✓ | ✓ |
| `request.reassign` / `request.view_all` / `audit.view` | | | ✓ | ✓ |
| `request.disburse` / `request.return` / `expense.verify` / `request.liquidate` | | | ✓ | only if `can_finance_ops` |
| `user.manage` | | | | ✓ |

Object rules sit on top of permissions: "is the requestor of this request", "is the assigned approver", "is a member of the project".

### 3.3 Money

- Every money value is a `Decimal`, stored as `NUMERIC(14,2)`. Inputs are Pydantic `condecimal(gt=0, max_digits=14, decimal_places=2)`.
- `app/core/money.py` has `q2()` (round half-up to two decimals), `ZERO`, and `MAX_AMOUNT = 99,999,999.99`.
- Never use `float` anywhere in the API, the database or the tests.

### 3.4 Demo users and projects (seed)

All demo passwords are `Demo@1234` (local only; documented in the README).

| Email | Role | Notes |
|---|---|---|
| `admin@demo.local` | Super Admin | `can_finance_ops = false` |
| `finance@demo.local` | Finance | |
| `approver.a@demo.local` | Approver | |
| `approver.b@demo.local` | Approver | used for reassignment |
| `req.a@demo.local` | Requestor | member of PRJ-001 and PRJ-002 |
| `req.b@demo.local` | Requestor | member of PRJ-001 only |

| Code | Name | Allocation | Status |
|---|---|---|---|
| PRJ-001 | Rural Health Camp | 500,000.00 | Active |
| PRJ-002 | School Library Upgrade | 200,000.00 | Active |
| PRJ-003 | Legacy Pilot | 100,000.00 | Inactive |

---

## 4. Phase 0 — Skeleton (`main`) ✅

- [x] Repo layout as in arch §6.1; docs moved to `docs/`
- [x] `app/main.py` with `create_app()`, CORS, `/api/health` (liveness) and `/api/health/ready` (database check)
- [x] `core/config.py` (pydantic-settings), `core/db.py` (engine, `Base` with naming convention, `DbSession` dependency)
- [x] `core/errors.py`: `AppError` → 401/403/404/409/422; schema errors remapped from 422 to 400 (arch §8.2)
- [x] Alembic wired to settings and `Base.metadata`; empty `versions/`
- [x] Six module packages: `accounts`, `projects`, `fund_requests`, `expenses`, `finance`, `audit`
- [x] React + Vite + TypeScript + Ant Design + TanStack Query shell; `/api` proxy; health badge
- [x] Dockerfiles (api, web with nginx) and `docker-compose.yml` (db on 5433, api 8000, web 3000)
- [x] Tests: liveness, error-code mapping, 400 for schema errors

---

## 5. Phase 1 — Data model and authentication

**Branch:** `feat/phase-1-data-model-auth` · **Time:** 35 min · **Architecture:** arch §10, §12

Detailed plan: [phase/PHASE-1-data-model-auth.md](phase/PHASE-1-data-model-auth.md)

### 5.1 Tasks

**Core helpers**
- [ ] `core/money.py`: `Money` type alias, `q2()`, `ZERO`, `MAX_AMOUNT`
- [ ] `core/security.py`:
  - `hash_password()` / `verify_password()` with bcrypt;
  - `create_access_token()` (30 min) and `create_refresh_token()` (7 days), carrying `sub`, `type` and `exp`;
  - `decode_token()`.
- [ ] `core/enums.py`: the enums from §3.1

**Models.** Define every table now so the schema is reviewed once and migrated once.

| File | Tables | Constraints and indexes to include |
|---|---|---|
| `accounts/models.py` | `users`, `invitations` | `email` UNIQUE (lower-case); `token_hash` UNIQUE |
| `projects/models.py` | `projects`, `project_members` | `code` UNIQUE; CHECK `reserved + outstanding + liquidated <= allocated`; CHECK each bucket `>= 0`; PK `(project_id, user_id)` |
| `fund_requests/models.py` | `fund_requests` | `ref_no` UNIQUE; CHECK `amount_requested > 0`; CHECK `requestor_id <> approver_id`; `version` integer; indexes on `(status, project_id)`, `requestor_id`, `approver_id`, `created_at` |
| `finance/models.py` | `disbursements`, `liquidations`, `budget_ledger` | CHECK `amount > 0`; `txn_ref` UNIQUE; `idempotency_key` UNIQUE; `liquidations.request_id` UNIQUE |
| `expenses/models.py` | `expense_items`, `documents` | CHECK `amount > 0`; `storage_key` UNIQUE |
| `audit/models.py` | `audit_log`, `notifications` | `snapshot` JSONB; index `(entity, entity_id)`; index `(user_id, read_at)` |

- [ ] All foreign keys use `ondelete="RESTRICT"`, so history can never be deleted.
- [ ] `app/modules/__init__.py` imports every `models` module, so Alembic sees all tables.
- [ ] `alembic revision --autogenerate -m "initial schema"`. Then **read the migration**: check that every CHECK and UNIQUE constraint is present and the enum types are created.

**Authentication**
- [ ] `accounts/permissions.py`: `ROLE_PERMISSIONS`, plus `permissions_for(user)`, which adds the finance permissions when `can_finance_ops` is true.
- [ ] `accounts/deps.py`:
  - `CurrentUser` dependency: decodes the token, reloads the user and rejects inactive users with 401;
  - `require(permission)` dependency factory.
- [ ] `accounts/router.py`:
  - `POST /api/auth/login` (email and password, returns access and refresh tokens);
  - `POST /api/auth/refresh`;
  - `GET /api/me` (id, name, email, role, permissions).
- [ ] Rate limit the login endpoint with `slowapi` (5 per minute per IP).

**Seed**
- [ ] `app/seed.py` (`python -m app.seed`): idempotent upsert of the §3.4 users, projects and members. It also writes ALLOCATE ledger rows and audit rows for the projects.

### 5.2 Tests
- [ ] `test_security.py`: hash and verify round trip; wrong password fails; expired token is rejected; a refresh token cannot be used as an access token.
- [ ] `test_auth_api.py`: login succeeds; wrong password gives 401; deactivated user gives 401 on login and on `/me` with an old token; `/me` lists the right permissions for each role, including Super Admin with and without `can_finance_ops`.
- [ ] `test_schema.py`: the database rejects `requestor_id = approver_id`, a negative amount, and a second liquidation for the same request (constraints tested directly).

### 5.3 Manual check
1. `docker compose up -d db`, then `alembic upgrade head`, then `python -m app.seed`.
2. In `/docs`, log in as each demo user and call `/api/me` with the token.
3. `alembic downgrade base` followed by `upgrade head` works.

---

## 6. Phase 2 — Users, projects, budget engine, audit

**Branch:** `feat/phase-2-users-projects-budget` · **Time:** 40 min · **Architecture:** arch §3, §9, §10.2

### 6.1 Tasks

**Audit (needed by everything that follows)**
- [ ] `audit/service.py`:
  - `record(db, *, entity, entity_id, action, actor, from_status=None, to_status=None, reason=None, snapshot=None)`;
  - `notify(db, *, user_ids, request_id, type, message)`.
  - Both only add rows to the caller's transaction and never commit.

**Users and invitations** (`accounts`)
- [ ] `POST /api/users/invite` (`user.manage`): takes email and role. Creates an invitation with a random token, stores only its SHA-256 hash, expires in 72 hours, and **returns the link once**.
- [ ] `POST /api/auth/accept-invite`: takes token, full name and password. The token must be single-use and unexpired, and the email must not exist yet. Creates the user with the invited role.
- [ ] `GET /api/users` (`user.manage`): list with filters and pagination.
- [ ] `PATCH /api/users/{id}` (`user.manage`): role, `is_active`, `can_finance_ops`.
  - Super Admin cannot deactivate themselves.
  - Refuse to deactivate an approver who has requests in `PENDING_EXPENSE_APPROVAL` (422, listing those request refs; arch §11.3 #17).
- [ ] `GET /api/users/approvers`: active approvers, for the request form. Excludes the caller.

**Projects** (`projects`)
- [ ] `POST /api/projects`, `PATCH /api/projects/{id}` (`project.manage`): name, description, status, allocation.
  - A new project writes an ALLOCATE ledger row.
  - A changed allocation goes through `budget.change_allocation` (422 if the new value is below the commitments).
  - Activate and deactivate are audited.
- [ ] `GET /api/projects`:
  - Finance and Super Admin see all projects with their four buckets plus `available`.
  - Requestors see only **eligible** projects (active and assigned to them).
  - Approvers see the projects of requests they are involved in.
- [ ] `PUT /api/projects/{id}/members` (`project.members`): replaces the member list with requestor user IDs only. Audited.
- [ ] `GET /api/projects/{id}/ledger` (`project.manage`).

**Budget engine** (`finance/budget.py`, the only code that changes project totals; arch §9.1)
- [ ] Pure functions (no database access, unit-testable):
  - `available(p)`;
  - `reservation_for_submit(amount, disbursed)`;
  - `overage_delta(total_expenses, disbursed, current_overage)`;
  - `settlement(verified, disbursed)`, which returns liquidated, balance return and top-up.
- [ ] Database functions. Each one runs **inside the caller's transaction** and writes a ledger row:
  - `lock_project(db, project_id)` → `select(Project).where(...).with_for_update()`
  - `reserve(db, project, request, amount)`: if `amount > available` raises `BusinessRuleViolation("Amount exceeds the project's available budget", details={available})`
  - `release(db, project, request)`: releases `request.reserved_amount`
  - `disburse(db, project, request, amount)`: reserved → outstanding
  - `adjust_overage(db, project, request, new_overage)`: reserves or releases the difference
  - `settle(db, project, request, verified_total)`: outstanding and overage cleared, liquidated increased, RETURN / TOPUP rows written
  - `change_allocation(db, project, new_allocation)`
- [ ] `core/pagination.py`: `Page[T]` response model and `paginate(query, page, size)` (size at most 100).

### 6.2 Tests
- [ ] `test_budget_unit.py`: every event in arch §9.1; the worked example in arch §9.2 step by step (both the overspend and the underspend branch); allocation below commitments raises an error.
- [ ] `test_users_api.py`:
  - invite, then accept, then log in;
  - token reuse gives an error; an expired token gives an error;
  - a non-admin calling invite gets 403;
  - deactivating an approver with Stage 5 work gives 422.
- [ ] `test_projects_api.py`:
  - Finance creates and edits a project; a requestor gets 403 on create;
  - a requestor sees only eligible projects;
  - deactivation is audited;
  - reducing the allocation below commitments gives 422.

### 6.3 Manual check
In `/docs`: as admin, invite a new requestor and accept with the returned link; as finance, create a project and assign the new requestor; `GET /projects` shows the right buckets.

---

## 7. Phase 3 — Workflow engine, stages 1–3

**Branch:** `feat/phase-3-request-approval-disbursement` · **Time:** 60 min · **Architecture:** arch §7, §8, §11

### 7.1 Tasks

**Workflow engine** (`fund_requests/workflow.py`)
- [ ] `TRANSITIONS: dict[tuple[RequestStatus, Action], Transition]`, where `Transition` holds `(to_status, permission, object_rule)`. This is a 1:1 copy of the arch §7.3 table.
- [ ] `transition(db, request, action, actor, *, reason=None, expected_version)`. It:
  - raises 409 if `(request.status, action)` is not in the table;
  - checks the permission and the object rule, raising 403;
  - requires a reason for every reject or return action (400);
  - runs a conditional update: `UPDATE fund_requests SET status=:to, version=version+1 WHERE id=:id AND status=:from AND version=:v`, raising 409 if zero rows change;
  - writes the audit row (old status, new status, reason, snapshot).

**Visibility** (`fund_requests/policies.py`)
- [ ] `visible_requests(user)`: a query filter where Finance and Super Admin see all, and everyone else sees requests where they are the requestor or the approver.
- [ ] `get_visible_or_404(db, user, request_id)`. Every request endpoint uses it, so an invisible request returns **404**.

**Stage 1–2 services** (`fund_requests/service.py`). Lock order is always **project, then request**.
- [ ] `create(...)`:
  - the project must exist, be active, and the requestor must be a member (otherwise 422 or 403);
  - the approver must be an active approver and not the requestor;
  - lock the project, reserve, insert the request (`ref_no` such as `FR-2026-0001`), audit, notify the approver.
- [ ] `resubmit(...)`: the project must still be active; reserve `max(amount − disbursed, 0)`; status back to PENDING_APPROVAL.
- [ ] `approve(...)`: sets `amount_approved`, then status PENDING_DISBURSEMENT. If the approved amount is already disbursed (the D3 case), it **auto-skips** to PENDING_EXPENSE_SUBMISSION.
- [ ] `reject(...)`: reason required; releases the reservation.
- [ ] `reassign(...)`: only in PENDING_APPROVAL; checks the new approver; audits; notifies both approvers.
- [ ] `withdraw(...)`: only from RETURNED_FOR_CORRECTION and only with nothing disbursed; releases.

**Stage 3** (`finance/service.py`)
- [ ] `disburse(...)`:
  - `Idempotency-Key` header required; the same key again returns the **original** disbursement (200), not a duplicate;
  - amount = approved − already disbursed, which must be > 0;
  - `txn_ref` unique; date, notes;
  - `budget.disburse`, then status PENDING_EXPENSE_SUBMISSION; notify the requestor.
- [ ] `return_to_stage1(...)`:
  - from PENDING_DISBURSEMENT, the reservation is released;
  - from PENDING_EXPENSE_SUBMISSION, the disbursement row is kept and stays outstanding (D3).

**Endpoints**
- [ ] `POST /api/requests`, `PUT /api/requests/{id}` (resubmit), `GET /api/requests/{id}`
- [ ] `GET /api/requests`: filters `project_id`, `status` (several allowed), `requestor_id`, `approver_id`, `date_from`, `date_to`, `mine=action` (requests waiting on me); paginated; newest first.
- [ ] `POST /api/requests/{id}/approve | reject | reassign | withdraw | disburse | return`. Each body includes `version` (and `reason` where needed).
- [ ] `GET /api/requests/{id}/history`: the audit rows for the request and its items, plus disbursements.
- [ ] `GET /api/notifications`, `POST /api/notifications/{id}/read`

### 7.2 Tests
- [ ] `test_workflow_table.py`: for every status and every action, allowed pairs reach the right status and all others get 409 (generated from `TRANSITIONS`).
- [ ] `test_stage1_3_api.py`:
  - happy path 1 → 2 → 3;
  - reject → resubmit → approve;
  - disbursement returned → resubmit;
  - withdraw;
  - inactive project and non-member are rejected;
  - self-approval is impossible.
- [ ] `test_visibility.py`: requestor B gets 404 on requestor A's request; approver B gets 404 until reassigned; Finance sees everything.
- [ ] `test_concurrency.py` (threads, real database):
  - two parallel requests against 20,000 available (15,000 and 10,000): exactly one gets 201 and the other 422; `reserved == 15,000` (arch Figure 8);
  - two parallel disbursements: one succeeds and the other gets 409, with exactly one disbursement row;
  - approve and reassign racing: exactly one wins;
  - stale `version` gives 409.
- [ ] `test_return_after_payout.py` (D3): disburse, return to Stage 1, resubmit the same amount, approve. Auto-skip to Stage 4; still only one disbursement row; ledger totals consistent.

### 7.3 Manual check
In `/docs`: req.a raises 30,000 on PRJ-001; approver.a approves; finance disburses (sending the same request twice with the same `Idempotency-Key` gives one disbursement); project buckets show 0 reserved and 30,000 outstanding.

---

## 8. Phase 4 — Expenses, documents, liquidation (stages 4–6)

**Branch:** `feat/phase-4-expenses-liquidation` · **Time:** 50 min · **Architecture:** arch §7.3, §9, §11.4

### 8.1 Tasks

**Storage and documents**
- [ ] `core/storage.py`: a `Storage` protocol (`save(stream) -> key`, `open(key)`, `delete(key)`) and `LocalStorage(media_root)`. Keys are UUIDs; the original name is never used as a path.
- [ ] `POST /api/documents` (multipart, linked to a request and optionally an item):
  - caller must be the requestor and the request must be in a Stage 4 status;
  - the type is checked by **file content** with `filetype` (PDF, JPG, PNG only);
  - at most `MAX_UPLOAD_MB`;
  - SHA-256 stored.
- [ ] `GET /api/documents/{id}`: same visibility check as the parent request (404 otherwise); streamed with `Content-Disposition: attachment`.
- [ ] If the database transaction fails after a file is saved, the file is deleted.

**Stage 4: submit expenses** (`expenses/service.py`)
- [ ] `PUT /api/requests/{id}/expenses`: saves the editable items. Only items with approval `PENDING` or `REJECTED`, or items Finance has `RETURNED`, can change. Approved items are locked (D9).
- [ ] `POST /api/requests/{id}/expenses/submit`. Requires:
  - at least one item; every item has a document (D6); dates not in the future; amounts > 0;
  - total > requested → `overspend_justification` required (400 without it);
  - overage = `max(total − disbursed, 0)`, locked and reserved through `budget.adjust_overage` (422 if over budget);
  - rejected and returned items go back to approval `PENDING` and verification `PENDING`;
  - status PENDING_EXPENSE_APPROVAL; notify the approver.

**Stage 5: approver review**
- [ ] `POST /api/expense-items/{id}/review` with `{decision, reason?}`: assigned approver only, in PENDING_EXPENSE_APPROVAL; a reason is required to reject.
- [ ] `POST /api/requests/{id}/send-back`: every item reviewed and at least one rejected → EXPENSE_RETURNED; notify the requestor.
- [ ] `POST /api/requests/{id}/final-approve`: every item APPROVED (otherwise 422) → PENDING_VERIFICATION; notify Finance.

**Stage 6: Finance verification and liquidation** (`finance/service.py`)
- [ ] `POST /api/expense-items/{id}/verify` with `{decision, reason?}`: Finance, in PENDING_VERIFICATION.
- [ ] `POST /api/requests/{id}/finance-send-back`: any item RETURNED → EXPENSE_RETURNED. Returned items reset to approval PENDING, so they pass Stage 5 again (D9).
- [ ] `POST /api/requests/{id}/liquidate`:
  - every item VERIFIED (otherwise 422);
  - `balance_return_ref` required when verified < disbursed (D5);
  - lock project and request;
  - `budget.settle`;
  - insert the `liquidations` row (UNIQUE per request);
  - status LIQUIDATED; audit; notify the requestor.
- [ ] `GET /api/requests/{id}` also returns the money summary:
  - requested, approved, disbursed;
  - submitted expenses, approved expenses, verified expenses;
  - liquidated, balance returned, top-up.

### 8.2 Tests
- [ ] `test_full_flow.py`: all six stages, checking project buckets after every stage against arch §9.2.
- [ ] `test_expense_loops.py`:
  - a Stage 5 rejection returns to Stage 4; approved items stay locked; the corrected item is reviewed again;
  - a Stage 6 return goes back through Stage 5 before Finance sees it again.
- [ ] `test_overspend_underspend.py`:
  - justification required;
  - overage reserved, and 422 when the project cannot cover it;
  - top-up recorded;
  - underspend requires the balance-return reference.
- [ ] `test_liquidation_duplicate.py`: two parallel liquidations → one succeeds and the other gets 409; exactly one `liquidations` row.
- [ ] `test_documents.py`:
  - a `.pdf` that is really an `.exe` is rejected;
  - an oversize file is rejected;
  - a user who cannot see the request gets 404 on download;
  - upload is refused outside Stage 4.

### 8.3 Manual check
Continue the phase 3 request in `/docs`:
1. Upload two receipts and submit 32,000 with a justification.
2. approver.a rejects one item.
3. Resubmit, then approver.a approves everything.
4. finance verifies and liquidates.

PRJ-001 should show 32,000 liquidated and available = allocation − 32,000.

---

## 9. Phase 5 — Frontend

**Branch:** `feat/phase-5-frontend` · **Time:** 70 min · **Architecture:** arch §13

### 9.1 Tasks

**Foundations**
- [ ] `src/auth/`:
  - `AuthProvider` keeps the access token in memory and the refresh token in `localStorage`;
  - `useAuth()` exposes `user` and `can(permission)`;
  - `RequireAuth` route guard;
  - logout.
- [ ] `src/api/client.ts`:
  - Bearer header;
  - on 401, refresh once and retry;
  - `Idempotency-Key` (`crypto.randomUUID()`) on disburse and liquidate;
  - one error helper that shows `error.message` with Ant Design `message`, and on **409** shows "This request changed. Reload?".
- [ ] `src/api/*.ts`: typed functions per module (`requests.ts`, `projects.ts`, `users.ts`, `expenses.ts`).
- [ ] App layout: sidebar menu filtered by permissions, notification bell (unread count), user menu.
- [ ] Route-level code splitting with `React.lazy`, which also fixes the 500 kB chunk warning.

**Pages**
- [ ] **Login** and **Accept invite**.
- [ ] **Dashboard**: "Waiting on me" (`mine=action`), counts by status, recent notifications.
- [ ] **Requests**: table with filters for project, status, requestor, approver and date range; pagination; "New request" button for `request.create`.
- [ ] **New / edit request** form:
  - eligible projects only, with available budget shown;
  - approver picker;
  - amount, purpose, supporting notes;
  - client-side checks that mirror the backend.
- [ ] **Request detail**:
  - step tracker (six stages, current one highlighted);
  - money summary cards;
  - expense items table with document links;
  - history timeline (audit rows with reasons);
  - the **Action Panel**.
- [ ] **Action Panel** (`components/ActionPanel/`): one component per row of arch §13.2, chosen by `status × permission × object rule`:
  - Approve / Reject (reason modal);
  - Reassign;
  - Disburse (amount pre-filled with the shortfall, date, reference, notes);
  - Return;
  - Expense editor (items and uploads, live total, justification field that appears when total > requested);
  - item-by-item Review with Final approve / Send back;
  - item-by-item Verify with Liquidate (balance-return reference field when underspent).
- [ ] **Projects**:
  - table with a stacked budget bar (available / reserved / disbursed / liquidated);
  - create/edit modal;
  - activate toggle;
  - members picker;
  - ledger drawer.
- [ ] **Users** (Super Admin): table; invite modal showing the link to copy; role / active / finance-ops toggles.
- [ ] **Audit log** (Finance, Super Admin): filterable table.
- [ ] Loading skeletons, empty states, success toasts on every action.

### 9.2 Checks
- [ ] `npm run build` passes (strict TypeScript, no unused code).
- [ ] Manual run in the browser at **1536×701** and **1536×1024** viewports:
  - req.a raises a request; approver.a rejects it; req.a resubmits; approver.a approves;
  - finance disburses;
  - req.a submits expenses with uploads; approver.a rejects one item and then approves after the fix;
  - finance verifies and liquidates.
- [ ] Every role sees only its menu items and actions. A requestor typing another request's URL sees "Not found".

---

## 10. Phase 6 — Documentation, sample data, final run

**Branch:** `chore/phase-6-docs-demo` · **Time:** 30 min · **Architecture:** problem statement §9

### 10.1 Tasks
- [ ] Seed extension (`python -m app.seed --with-requests`): at least one request **in every status**, built through the real services (not raw inserts), so ledgers and audit trails are consistent.
- [ ] `README.md`:
  - what the app does;
  - setup with Docker (one command) and without Docker;
  - demo users table;
  - a 10-step demo script;
  - architecture summary with a link to the PDF;
  - **business rules** (D1–D12);
  - the edge-case list (arch §11);
  - how to run the tests;
  - known limitations and next steps (arch §14).
- [ ] Clean-machine run: `docker compose down -v && docker compose up --build`, then the demo script in the browser at `http://localhost:3000`.
- [ ] Final quality pass:
  - `ruff check`, `pytest`, `npm run build`;
  - no secrets committed;
  - `.env.example` files up to date;
  - architecture PDF still matches the code (update it if anything changed).
- [ ] Tag `v0.1.0` on `main` after the merge.

---

## 11. API checklist (implementation status)

| Endpoint | Phase | Permission / rule |
|---|:-:|---|
| `POST /api/auth/login`, `POST /api/auth/refresh`, `GET /api/me` | 1 | public / authenticated |
| `POST /api/users/invite`, `GET /api/users`, `PATCH /api/users/{id}` | 2 | `user.manage` |
| `POST /api/auth/accept-invite` | 2 | valid invitation token |
| `GET /api/users/approvers` | 2 | authenticated |
| `GET|POST /api/projects`, `PATCH /api/projects/{id}` | 2 | `project.manage` (GET filtered by role) |
| `PUT /api/projects/{id}/members`, `GET /api/projects/{id}/ledger` | 2 | `project.members` / `project.manage` |
| `GET|POST /api/requests`, `GET|PUT /api/requests/{id}` | 3 | visibility; `request.create` / `request.resubmit` |
| `POST /api/requests/{id}/approve | reject` | 3 | `request.approve` + assigned approver |
| `POST /api/requests/{id}/reassign` | 3 | `request.reassign`, PENDING_APPROVAL only |
| `POST /api/requests/{id}/withdraw` | 3 | `request.withdraw` + own request + nothing disbursed |
| `POST /api/requests/{id}/disburse | return` | 3 | `request.disburse` / `request.return` |
| `GET /api/requests/{id}/history`, `GET /api/notifications` | 3 | visibility / own |
| `POST /api/documents`, `GET /api/documents/{id}` | 4 | own request in Stage 4 / visibility |
| `PUT /api/requests/{id}/expenses`, `POST …/expenses/submit` | 4 | `expense.submit` + own request |
| `POST /api/expense-items/{id}/review`, `POST …/send-back | final-approve` | 4 | `expense.review` + assigned approver |
| `POST /api/expense-items/{id}/verify`, `POST …/finance-send-back | liquidate` | 4 | `expense.verify` / `request.liquidate` |
| `GET /api/audit` | 5 | `audit.view` |

---

## 12. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Docker not running on the demo machine | Cannot demo | README also covers running without Docker (local PostgreSQL on 5433 or 5432) |
| Concurrency tests flaky on a slow machine | False red build | Use a thread barrier to start both calls together; assert on final database state, not timing |
| Frontend overruns its 70 minutes | Incomplete UI | Backend is complete by then, so `/docs` demos the full flow; follow the cut order in §1 |
| Migration drift between models and database | Broken setup for reviewers | Review every autogenerated migration; run `downgrade base` + `upgrade head` in phase 1 and phase 6 |
| Money rounding errors | Wrong balances | `Decimal` everywhere, `q2()` on every computed value, no floats in tests |
| A rule from the problem statement missed | Lost marks | Before phase 6, walk the FR-01 to FR-24 checklist below |

---

## 13. Requirements traceability (FR → phase)

| FR | Requirement | Phase | Proof |
|---|---|:-:|---|
| FR-01 | Authentication and RBAC | 1 | `test_auth_api`, permission catalogue |
| FR-02 | Project management | 2 | `test_projects_api` |
| FR-03 | Project access control | 2, 3 | eligible-projects filter; create rejects non-members |
| FR-04 | Budget tracking | 2 | budget engine + ledger, `test_budget_unit` |
| FR-05 | Fund request creation | 3 | `test_stage1_3_api` |
| FR-06 | Budget validation with reservations | 3 | `test_concurrency` (parallel reservations) |
| FR-07 | Initial approval | 3 | `test_stage1_3_api` |
| FR-08 | Disbursement | 3 | `test_stage1_3_api`, duplicate test |
| FR-09 | Expense submission with proofs | 4 | `test_full_flow`, `test_documents` |
| FR-10 | Overspend justification | 4 | `test_overspend_underspend` |
| FR-11 | Item-level review | 4 | `test_expense_loops` |
| FR-12 | Final approval requires all items | 4 | `test_expense_loops` |
| FR-13 | Finance verification | 4 | `test_expense_loops` |
| FR-14 | Liquidation | 4 | `test_full_flow` |
| FR-15 | Balances without double counting | 2, 4 | ledger vs project totals asserted in `test_full_flow` |
| FR-16 | Rejection routing | 3, 4 | `test_workflow_table`, loop tests |
| FR-17 | Approver reassignment | 3 | `test_concurrency` (race), `test_visibility` |
| FR-18 | User management | 2 | `test_users_api` |
| FR-19 | Request visibility | 3 | `test_visibility` |
| FR-20 | Audit logging | 2–4 | history endpoint assertions in every flow test |
| FR-21 | Notifications | 3, 4 | notification rows asserted in flow tests; bell in UI |
| FR-22 | Search and filtering | 3, 5 | list filters + UI |
| FR-23 | Duplicate transaction prevention | 3, 4 | duplicate disbursement / liquidation tests |
| FR-24 | Validation and error handling | all | 400 / 409 / 422 tests; UI error helper |
