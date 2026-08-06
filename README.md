# Carpentry Production Control

A workshop management system for coordinating custom orders, production work,
people, stations, materials, and shop-floor activity.

## Overview

Carpentry Production Control gives a custom production workshop one governed
operating place for the work between accepting a customer order and controlling
the shop-floor activity needed to complete it.

It connects information that otherwise fragments easily:

- customer commitments;
- production routes and scheduled work;
- operator availability and station capacity;
- material requirements, reservations, stock, and purchasing;
- maintenance interruptions and production blockers;
- requests, concerns, assigned work, and workshop communication;
- operating history and management review.

The system does not flatten these responsibilities into one generic task list.
Orders, production work, material movements, maintenance, reports, issues, and
assigned work retain their own rules and sources of truth.

Managers can see what requires attention, operators can focus on what they are
authorised to do next, and the workshop retains a reliable record of decisions,
changes, blockers, progress, and material use.

## Users

The application is designed around three workshop responsibilities.

### Administrators

Administrators establish and govern the workshop. They manage workshop setup,
users, protected operating configuration, sensitive account changes, and the
highest-authority lifecycle actions.

### Managers

Managers control day-to-day workshop operation. They manage customers and
orders, plan production routes, schedule people and stations, coordinate
maintenance, oversee stock and purchasing, review concerns, and keep production
moving.

### Operators

Operators use a focused shop-floor workspace. They see their assigned work and
schedule, start and pause authorised operations, record progress, report
blockers and concerns, communicate around relevant work, and view the material
information needed for their tasks.

## Core workflow

The operating lifecycle begins with workshop setup and the establishment of its
permanent administrator and manager responsibilities.

The workshop then establishes its operating configuration: work types, units,
material categories, shifts, stations, materials, stock items, and selected
built-in presets.

Managers and administrators create customers, contacts, and orders. Confirming
an order accepts the customer commitment and creates the production work that
must then be planned.

Each accepted order line becomes a Build. Its route is planned as an ordered
sequence of Operations. Managers schedule the appropriate operators, stations,
and time blocks while the system identifies conflicts, warnings, and readiness
problems.

Execution remains operator-led. The assigned operator starts work, records
progress, pauses or resumes where permitted, reports blockers, and closes out
completed work. Managers may intervene through controlled, reasoned commands
without rewriting work that has already happened.

Material requirements are reserved when production starts. Actual material use
is recorded when started work is completed or cancelled. Physical counts,
write-offs, settlements, and purchase-order arrivals create new immutable stock
history rather than rewriting earlier movements.

Maintenance, staff requests, reports, issues, notifications, work assignments,
notes, and operating history run alongside production while retaining their own
authority and lifecycle rules.

## MVP capabilities

The MVP covers:

- governed workshop setup and user invitation;
- administrator, manager, and operator responsibility boundaries;
- workshop configuration and selected built-in presets;
- customer and contact management;
- order drafting, confirmation, progression, completion, delivery, and
  cancellation;
- linear production-route planning for each accepted order line;
- manual operator, station, and time-block scheduling;
- operator-led production execution and progress recording;
- blocker reporting and resolution;
- station availability and maintenance work;
- material requirements, reservations, settlement, stock history, physical
  counts, and write-offs;
- purchase-order drafting, sending, cancellation, and scheduled arrival;
- protected staff-change, clearance, and leave requests;
- human-created assigned work, reports, and management issues;
- event history, notifications, NotesBoards, and Pings;
- permission-filtered operational views and review queues.

## Design principles

### Human-controlled production

The application surfaces conflicts, warnings, readiness information, and work
requiring attention. It does not replace the practical production judgement of
the people running the workshop.

### Clear sources of truth

Orders, Builds, Operations, maintenance work, stock effects, requests, reports,
issues, and communication remain distinct domain concepts. Derived badges,
queues, warnings, and metrics do not become hidden workflow state.

### Workshop isolation

Each workshop is a private operating space. Customer, staff, production,
material, purchasing, communication, and historical information must remain
isolated from other workshops.

### Explicit authority

Visible buttons, notifications, work assignments, or links never grant
authority by themselves. Every command revalidates the active account, role,
workshop, source access, current state, version, and applicable business rules.

### Reliable changes and history

Important changes are atomic, version-aware, and retry-safe. Rejected, stale,
or conflicting actions do not leave partial work behind. Events, material
effects, progress records, and other operating history are retained rather than
silently rewritten.

## MVP boundaries

The MVP deliberately does not include accounting, payroll, CAD or drawing
tools, customer portals, automatic scheduling optimisation, supplier
procurement workflows, broad analytics, quality-control programmes,
multi-workshop membership, reusable production templates, generic commercial
imports, or production-deployment commitments.

## Technical direction

The selected implementation direction is:

