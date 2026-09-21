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
LLM_MODEL=openai/gpt-oss-120b
```

Provider URLs, model names and free-tier limits change: check the provider's docs. **Model names
go out of date fast** (a `llama-3.3-70b-versatile` example stopped working on Groq while this was
being written): list what your key can actually use with
`curl -H "Authorization: Bearer $LLM_API_KEY" $LLM_BASE_URL/models`.

Verified on 2026-09-21 with Groq and `openai/gpt-oss-120b` on a free account: real corrections in
Spanish, Catalan and English in 1.5 to 3 seconds, correct offsets, and a prompt-injection attempt
inside the text was ignored. Groq reported 1000 requests per day and 8000 tokens per minute for that
model, which is roughly 4 to 6 analyses per minute: plenty for a portfolio. If a free tier
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

## Deployment (Neon + Render, both free)

Order: **1)** Neon (database), **2)** Render (API), **3)** put the Render URL in the frontend,
**4)** verify. Free-tier limits change: check each provider's pricing page before starting.

**1. Neon.** Create a project named `marginalia` in a European region (Frankfurt). The free plan is
permanent, needs no card, and suspends after 5 minutes idle (the first query is a bit slower).
Copy the **pooled** connection string. It can be pasted as-is (`postgresql://...?sslmode=require`):
the app adds the `+psycopg` driver by itself.

**2. Render.** *New, Blueprint*, choose this repository (it reads `render.yaml`), and paste the Neon
string when asked for `DATABASE_URL`. Before confirming, check that the plan shows **Free / $0**;
if Render asks for a payment method, stop. The free web service sleeps after 15 minutes without
traffic and takes about a minute to wake up (the frontend shows a banner while it does), and Render
gives 750 free instance hours per month. The Python version is read from `.python-version`.
Render asks for five values when the Blueprint is created (see the table); type the API key in the
Render panel and never anywhere else.

| Variable               | Value in production                                            |
| ---------------------- | -------------------------------------------------------------- |
| `DATABASE_URL`         | Neon pooled string (secret)                                    |
| `SECRET_KEY`           | generated by Render (secret)                                   |
| `CORS_ORIGINS`         | `https://marginalia-english.vercel.app` (exactly the frontend) |
| `TRUSTED_PROXY_HOPS`   | `1` (one reverse proxy in front of the API)                    |
| `ENVIRONMENT`          | `production` (refuses the placeholder secret)                  |
| `LLM_PROVIDER`         | `openai_compatible` (or `fake` for the offline demo client)    |
| `LLM_BASE_URL`         | `https://api.groq.com/openai/v1` (asked for at creation)       |
| `LLM_API_KEY`          | your provider key (secret, asked for at creation)              |
| `LLM_MODEL`            | `openai/gpt-oss-120b` (asked for at creation)                  |

**3. Frontend.** Put the service URL (`https://<name>.onrender.com/api/v1`) in
`src/environments/environment.production.ts` of the frontend repository and merge: Vercel redeploys.

**4. Verify** (the API docs are at `/docs`):

1. `GET /api/v1/health` answers `{"status":"ok"}`.
2. Sign up and log in from the deployed frontend, and reload a deep route such as `/write`.
3. No CORS errors in the browser console.
4. **Rate limiting sees real visitors.** Getting `TRUSTED_PROXY_HOPS` wrong is silent: the limits
   simply stop working (or every visitor shares one bucket). Do not guess it, look at what the host
   sends. Add the variable `ENABLE_DIAGNOSTICS=true` in the Render *Environment* tab, then call
   `GET /api/v1/diagnostics/client-ip` from your computer and from your phone on mobile data, once
   with no extra header and once with `X-Forwarded-For: 1.2.3.4`. The answer shows the headers the
   API received and `rate_limit_key`, the address it would use:
   - the key must be **your real public address** (compare with what a "what is my IP" site says),
   - it must **differ** between computer and phone,
   - and it must **not change** when you add the forged `X-Forwarded-For`.
   If not, change `TRUSTED_PROXY_HOPS` (the entry counted from the END of `X-Forwarded-For`) and call
   again. When it is right, **delete `ENABLE_DIAGNOSTICS`**: the endpoint is only for this check.

**Keeping it awake (optional).** An external ping to `/api/v1/health` every few minutes (UptimeRobot,
cron-job.org) avoids the one-minute wake-up. One service running all month uses about 744 of the
750 free hours, so it fits, but confirm the current limits first.

## Environment variables

See [.env.example](.env.example) for the full list (values there are placeholders, never real secrets).

## Author

[@costanna](https://github.com/costanna)
