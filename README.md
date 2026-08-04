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

Development and setup instructions will be added as implementation begins.
