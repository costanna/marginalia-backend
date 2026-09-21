# Marginalia · Backend

[![CI](https://github.com/costanna/marginalia-backend/actions/workflows/ci.yml/badge.svg)](https://github.com/costanna/marginalia-backend/actions/workflows/ci.yml)

REST API for **Marginalia**, an AI-powered English corrector that annotates a learner's text like a
teacher's margin notes, estimates the CEFR level and builds personalised exercises.

> Status: **Phase 2 (LLM text analysis)** done. Exercises (Phase 6) and statistics (Phase 7) are next.

Frontend: [marginalia-frontend](https://github.com/costanna/marginalia-frontend)

## Stack

Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) · Alembic · PostgreSQL (Neon) ·
Argon2id + JWT · Pluggable LLM behind an `LLMClient` interface (free tiers supported) · pytest · ruff · mypy

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

## Endpoints

| Method | Route                     | Auth | Description                                        |
| ------ | ------------------------- | ---- | -------------------------------------------------- |
| GET    | `/api/v1/health`          | No   | Liveness probe (also wakes the free-tier host)     |
| POST   | `/api/v1/auth/register`   | No   | Create an account and return an access token       |
| POST   | `/api/v1/auth/login`      | No   | Return an access token                             |
| GET    | `/api/v1/me`              | Yes  | Current profile                                    |
| PATCH  | `/api/v1/me`              | Yes  | Update name, UI language, theme, target level      |
| DELETE | `/api/v1/me`              | Yes  | Delete the account and all its data                |
| POST   | `/api/v1/demo/analyze`    | No   | Try the corrector, nothing saved (limited per IP)  |
| POST   | `/api/v1/texts/analyze`   | Yes  | Analyse a text, save it, return the corrections    |
| GET    | `/api/v1/texts`           | Yes  | Paginated history (`?page=&page_size=`)            |
| GET    | `/api/v1/texts/{id}`      | Yes  | One text with its corrections                      |
| DELETE | `/api/v1/texts/{id}`      | Yes  | Delete a text                                      |

**Correction offsets** (`start`, `end`) are **Unicode code points** into `original_text`, end-exclusive
(Python string indices). JavaScript strings use UTF-16 units, so the frontend must convert them (an
emoji is one code point but two UTF-16 units).

Errors always look like `{"error": {"code": "...", "message": "...", "details": {}}}`; the frontend
translates `code`, the backend never translates messages.

## LLM provider: zero-cost by design

This is a portfolio project, so it is built to run **without spending money**. The AI provider is
swappable by configuration (`LLM_PROVIDER`), and none of the options needs a paid plan:

| `LLM_PROVIDER`      | Cost               | Use                                                               |
| ------------------- | ------------------ | ----------------------------------------------------------------- |
| `fake` (default)    | Free, offline      | Development and tests. Only recognises a few common mistakes.     |
| `openai_compatible` | Free tiers exist   | Real corrections through Groq, Gemini, OpenRouter... (see below). |
| `anthropic`         | Paid, **optional** | Anthropic API. Not needed for anything in this project.           |

To get real corrections for free, create an API key on a provider with a free tier and set, in `.env`
(never commit it):

```
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=<your free key>
LLM_MODEL=llama-3.3-70b-versatile
```

Provider URLs, model names and free-tier limits change: check the provider's docs. If a free tier
runs out (HTTP 429) the API retries with growing waits and then answers `503 llm_unavailable`, which
the frontend shows as a friendly message; nothing is charged to the user's daily allowance.

The model is asked for JSON; the answer is validated with Pydantic, retried once if unusable, and its
corrections are located in the text by the server, so a weaker free model cannot break the offsets.

## Quality checks

```bash
ruff check . && ruff format --check .
mypy
pytest --cov=app.services --cov-fail-under=80
```

Tests never call the real LLM and never depend on your `.env`. They run against a **real PostgreSQL** (a separate `<db>_test` database, created automatically and
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
- **Offsets are computed on the server.** The model only quotes the fragment; language models count
  characters badly. The server finds it on word boundaries, drops invented or overlapping corrections
  and builds the corrected text from the end to the start so positions never shift.
- **Daily quota is reserved atomically** (`INSERT .. ON CONFLICT DO UPDATE .. WHERE count < limit`)
  *before* calling the LLM and refunded if the analysis fails, so concurrent requests cannot exceed the
  limit and a failed analysis costs nothing.
- **A provider only transports; the service validates.** "Invalid answer, retry once" lives in one place
  and works the same for every provider (real or fake).
- **Prompt-injection defence:** the learner's text is wrapped in `<user_text>` tags and a literal
  closing tag inside it is neutralised.
- **Rate limiting** (slowapi) is per IP and kept in memory: fine for one instance; several would need a
  shared store. Behind a reverse proxy set `TRUSTED_PROXY_HOPS` (1 on Render) so visitors are told
  apart; the client-controlled part of `X-Forwarded-For` is never trusted.
- **Enums stored as `VARCHAR` + `CHECK`** instead of native PostgreSQL enums: much easier to migrate.

## Environment variables

See [.env.example](.env.example) for the full list (values there are placeholders, never real secrets).

## Author

[@costanna](https://github.com/costanna)
