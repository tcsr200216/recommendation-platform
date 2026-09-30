# ADR-001: Introduce an interaction repository boundary

## Context / Problem

The first API implementation stored interactions directly in a module-level Python list.
That was sufficient to prove the recommendation flow, but it coupled the HTTP layer to a
specific persistence mechanism. Replacing that list with PostgreSQL later would otherwise
require changing endpoint logic and recommendation code at the same time.

## Decision

Introduce an `InteractionRepository` protocol and an
`InMemoryInteractionRepository` implementation. The API depends on the repository
contract rather than directly managing a list.

## Why this approach

The repository boundary keeps persistence concerns separate from request handling and
recommendation logic. The in-memory implementation remains simple for the MVP while the
same interface can later be implemented by PostgreSQL.

## Alternatives considered

- Keep a module-level list until PostgreSQL is added. This is simpler initially but
  increases coupling and makes the storage migration riskier.
- Add SQLAlchemy immediately. That would provide persistence now, but it adds database
  setup before the API and ranking behavior are fully established.
- Use a generic service locator or dependency-injection framework. That is unnecessary
  complexity at the current project size.

## Tradeoffs / Risks

The abstraction adds one additional module and interface before multiple persistence
implementations exist. The in-memory implementation is process-local, non-durable, and
not suitable for horizontal scaling.

## How it fits the architecture

The HTTP layer records and reads interactions through the repository port. Recommendation
algorithms consume interaction snapshots and remain independent of how those interactions
are stored. A PostgreSQL adapter can later replace the in-memory adapter without changing
the public API contract.

## Interview explanation

"I started with in-memory storage to validate the recommendation flow, then introduced a
small repository boundary before adding PostgreSQL. That keeps persistence out of the API
and ranking code. The tradeoff is an extra abstraction early, but it makes the later
database migration isolated and lets the same recommendation logic run against in-memory
data in tests and PostgreSQL in production."
