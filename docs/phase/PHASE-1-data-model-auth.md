# Phase 1: Data model and authentication

| | |
|---|---|
| **Branch** | `feat/phase-1-data-model-auth` |
| **Estimate** | about 35 minutes |
| **Depends on** | Phase 0 skeleton (`main`, commit `077a3a2`) |
| **Prerequisite** | Docker Desktop running, so that `docker compose up -d db` starts PostgreSQL on `localhost:5433` |
| **Architecture references** | Architecture PDF §3 (roles), §10 (data model), §12 (security) |

**Goal:** the full database schema (all 12 tables, created by one migration), demo users and projects, and JWT
login with permission checks. No business endpoints yet; those start in Phase 2.

---

## 1. Design choices in this phase

| Choice | Why |
|---|---|
| Integer primary keys, plus readable codes (`FR-2026-0001`, `PRJ-001`) | Easy to read in the demo and in logs. Guessing IDs gains nothing, because every read checks visibility and returns 404 if the user can't see the record. |
| Status values stored as text with a CHECK constraint, not native PostgreSQL enums | Adding a status later is a one-line migration; native enum types are awkward to change. |
| Money as `NUMERIC(14,2)` and Python `Decimal`, never float | Exact amounts, as required by the architecture document. |
| Timestamps with time zone, set by the database | One clock, no time-zone mistakes. |
| Every foreign key blocks deletion of the record it points to | History can't be deleted; records are deactivated instead. |
| Swagger "Authorize" takes a pasted Bearer token | Login takes plain JSON, and testing in `/docs` stays one click. |
| Tests run against a real PostgreSQL test database, each test rolled back afterwards | Constraint and locking tests only mean something on real PostgreSQL. |

---

## 2. Steps (one commit each)

### Step 1: core helpers
**Commit:** `feat(core): enums, money helpers, password hashing and JWT`

- [ ] `app/core/enums.py`

  | Enum | Values |
  |---|---|
  | `Role` | `REQUESTOR`, `APPROVER`, `FINANCE`, `SUPER_ADMIN` |
  | `ProjectStatus` | `ACTIVE`, `INACTIVE` |
  | `RequestStatus` | `PENDING_APPROVAL`, `RETURNED_FOR_CORRECTION`, `PENDING_DISBURSEMENT`, `PENDING_EXPENSE_SUBMISSION`, `PENDING_EXPENSE_APPROVAL`, `EXPENSE_RETURNED`, `PENDING_VERIFICATION`, `LIQUIDATED`, `WITHDRAWN` |
  | `ApprovalStatus` | `PENDING`, `APPROVED`, `REJECTED` |
  | `VerificationStatus` | `PENDING`, `VERIFIED`, `RETURNED` |
  | `LedgerEntryType` | `ALLOCATE`, `RESERVE`, `RELEASE`, `DISBURSE`, `LIQUIDATE`, `RETURN`, `TOPUP` |

- [ ] `app/core/money.py`
  - `Money` input type: greater than 0, at most 2 decimal places, at most 14 digits
  - `q2()`: round half-up to 2 decimals
  - `ZERO`, `MAX_AMOUNT = 99,999,999.99`
- [ ] `app/core/security.py`
  - password hashing and checking with bcrypt
  - access token valid 30 minutes, refresh token valid 7 days; each carries the user ID (`sub`), the token type and the expiry
  - token decoding that rejects an expired token, or a token of the wrong type

### Step 2: models for all 12 tables
**Commit:** `feat(models): full schema with integrity constraints`

| Module | Table | Key columns | Constraints and indexes |
|---|---|---|---|
| accounts | `users` | email (stored lower-case), full_name, password_hash, role, is_active, can_finance_ops, created_at, updated_at | email unique |
| accounts | `invitations` | email, role, token_hash, invited_by, expires_at, accepted_at | token_hash unique |
| projects | `projects` | code, name, description, allocated, reserved, outstanding, liquidated, status, created_by, created_at, updated_at | code unique; `reserved + outstanding + liquidated <= allocated`; each bucket `>= 0` |
| projects | `project_members` | project_id, user_id, added_by, added_at | one row per (project, user) |
| fund_requests | `fund_requests` | ref_no, project, requestor, approver, amount_requested, amount_approved, reserved_amount, overage_reserved, purpose, supporting_info, overspend_justification, status, version, created_at, updated_at | ref_no unique; `amount_requested > 0`; `requestor_id <> approver_id`; indexes on (status, project_id), requestor_id, approver_id, created_at |
| finance | `disbursements` | request, amount, txn_ref, disbursed_on, notes, idempotency_key, created_by, created_at | `amount > 0`; txn_ref unique; idempotency_key unique |
| finance | `liquidations` | request, verified_total, disbursed_total, balance_returned, balance_return_ref, top_up_amount, completed_by, completed_at | one liquidation per request (request_id unique) |
| finance | `budget_ledger` | project, request, entry_type, amount, actor, note, created_at | rows are only ever added; for budget changes (ALLOCATE) the amount is the signed change |
| expenses | `expense_items` | request, description, amount, expense_date, category, comment, approval status / reason / by / at, verification status / reason / by / at | `amount > 0` |
| expenses | `documents` | request, expense_item, storage_key, original_name, mime_type, size_bytes, sha256, uploaded_by, created_at | storage_key unique |
| audit | `audit_log` | entity, entity_id, action, from_status, to_status, reason, actor (empty for system actions), snapshot (JSON), created_at | index on (entity, entity_id) |
| audit | `notifications` | user, request, type, message, read_at, created_at | index on (user_id, read_at) |

