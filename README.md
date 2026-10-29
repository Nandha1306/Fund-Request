# Project Fund Request & Liquidation Management System

Employees request funds against a project budget. The request is approved, Finance disburses the money, the employee
submits expenses with proofs, the approver reviews each item, and Finance verifies the items and liquidates. The whole
process runs as a six-stage workflow with role-based access, budget reservations and a full audit trail.

> **Status:** Phase 0, project skeleton. See [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) for the phase plan.

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
uvicorn app.main:app --reload   # http://localhost:8000/docs

# frontend (new terminal)
cd frontend
npm install
npm run dev                     # http://localhost:5173, proxies /api to :8000
```

## Tests and checks

```bash
cd backend && pytest && ruff check .
cd frontend && npm run build
```

## Branching

`main` always holds verified work. Each phase is built on its own branch (`feat/phase-N-...`) and merged with
`--no-ff` once its tests and manual check pass. The branch names are listed in the implementation plan.
