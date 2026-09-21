# Marginalia · Backend

[![CI](https://github.com/costanna/marginalia-backend/actions/workflows/ci.yml/badge.svg)](https://github.com/costanna/marginalia-backend/actions/workflows/ci.yml)

REST API for **Marginalia**, an AI-powered English corrector that annotates a learner's text like a
teacher's margin notes, estimates the CEFR level and builds personalised exercises.

> Status: **Phase 0 (project setup)**. The API itself arrives in Phase 1.

Frontend: [marginalia-frontend](https://github.com/costanna/marginalia-frontend)

## Stack

Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) · Alembic · PostgreSQL (Neon) ·
Anthropic API behind an `LLMClient` interface · pytest · ruff · mypy

## Run locally

```bash
cp .env.example .env                       # 1. configure (no real secrets needed for local dev)
docker compose up -d db                    # 2. start PostgreSQL
python -m venv .venv                       # 3. create a virtualenv...
```

Then activate it (`source .venv/bin/activate`, or `.venv\Scripts\activate` on Windows) and run
`pip install -r requirements-dev.txt`.

## Quality checks

```bash
ruff check . && ruff format --check .
mypy
pytest
```

The same checks run in CI on every pull request and on pushes to `main`.

## Environment variables

See [.env.example](.env.example) for the full list (values there are placeholders, never real secrets).

## Author

[@costanna](https://github.com/costanna)