- [ ] Every foreign key uses `ondelete="RESTRICT"`.
- [ ] `app/modules/__init__.py` imports every module's models, so Alembic sees all the tables.

### Step 3: migration and test database
**Commit:** `feat(db): initial migration and PostgreSQL test fixtures`

- [ ] `alembic revision --autogenerate -m "initial schema"`, then **read the migration by hand**: every CHECK and
  UNIQUE constraint and every index from Step 2 must be present.
- [ ] `tests/conftest.py`:
  - creates the `fundreq_test` database if it doesn't exist;
  - builds the schema by running the real migration (`alembic upgrade head`), so the migration itself is tested;
  - wraps each test in a transaction that is rolled back at the end.

### Step 4: login and permissions
**Commit:** `feat(auth): login, refresh, /me and permission dependencies`

- [ ] `app/modules/accounts/permissions.py`: which permissions each role has.

  | Permission | Requestor | Approver | Finance | Super Admin |
  |---|:-:|:-:|:-:|:-:|
  | `request.create` / `request.resubmit` / `request.withdraw` | ✓ | | | |
  | `expense.submit` | ✓ | | | |
  | `request.approve` / `expense.review` | | ✓ | | |
  | `project.manage` / `project.members` | | | ✓ | ✓ |
  | `request.reassign` / `request.view_all` / `audit.view` | | | ✓ | ✓ |
  | `request.disburse` / `request.return` / `expense.verify` / `request.liquidate` | | | ✓ | only if `can_finance_ops` |
  | `user.manage` | | | | ✓ |

- [ ] `app/modules/accounts/deps.py`
  - `CurrentUser` dependency: decodes the token, reloads the user from the database, returns 401 if the user is inactive
  - `require("permission.name")` dependency: returns 403 when the permission is missing
- [ ] Endpoints

  | Endpoint | Input | Output |
  |---|---|---|
  | `POST /api/auth/login` | email, password | access and refresh tokens, plus the user and their permissions |
  | `POST /api/auth/refresh` | refresh token | a new access token |
  | `GET /api/me` | Bearer token | id, name, email, role, permissions |

- [ ] Login is limited to 5 attempts per minute per IP address (`slowapi`).

### Step 5: seed data
**Commit:** `feat(seed): demo users and projects`

- [ ] `python -m app.seed` can be run repeatedly without creating duplicates.
- [ ] Users (password `Demo@1234` for all, local demo only)

  | Email | Role | Notes |
  |---|---|---|
  | `admin@demo.local` | Super Admin | `can_finance_ops = false` |
  | `finance@demo.local` | Finance | |
  | `approver.a@demo.local` | Approver | |
  | `approver.b@demo.local` | Approver | used to demo reassignment |
  | `req.a@demo.local` | Requestor | member of PRJ-001 and PRJ-002 |
  | `req.b@demo.local` | Requestor | member of PRJ-001 only |

- [ ] Projects

  | Code | Name | Allocation | Status |
  |---|---|---|---|
  | PRJ-001 | Rural Health Camp | 500,000.00 | Active |
  | PRJ-002 | School Library Upgrade | 200,000.00 | Active |
  | PRJ-003 | Legacy Pilot | 100,000.00 | Inactive |

- [ ] A ledger row (ALLOCATE) records each project's allocation, plus audit rows for each project created.

### Step 6: docs
**Commit:** `docs: phase 1 status`

- [ ] Tick the Phase 1 boxes in [IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md) and in this file.
- [ ] Add the seed step to the README.

---

## 3. Tests

| File | What it proves |
|---|---|
| `tests/test_security.py` | Hash and check round trip; wrong password fails; expired token rejected; a refresh token can't be used as an access token |
| `tests/test_auth_api.py` | Login works; wrong password gives 401; a deactivated user gives 401 on login and on `/me` with an old token; `/me` lists the right permissions for each role, including Super Admin with and without `can_finance_ops` |
| `tests/test_schema.py` | The database itself rejects self-approval, a negative amount, a second liquidation for the same request, and a project whose commitments exceed its budget |

---

## 4. Manual check

1. Start the database: `docker compose up -d db`.
2. In `backend/`, apply the schema and load the demo data: `alembic upgrade head`, then `python -m app.seed`.
3. Start the API: `uvicorn app.main:app --reload`, then open http://localhost:8000/docs.
4. Log in as `req.a@demo.local` / `Demo@1234`. Paste the access token into **Authorize**. Call `/api/me`: it should
   show the Requestor role and its permissions.
5. Do the same for `finance@demo.local` and `admin@demo.local`.
6. Run `alembic downgrade base` and then `alembic upgrade head`. Both should run cleanly.

---

## 5. Definition of done

- [ ] A fresh database migrates with `alembic upgrade head`, and `alembic downgrade base` reverses it.
- [ ] The seed runs twice without creating duplicates.
- [ ] Every demo user can log in, and `/api/me` shows the right permissions.
- [ ] `pytest` and `ruff check .` pass in `backend/`.
- [ ] The manual check in §4 has been done; then the branch is merged into `main` with `--no-ff`.
