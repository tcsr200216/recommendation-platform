# ADR-001: Introduce an interaction repository boundary

## Context / Problem

The first API implementation stored interactions directly in a module-level Python list.
That was sufficient to prove the recommendation flow, but it coupled the HTTP layer to a
specific persistence mechanism. The platform also needs durable storage for production
deployments without making local development depend on a running database.

## Decision

Keep the `InteractionRepository` protocol as the persistence port and provide two
adapters: `InMemoryInteractionRepository` for local/test use and
`SqlInteractionRepository` for PostgreSQL deployments. The SQL adapter uses SQLAlchemy
Core and stores the interaction type as its stable string value. Database connectivity is
configured through `DATABASE_URL`; leaving it unset preserves the zero-infrastructure
local fallback.

## Why this approach

The repository boundary keeps persistence concerns separate from request handling and
recommendation logic. SQLAlchemy provides a small, explicit database adapter while keeping
the ranking code independent of SQL. The in-memory adapter preserves a fast local path and
also makes repository-contract behavior easy to test.

## Alternatives considered

- Keep only a module-level list. This is simple but non-durable and unsuitable for
  multi-instance deployment.
- Couple endpoint handlers directly to SQLAlchemy. This reduces files but mixes transport
  and persistence concerns and makes ranking tests more infrastructure-dependent.
- Require PostgreSQL for every environment. This is closer to production but makes the
  basic project harder to run and demonstrate locally.
- Introduce a full ORM model layer. The current interaction record is small, so SQLAlchemy
  Core is sufficient and avoids unnecessary mapping complexity.

## Tradeoffs / Risks

The project now has two persistence adapters whose behavior must remain consistent.
`create_schema()` is intentionally lightweight bootstrap behavior; production schema
evolution will need migrations before the data model becomes more complex. The SQL
repository currently reads all interactions for the baseline ranker, which will need a
more selective query strategy as data volume grows.

## How it fits the architecture

The HTTP layer depends on the repository port. Recommendation algorithms consume
interaction snapshots and remain independent of storage. Local development can use the
in-memory adapter, while a PostgreSQL-backed adapter provides the durable path for hosted
environments.

## Interview explanation

"I separated interaction persistence behind a repository port, then added a SQLAlchemy
adapter for PostgreSQL while keeping an in-memory adapter for local development and tests.
That let the API and ranking algorithm stay storage-agnostic. I deliberately used
SQLAlchemy Core because the model is still simple; the tradeoff is that schema creation is
only bootstrap-level today, so I would add migrations as the schema evolves."
