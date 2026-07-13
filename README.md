# Carpentry Workshop Production-Control System

Django + PostgreSQL production-control backend for a small carpentry workshop.
Portfolio project. The full specification and planning docs live alongside this
repo in `carpentry-production-control-planning/`.

## Requirements

- Docker + Docker Compose (recommended), or
- Python 3.12+ and a local PostgreSQL 16 instance

## Getting started (Docker Compose)

1. `cp .env.example .env` and set a real `SECRET_KEY`.
2. `docker compose up --build`
3. App: http://localhost:8000/ — health check: http://localhost:8000/health/

The `db` service provides PostgreSQL; the `web` service runs migrations on start.

## Local development (without Docker)

1. Create and activate a virtualenv:
   - Windows: `python -m venv .venv && source .venv/Scripts/activate`
   - Unix: `python -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements-dev.txt`
3. `cp .env.example .env`, point `DB_HOST` at your PostgreSQL, and set `SECRET_KEY`.
4. `python manage.py migrate`
5. `python manage.py runserver`

## Quality checks

- Lint: `ruff check .`
- Tests: `pytest` (run against PostgreSQL — never SQLite, per D-029)

CI (GitHub Actions) runs `ruff check` and `pytest` against a PostgreSQL service
on every push.