- Python 3.14;
- Django 5.2 LTS modular monolith;
- PostgreSQL with psycopg 3;
- server-rendered Django templates;
- HTMX for bounded progressive enhancement;
- project-owned Graphite design tokens and components;
- Tabler icons;
- uv for dependency and environment management;
- pytest, pytest-django, and PostgreSQL-backed integration testing;
- factory_boy and explicit scenario builders;
- Ruff for linting and formatting;
- Docker Compose for local development and testing;
- GitHub Actions for engineering quality gates.

The implementation favours domain-focused modules, named command/use-case
functions, purpose-built queries, explicit transaction boundaries, and
database-backed correctness.

## Development

The local stack uses Docker Compose for both Django and PostgreSQL. Django's
development server and the example settings below are for local development
only; they are not a production deployment configuration.

From PowerShell in the application repository, create the ignored local
environment file, build the application image, and start both services:

```powershell
Copy-Item .env.example .env
docker compose build app
docker compose up
```

Do not commit `.env`. Replace its placeholder secret and password with local
values. The application is then available at:

- `http://127.0.0.1:8000/` for the rendered foundation page;
- `http://127.0.0.1:8000/health/` for process liveness only.

Run checks from a second PowerShell terminal:

```powershell
docker compose run --rm app python manage.py check
docker compose run --rm app python manage.py migrate --noinput
docker compose run --rm app pytest
docker compose run --rm --no-deps app ruff check .
docker compose run --rm --no-deps app ruff format --check .
```

The migration and test commands use PostgreSQL. There is no SQLite fallback.
The current foundation has no product, user, authentication, administration,
session, or domain migrations; the migration command currently has an empty
graph and creates no product or default-contrib schema.

Stop the stack without deleting its database volume:

```powershell
docker compose down
```

### Troubleshooting

- If `.env` or a required setting is missing, copy `.env.example` again and
  supply local values. Missing settings fail explicitly by variable name.
- Correct malformed `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, or `DATABASE_PORT`
  values. The application does not replace them with permissive defaults.
- If port 8000 is occupied, stop the process using it before starting this
  fixed loopback-only service.
- If PostgreSQL is unhealthy, inspect `docker compose ps` and
  `docker compose logs db`.
- Changing PostgreSQL initialization credentials in `.env` does not rewrite an
  existing named volume. Diagnose the mismatch rather than deleting data.
- `docker compose down` preserves the named PostgreSQL volume. Deleting the
  volume is destructive and is not a routine repair step.
- After source or dependency-image drift, rerun `docker compose build app`.
  Do not install project packages directly on the host as a substitute.
# Carpentry Production Control

Public identity entry points are `/register` and `/login`; `/logout` accepts
CSRF-protected POST requests only. Successful registration and Login derive the
next route from current account state. Until the next onboarding ticket, an
unattached administrator is redirected to `/onboarding/workshop` without a
temporary Workshop page.

Registration requires three deployment values:
`ADMIN_REGISTRATION_ACTIVATION_CODE`,
`ADMIN_REGISTRATION_IP_HMAC_KEY`, and a positive
`ADMIN_REGISTRATION_IP_HMAC_KEY_VERSION`. The code and HMAC key must be
independent secrets and must not be committed.

Migration, race and manual QA checks use uniquely named disposable PostgreSQL
databases. The ordinary development database must not be migrated without
separate approval.
An authenticated administrator resumes Workshop setup from current database state:
`/onboarding/workshop` creates the Workshop and `/onboarding/manager` creates its
pending permanent-manager account, generation-one invitation and delivery evidence.
The source transaction commits before the generation-bound delivery worker claims
the email once and performs external I/O outside database locks. Delivery failure
does not undo the invitation. `/onboarding` shows the administrator the safe
pending/sent/failed state; it deliberately has no Resend or replacement controls
yet. Active operators wait at `/onboarding/holding`, and operational identities
reach the data-free `/dashboard` handoff.

The default `memory` invitation adapter is non-networked and intended for CI,
local and integration work. The explicit `failing` adapter exercises
recoverable delivery failure. `INVITATION_DELIVERY_MODE=live` opts in to the
fixed Resend SMTP boundary: `smtp.resend.com:587`, username `resend`, sender
`workshop@alder-and-green.co.uk`, verified STARTTLS and a strict 10-second
timeout. The API key is environment-only. Live non-production delivery also
requires the exact recipient in `INVITATION_RECIPIENT_ALLOWLIST`; production
requires a public credential-free HTTPS `INVITATION_PUBLIC_ORIGIN`. There is no
fallback or same-generation retry. A `sent` state means Resend accepted the
message, not that inbox delivery is confirmed. Provider configuration must keep
Enforced TLS enabled and click/open tracking disabled.

No adapter logs or persists the invitation token, link, message body or SMTP
credentials. PostgreSQL verification must use uniquely named disposable
databases; the ordinary development database is not a migration test target.
