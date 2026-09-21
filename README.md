# Marginalia · Backend

[![CI](https://github.com/costanna/marginalia-backend/actions/workflows/ci.yml/badge.svg)](https://github.com/costanna/marginalia-backend/actions/workflows/ci.yml)

REST API for **Marginalia**, an AI-powered English corrector that annotates a learner's text like a
teacher's margin notes, estimates the CEFR level and builds personalised exercises.

> Status: **Phase 1 (base API and authentication)** done. Text analysis with the LLM arrives in Phase 2.

Frontend: [marginalia-frontend](https://github.com/costanna/marginalia-frontend)

## Stack

Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) · Alembic · PostgreSQL (Neon) ·
Argon2id + JWT · Anthropic API behind an `LLMClient` interface (Phase 2) · pytest · ruff · mypy

## Run locally

```bash
cp .env.example .env        # 1. configure (no real secrets needed for local dev)
docker compose up           # 2. PostgreSQL + API (migrations run on startup)
```

The API is then at <http://localhost:8000> and the interactive docs at <http://localhost:8000/docs>.

> If port 5432 is already used by a PostgreSQL installed on your machine, set `POSTGRES_PORT`
> (and the port in `DATABASE_URL`) in `.env`, e.g. `5433`.

### Working from a virtualenv

```bash
docker compose up -d db                  # database only
python -m venv .venv                     # then activate it:
                                         #   source .venv/bin/activate   |   .venv\Scripts\activate
pip install -r requirements-dev.txt
alembic upgrade head                     # apply migrations
```

## Endpoints (Phase 1)

| Method | Route                  | Auth | Description                                   |
| ------ | ---------------------- | ---- | --------------------------------------------- |
| GET    | `/api/v1/health`       | No   | Liveness probe (also wakes the free-tier host) |
| POST   | `/api/v1/auth/register`| No   | Create an account and return an access token  |
| POST   | `/api/v1/auth/login`   | No   | Return an access token                        |
| GET    | `/api/v1/me`           | Yes  | Current profile                               |
| PATCH  | `/api/v1/me`           | Yes  | Update name, UI language, theme, target level |
| DELETE | `/api/v1/me`           | Yes  | Delete the account                            |

Errors always look like `{"error": {"code": "...", "message": "...", "details": {}}}`; the frontend
translates `code`, the backend never translates messages.

## Quality checks

```bash
ruff check . && ruff format --check .
mypy
pytest
```

Tests run against a **real PostgreSQL** (a separate `<db>_test` database, created automatically and
built with the real Alembic migrations). The same checks run in CI on every pull request and on pushes
to `main`.

### Database migrations

```bash
alembic revision --autogenerate -m "describe the change"   # then review the generated file
alembic upgrade head
```

A test fails if a model changes without its migration.

## Technical decisions

- **Argon2id** for passwords and a **constant-work login**: unknown email and wrong password return the
  same error and take similar time, so the endpoint cannot be used to discover registered emails.
- **Email uniqueness is enforced by the database** (unique index), not by a "check then insert", which
  two concurrent requests could both pass.
- **JWT pinned to HS256** and re-validated against the database on every request, so a token for a
  deleted account stops working immediately.
- **Enums stored as `VARCHAR` + `CHECK`** instead of native PostgreSQL enums: much easier to migrate.

## Environment variables

See [.env.example](.env.example) for the full list (values there are placeholders, never real secrets).

## Author

[@costanna](https://github.com/costanna)
