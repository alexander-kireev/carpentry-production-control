# Claude Code Project Rules — Carpentry Workshop Production-Control System

## Project Identity

This is a Django/PostgreSQL production-control system for small carpentry workshops. It is a portfolio backend project.

One instance hosts multiple fully isolated workshops (D-126): each admin self-registers and owns exactly one workshop, and every workshop-owned query/create/view must be scoped to the requesting user's workshop — never `Workshop.objects.first()` or a global count as a state check. The only global rows are the seeded system sentinels (`workshop=NULL`). See the planning repo's `docs/13_claude/conventions.md`, "Tenancy / workshop scoping".

The system manages workshop orders, jobs, operation routes, material reservation, scheduling, blockers, analytics, and backup. It is not a generic ERP, a marketplace, a booking platform, or a customer-facing product. (QC/rework and Demo Mode are post-MVP — see the planning repo's post_mvp_backlog.md.)

## Technical Stack

Backend: Django, PostgreSQL.
Frontend: Django templates, HTMX, Bootstrap, Chart.js.
Testing: pytest, pytest-django, factory_boy.
Linting: Ruff.
Infrastructure: Docker, Docker Compose, GitHub Actions CI.
Backup: Django management commands and lightweight cron-style runner.

Do not introduce React, Vue, Svelte, client-side routing, Celery, Redis, SQLite, separate task queues, WebSockets, server-sent events, or any infrastructure not listed above.

## Permanent Exclusions

Do not implement:

- automatic schedule optimisation or auto-rescheduling;
- separate branching rework jobs;
- dedicated QC software role;
- auto-calculate or Order-integrated procurement workflows (limited manual purchase orders with simulated arrival ARE in MVP scope — see Stage 11 development_strategy.md in the planning repo);
- customer-facing portal, booking flow, or payment flow;
- backup management console, backup browser, or arbitrary backup file downloads;
- cloud backup, point-in-time recovery, WAL archiving, or event replay restore;
- advanced BI, custom report builder, or scheduled reports;
- Demo Mode auto-play;
- configurable permission system beyond the four approved account roles (admin, manager, operator, technician);
- visual route builder or configurable transition graphs;
- attachment-based messaging;
- financial downtime costing or employee productivity scoring.

## Operating Rule

Do not edit files before repeating understanding and receiving approval, unless the prompt explicitly says this is a Micro-ticket.

## Ticket Type Rule

Always identify the ticket type before implementation:

- Micro-ticket: tiny, low-risk change only.
- Normal ticket: standard feature/page/route/component work.
- Full protocol ticket: auth, permissions, database, migrations, deployment, background jobs, architecture or security-sensitive work.

Escalate the ticket if the work becomes riskier than the prompt suggests.

## Scope Rule

Only modify files required for the approved ticket.

Do not silently broaden scope. If related issues are found, report them under Scope / Design Notes.

## Reuse Rule

Before creating new helpers, schemas, services, models, components or utilities, check existing abstractions listed in the ticket context.

Do not create duplicate abstractions unless the approved implementation plan explains why the existing one is unsuitable.

## Neighbouring File Rule

If another nearby file is needed to understand an existing pattern or avoid duplication, ask before reading it unless the ticket already authorises it.

State:

- which file is needed,
- why it is relevant,
- what risk inspection avoids.

Do not edit extra files unless the approved plan includes them.

## Git Rule

Before implementation, check branch and git status.

After implementation, report:

- files changed,
- git status,
- tests/checks run,
- known uncommitted changes,
- unresolved issues.

Commit only when instructed.

## Safety Rule

Do not change auth, permissions, database schema, migrations, deployment config, secrets or environment handling unless the ticket explicitly authorises it.

Never commit `.env`, secrets, API keys, local logs, virtual environments, generated cache files or local database dumps.

## Testing Rule

Always run tests against PostgreSQL, never SQLite. Concurrency, locking, and reservation correctness tests are invalid against SQLite.

## Completion Rule

Normal and Full protocol tickets require a completion report with:

- Ticket Type,
- Git State,
- files changed,
- tests/checks run,
- acceptance criteria status,
- Scope / Design Notes,
- Risks / Follow-up,
- Engineering Learning Notes with Evidence Anchors.

Micro-tickets require at minimum:

- scope,
- files changed,
- test/manual check,
- git status.
