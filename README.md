# Project Fund Request & Liquidation Management System

Employees request funds against a project budget. The request is approved, Finance disburses the money, the employee
submits expenses with proofs, the approver reviews each item, and Finance verifies the items and liquidates. The whole
process runs as a six-stage workflow with role-based access, budget reservations and a full audit trail.

> **Status:** Phase 2 of 6 done and connected to the web app: sign-in, invitations, users and access, projects with
> live budgets, members, ledger and the audit log. The request workflow (stages 1–6) follows in Phases 3–4, built
> backend and frontend together. See [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

## Web app

Run the API and `npm run dev`, then open http://localhost:5173 and sign in with a demo account (listed on the sign-in
page in development builds).

| Screen | Who sees it | What it does |
|---|---|---|
| Sign in · Accept invitation | everyone | Sign in; set name and password from an invitation link |
| Dashboard | everyone | Finance / Super Admin: budget position across active projects, most committed projects, recent activity, people. Requestors: their eligible projects. Approvers: projects of requests assigned to them |
| Projects | everyone (filtered) | Search and filter; usage bar per project; create a project (Finance, Super Admin) |
| Project detail | everyone who can see it | Five budget figures and bar; tabs for Members, Ledger, Activity and Details, each shown only to roles allowed to use it; edit, activate / deactivate |
| Users & access | Super Admin | People and pending invitations; invite (link shown once); change role, active, finance operations |
| Audit log | Finance, Super Admin | Filter by record, action and dates; expand a row for the before / after values |

Money is shown in rupees with Indian grouping (₹5,00,000.00) and is never converted through floating point. Budget
colours are the same on every screen: liquidated, disbursed, reserved, available.

## Documents

| Document | What it is |
|---|---|
| [Problem statement](docs/project_fund_request_liquidation_problem_statement_revised.pdf) | The requirements (FR-01 to FR-24) |
| [Architecture & MVP design](docs/Fund_Request_Liquidation_MVP_Architecture.pdf) | Stack choice, layers, workflow, data model, edge cases, scaling |
| [Implementation plan](docs/IMPLEMENTATION_PLAN.md) | Phases, branches, tasks, tests and exit criteria |

## Stack

FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL 16 · React + TypeScript (Vite) · Ant Design · TanStack Query · Docker Compose

## Repository layout

```
backend/    FastAPI app (app/core, app/modules/*), Alembic migrations, tests
frontend/   React single-page app, served by nginx in Docker
docs/       requirements, architecture and implementation plan
```

## Run with Docker

```bash
docker compose up --build
```

| Service | URL |
|---|---|
| Web app | http://localhost:3000 |
| API docs (Swagger) | http://localhost:8000/docs |
| PostgreSQL | localhost:5433 (user / password / db: `fundreq`) |

## Run locally (for development)

```bash
# database only
docker compose up -d db

# backend
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS / Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env          # macOS / Linux: cp .env.example .env
alembic upgrade head
python -m app.seed              # demo users and projects; safe to run again
uvicorn app.main:app --reload   # http://localhost:8000/docs

# frontend (new terminal)
cd frontend
npm install
npm run dev                     # http://localhost:5173, proxies /api to :8000
```

## Demo users

`python -m app.seed` creates these accounts (password `Demo@1234`, local only) and three projects:
PRJ-001 Rural Health Camp (500,000, active), PRJ-002 School Library Upgrade (200,000, active) and
PRJ-003 Legacy Pilot (100,000, inactive).

| Email | Role | Notes |
|---|---|---|
| `admin@demo.local` | Super Admin | invites users and manages access; no money actions unless `can_finance_ops` is granted |
| `finance@demo.local` | Finance | manages projects and budgets; disburses, verifies and liquidates |
| `approver.a@demo.local`, `approver.b@demo.local` | Approver | approver B is there to demo reassignment |
| `req.a@demo.local` | Requestor | member of PRJ-001 and PRJ-002 |
| `req.b@demo.local` | Requestor | member of PRJ-001 |

## API overview

All endpoints are under `/api`; Swagger at http://localhost:8000/docs (log in, then paste the `access_token` into
**Authorize**). Errors share one shape, `{"error": {"code", "message", "details"}}`: 400 invalid input, 401 not
logged in, 403 not allowed, 404 not visible or missing, 409 wrong state or duplicate, 422 business rule.

| Area | Endpoints | Who |
|---|---|---|
| Auth | `POST /auth/login` · `POST /auth/refresh` · `POST /auth/accept-invite` · `GET /me` | public / logged in |
| Users | `POST /users/invite` · `GET /users/invitations` · `GET /users` · `PATCH /users/{id}` | Super Admin |
| Directories | `GET /users/approvers` (request form) · `GET /users/requestors` (members picker) | logged in / Finance, Super Admin |
| Projects | `GET /projects` · `GET /projects/{id}` · `POST /projects` · `PATCH /projects/{id}` | list filtered by role; writes Finance, Super Admin |
| Members | `GET /projects/{id}/members` · `PUT /projects/{id}/members` | Finance, Super Admin |
| Ledger | `GET /projects/{id}/ledger` | Finance, Super Admin |
| Audit | `GET /audit?entity=&entity_id=&actor_id=&action=&date_from=&date_to=` | Finance, Super Admin |

Lists are paginated with `?page=` and `?size=` (at most 100) and return `{items, total, page, size}`.

### Inviting a user (demo)

1. As `admin@demo.local`: `POST /api/users/invite` with `{"email": "req.c@demo.local", "role": "REQUESTOR"}`.
   The response contains `invite_link` **once**; only a hash of its token is stored, and it expires after 72 hours.
2. Without logging in: `POST /api/auth/accept-invite` with the `token` from that link, a `full_name` and a
   `password` (8–72 bytes). The new user is logged in straight away.
3. The same link cannot be used again. Inviting the same email again invalidates the previous link.

## Tests and checks

```bash
cd backend && pytest && ruff check .
cd frontend && npm run build
```

## Branching

`main` always holds verified work. Each phase is built on its own branch (`feat/phase-N-...`) and merged with
`--no-ff` once its tests and manual check pass. The branch names are listed in the implementation plan.
