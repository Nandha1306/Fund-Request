# Phase 2: Users, projects, budget engine and audit

| | |
|---|---|
| **Branch** | `feat/phase-2-users-projects-budget`, created from `main` **after Phase 1 is merged** |
| **Estimate** | about 40 minutes |
| **Depends on** | Phase 1: schema, `CurrentUser`, `require()`, `permissions_for()`, `Money` / `Allocation`, seed data |
| **Architecture references** | Architecture PDF §3 (roles), §9 (budget model), §10.2 (history), §11.3 #14–17 (edge cases), §12 (security) |
| **Requirements covered** | FR-02, FR-03 (project side), FR-04, FR-15 (engine), FR-18, FR-20 (writer), FR-22 (users / projects lists) |

**Goal:** everything the request workflow (Phase 3) stands on:
- **audit and notification writers**;
- **user invitations and management**;
- **projects with members and live budget figures**;
- the **budget engine**: the only code allowed to change project money, with an append-only ledger.

**Out of scope:** fund requests, disbursements, expenses, liquidation endpoints (Phases 3 and 4). The budget engine's
request-related functions are built and tested here, against request rows created directly in tests.

---

## 1. Design choices in this phase

| Choice | Why |
|---|---|
| **The service layer owns the transaction.** Each use case ends with one `db.commit()`. The audit writer and budget engine only add rows to the caller's session and never commit. | One use case is one atomic unit: status, money, ledger and audit succeed or fail together (arch §11.2 #8). |
| **Budget engine = pure functions + database functions.** The arithmetic is pure and unit-tested without a database. The database functions lock, apply and write the ledger. | The money rules can be proven exactly, and the database code stays thin. |
| **Every money move writes one ledger row**, and project totals can be rebuilt from the ledger (`reconcile()`). | The ledger is the source of truth; the totals on `projects` are a fast cache that tests check against it. |
| **`lock_project()` uses `SELECT … FOR UPDATE`.** Lock order is always project, then request. | Prevents two transactions committing the same money; a fixed order prevents deadlocks (arch §11.2 #1, #7). |
| **Invitation token** = `secrets.token_urlsafe(32)`. Only its SHA-256 is stored, it expires after 72 hours, works once, and the link is returned **once**. | A leaked database can't be used to accept invitations; no mail server needed for the MVP (D8). |
| **Re-inviting a pending email** expires the old invitation and issues a new one. | Lost links are recoverable, and only one link is ever valid. |
| **Invisible project → 404**, visible but not allowed → 403. | Doesn't reveal that the record exists (D12). |
| **Project codes** are entered by Finance (`^[A-Z0-9-]{3,20}$`, stored upper-case) and are unique (409 on duplicate). | The PDF asks for a "unique project code or identifier"; human codes read well in the demo. |
| **Members must be active Requestors.** Removing a member keeps their existing requests visible. | Edge case #16: access to new requests stops; history doesn't. |
| **The seed keeps its direct inserts** for now. | It already writes consistent ledger and audit rows; Phase 6 rebuilds it on top of the services. |

---

## 2. New settings (`app/core/config.py`)

| Setting | Default | Used for |
|---|---|---|
| `frontend_base_url` | `http://localhost:5173` | Building the invitation link `{frontend_base_url}/accept-invite?token=…` |
| `invite_ttl_hours` | `72` | Invitation expiry |

Add both to `backend/.env.example`, and set `FRONTEND_BASE_URL: http://localhost:3000` in `docker-compose.yml`.

---

## 3. Steps (one commit each)

### Step 1: pagination, audit and notification writers, audit API
**Commit:** `feat(audit): audit/notification writers, pagination helper and audit log API`

- [ ] `app/core/pagination.py`
  - `Page[T]` (Pydantic generic): `items`, `total`, `page`, `size`
  - `PageParams` dependency: `page >= 1` (default 1), `1 <= size <= 100` (default 20)
  - `paginate(db, stmt, params) -> tuple[list, int]`: runs a `count()` over the filtered statement plus `offset` / `limit`
- [ ] `app/modules/audit/service.py` (never commits)
  - `record(db, *, entity, entity_id, action, actor_id, from_status=None, to_status=None, reason=None, snapshot=None) -> AuditLog`
  - `notify(db, *, user_ids, type, message, request_id=None)`: one `notifications` row per user (duplicates removed)
  - `snapshot_of(obj, fields)`: JSON-safe dict (Decimal and dates as strings), used for before / after snapshots
- [ ] `app/modules/audit/router.py`
  - `GET /api/audit` (`audit.view`): filters `entity`, `entity_id`, `actor_id`, `action`, `date_from`, `date_to`; newest first; paginated
- [ ] Register the router in `main.py`.

### Step 2: budget engine
**Commit:** `feat(finance): budget engine with ledger and reconciliation`

`app/modules/finance/budget.py` is **the only code that changes `projects.reserved / outstanding / liquidated / allocated`**.
All values pass through `q2()`.

**Pure functions (no database)**

| Function | Returns |
|---|---|
| `available(allocated, reserved, outstanding, liquidated)` | `allocated − reserved − outstanding − liquidated` |
| `committed(reserved, outstanding, liquidated)` | `reserved + outstanding + liquidated` |
| `reservation_for_submit(amount, already_disbursed)` | `max(amount − already_disbursed, 0)` (covers D3, resubmission after payout) |
| `overage_for(expense_total, disbursed)` | `max(expense_total − disbursed, 0)` |
| `settlement(verified, disbursed)` | `Settlement(liquidated=verified, balance_return=max(disbursed − verified, 0), top_up=max(verified − disbursed, 0))` |

**Database functions.** Each takes `actor_id`, runs inside the caller's transaction, updates the project row
(and the request row where relevant), and writes **one ledger row per movement**.

| Function | Effect on the project | Effect on the request | Ledger | Errors |
|---|---|---|---|---|
| `lock_project(db, project_id)` | `SELECT … FOR UPDATE` | — | — | 404 if missing |
| `reserve(db, project, request, amount)` | `reserved += amount` | `reserved_amount += amount` | RESERVE | 422 "Amount exceeds the project's available budget" with `details={"available": …}` |
| `release(db, project, request)` | `reserved −= request.reserved_amount` | `reserved_amount = 0` | RELEASE (skipped when 0) | — |
| `disburse(db, project, request, amount)` | `reserved −= amount`, `outstanding += amount` | `reserved_amount −= amount` | DISBURSE | 409 if `amount > request.reserved_amount` |
| `adjust_overage(db, project, request, new_overage)` | `reserved ± delta` | `overage_reserved = new_overage` | RESERVE or RELEASE, note `"overage"` | 422 if the increase exceeds available |
| `settle(db, project, request, verified, disbursed)` | `outstanding −= disbursed`, `reserved −= overage_reserved`, `liquidated += verified` | `overage_reserved = 0` | LIQUIDATE, plus RETURN or TOPUP | 409 if `top_up ≠ overage_reserved` (defensive invariant) |
| `change_allocation(db, project, new_allocation)` | `allocated = new` | — | ALLOCATE (signed change; skipped when 0) | 422 if `new < committed` (edge case #14) |

**Reconciliation**: `reconcile(db, project_id) -> Buckets` rebuilds the totals from the ledger:

| Ledger row | allocated | reserved | outstanding | liquidated |
|---|---|---|---|---|
| ALLOCATE a | + a | | | |
| RESERVE a | | + a | | |
| RELEASE a | | − a | | |
| DISBURSE a | | − a | + a | |
| TOPUP a | | − a | + a | |
| LIQUIDATE a | | | − a | + a |
| RETURN a | | | − a | |

The tests assert `reconcile(...) == project totals` after every scenario. This is how FR-15 ("no double counting") is proven.

### Step 3: projects and members
**Commit:** `feat(projects): project CRUD, members, role-filtered visibility and ledger view`

- [ ] `app/modules/projects/schemas.py`
  - `ProjectCreate`: code, name (1–255), description (optional), allocated (`Allocation`), status (default ACTIVE)
  - `ProjectUpdate`: name, description, status, allocated (all optional)
  - `ProjectOut`: id, code, name, description, status, allocated, reserved, outstanding, liquidated, **available**, created_at, updated_at
  - `MemberOut`, `MembersUpdate {user_ids: list[int]}`, `LedgerEntryOut`
- [ ] `app/modules/projects/policies.py`
  - `visible_projects(user) -> stmt filter`:
    - `project.manage` → all projects;
    - Requestor → **eligible only** (ACTIVE and member);
    - Approver → projects of requests where they are the approver (empty until Phase 3).
  - `get_visible_project_or_404(db, user, project_id)`
- [ ] `app/modules/projects/service.py` (each function ends with `db.commit()`)
  - `create_project`: duplicate code → 409. Inserts the project, then `change_allocation(0 → allocated)` writes the ALLOCATE row, then audit `created`.
  - `update_project`:
    - lock the project;
    - allocation change goes through `budget.change_allocation`;
    - status change is audited as `activated` / `deactivated`, other fields as `updated`, each with a before / after snapshot.
    - Deactivating does not touch existing requests (edge case #15).
  - `set_members`:
    - every id must be an **active REQUESTOR** (422 listing the invalid ids);
    - computes added / removed;
    - audit `members_changed` with both lists.
- [ ] `app/modules/projects/router.py`

  | Endpoint | Permission / rule | Notes |
  |---|---|---|
  | `GET /api/projects` | authenticated; filtered by `visible_projects` | filters `status`, `q` (code or name); paginated; sorted by code |
  | `GET /api/projects/{id}` | visible, otherwise 404 | |
  | `POST /api/projects` | `project.manage` | 201 |
  | `PATCH /api/projects/{id}` | `project.manage` | |
  | `GET /api/projects/{id}/members` | `project.members` | |
  | `PUT /api/projects/{id}/members` | `project.members` | replaces the member list |
  | `GET /api/projects/{id}/ledger` | `project.manage` | newest first; paginated |

### Step 4: users and invitations
**Commit:** `feat(accounts): invitations, user management and user directories`

- [ ] `app/modules/accounts/schemas.py` (extend)
  - `InviteCreate {email, role}`; `InviteOut {id, email, role, expires_at, invite_link}` (the link appears only in the create response)
  - `AcceptInvite {token, full_name, password}`: password 8–72 bytes
  - `UserAdminOut` (adds is_active, can_finance_ops, created_at); `UserUpdate {role?, is_active?, can_finance_ops?}`; `UserBrief {id, full_name, email}`
- [ ] `app/modules/accounts/service.py`
  - `invite_user`:
    - an **active user** with that email exists → 409;
    - a pending invitation exists → it is expired, and a new one is issued;
    - token generated, only its hash stored;
    - audit `invited` (no token in the snapshot).
  - `accept_invite`:
    - hash lookup;
    - unknown, expired or already used → 400 "Invitation is invalid or has expired" (one message for all, so nothing is revealed);
    - creates the user with the invited role;
    - sets `accepted_at`;
    - audit `invitation_accepted`;
    - returns tokens (user is logged in immediately).
  - `update_user`. Rules:

    | Rule | Result |
    |---|---|
    | Super Admin changes their own role or deactivates themselves | 422 |
    | `can_finance_ops = true` on any role other than SUPER_ADMIN | 422 (the flag only means something for Super Admin) |
    | Role change while the user is requestor or approver on a **non-terminal** request (anything except LIQUIDATED, WITHDRAWN) | 422 listing the request refs |
    | Deactivating an approver with requests in **PENDING_EXPENSE_APPROVAL** | 422 listing the refs (edge case #17: only that approver may review them) |
    | Deactivating an approver with requests in PENDING_APPROVAL | Allowed; response includes `needs_reassignment: [refs]` so Finance can reassign in Phase 3 |
    | Anything changed | audit `updated` with a before / after snapshot |

- [ ] `app/modules/accounts/router.py` (extend)

  | Endpoint | Permission / rule | Notes |
  |---|---|---|
  | `POST /api/users/invite` | `user.manage` | 201; returns the link once |
  | `GET /api/users/invitations` | `user.manage` | pending invitations (no link) |
  | `POST /api/auth/accept-invite` | public; rate limit 10/min per IP | 201 `TokenResponse` |
  | `GET /api/users` | `user.manage` | filters `role`, `is_active`, `q`; paginated |
  | `PATCH /api/users/{id}` | `user.manage` | rules above |
  | `GET /api/users/approvers` | authenticated | active approvers, **excluding the caller**; `UserBrief[]`, for the request form |
  | `GET /api/users/requestors` | `project.members` | active requestors; `UserBrief[]`, for the members picker |

### Step 5: docs
**Commit:** `docs: phase 2 status`
- [ ] Tick the boxes here and in `IMPLEMENTATION_PLAN.md` §6.
- [ ] README: list the new endpoints under "API overview", and document the invite flow for the demo.

---

## 4. Tests

| File | What it proves |
|---|---|
| `tests/test_budget_unit.py` (no database) | Every pure function. Worked example from arch §9.2 step by step: 100,000 allocation; 30,000 reserve, disburse, 32,000 expenses (2,000 overage), settle with top-up, and the underspend branch (verified 27,500 → balance return 2,500, available 72,500). `reservation_for_submit` when the amount is already disbursed → 0. |
| `tests/test_budget_db.py` (`db_session`) | Each database function updates the project, the request and the ledger exactly as in the Step 2 table. `reserve` above available → 422 and nothing changed. `disburse` more than reserved → 409. `adjust_overage` up and down. `change_allocation` below committed → 422. **`reconcile()` equals the project totals after every scenario.** |
| `tests/test_audit.py` | `record` / `notify` write rows without committing. `GET /api/audit` returns 403 for a requestor and filters by entity. |
| `tests/test_projects_api.py` | Finance creates a project → ALLOCATE ledger row and audit row. Duplicate code → 409. Requestor or approver creating → 403. Requestor sees only active projects they belong to; inactive and non-member projects → 404 on detail. Reducing allocation below commitments → 422. Deactivation audited. `PUT members` rejects non-requestor and inactive ids (422) and audits added / removed. Ledger endpoint → 403 for a requestor. |
| `tests/test_users_api.py` | Invite → accept → `/api/me` works with the returned token. Token reuse, expired token, unknown token → the same 400. Re-invite makes the old link invalid. Invite for an existing active email → 409. Non-admin invite → 403. Self-deactivation → 422. `can_finance_ops` on Finance → 422. Deactivating an approver with a PENDING_EXPENSE_APPROVAL request → 422. Deactivating with only PENDING_APPROVAL → 200 plus `needs_reassignment`. Approvers list excludes the caller and inactive users. |

Request rows needed by these tests (for example, an approver with a Stage 5 request) are inserted directly with a small
`make_request(...)` fixture in `conftest.py`. The real request services arrive in Phase 3.

---

## 5. Manual check

1. `git switch main && git pull`, then `git switch -c feat/phase-2-users-projects-budget`; `alembic upgrade head`; `python -m app.seed`.
2. `uvicorn app.main:app --reload`, then open http://localhost:8000/docs.
3. As `admin@demo.local`:
   1. `POST /api/users/invite` with `{"email": "req.c@demo.local", "role": "REQUESTOR"}`. Copy the `token` from `invite_link`.
   2. Without auth, `POST /api/auth/accept-invite` with that token, a name and a password → 201 with tokens.
   3. Accept again with the same token → 400.
4. As `finance@demo.local`:
   1. `POST /api/projects` `{code: "PRJ-004", name: "Water Pilot", allocated: 50000}`.
   2. `PUT /api/projects/{id}/members` with req.c's id.
   3. `GET /api/projects/{id}/ledger` shows one ALLOCATE row of 50,000.
   4. `PATCH` allocation to 40,000 → 200 and a second ALLOCATE row of −10,000.
5. As `req.c`: `GET /api/projects` lists only PRJ-004. `GET /api/projects/1` → 404.
6. As `finance`: `GET /api/audit?entity=projects` shows created, updated and members_changed rows with snapshots.

---

## 6. Definition of done

- [ ] All endpoints in Steps 1, 3 and 4 respond as specified, including 403 vs 404.
- [ ] Budget engine: every function is covered by tests, and `reconcile()` matches project totals in every scenario.
- [ ] Invitations: single use, expiring, hash-only storage, link shown once.
- [ ] `pytest` passes and `ruff check .` is clean in `backend/`.
- [ ] `alembic upgrade head` still clean. No new migration is expected; if a model changes, add a reviewed migration.
- [ ] Manual check in §5 done; then `--no-ff` merge into `main` and push.
