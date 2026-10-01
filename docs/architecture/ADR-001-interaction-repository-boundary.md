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
Core and stores the interaction type as its stable string value.

At application startup, a small repository factory selects the adapter from
`DATABASE_URL`. When the variable is absent, the service uses the zero-setup in-memory
adapter. When it is configured, the service initializes the SQL adapter and creates the
current bootstrap schema. Both adapters expose a readiness contract; the API's `/ready`
endpoint checks that contract while `/health` remains a process-only liveness probe.

## Why this approach

The repository boundary keeps persistence concerns separate from request handling and
recommendation logic. SQLAlchemy provides a small, explicit database adapter while keeping
the ranking code independent of SQL. Environment-driven adapter selection gives local
development a fast path while hosted environments can opt into durable PostgreSQL without
changing endpoint code.

Separating liveness from readiness also makes container orchestration safer: a running
process can remain live while being removed from traffic if its database dependency is
temporarily unavailable.

## Alternatives considered

- Keep only a module-level list. This is simple but non-durable and unsuitable for
  multi-instance deployment.
- Couple endpoint handlers directly to SQLAlchemy. This reduces files but mixes transport
  and persistence concerns and makes ranking tests more infrastructure-dependent.
- Require PostgreSQL for every environment. This is closer to production but makes the
  basic project harder to run and demonstrate locally.
- Introduce a full ORM model layer. The current interaction record is small, so SQLAlchemy
  Core is sufficient and avoids unnecessary mapping complexity.
- Make `/health` depend on PostgreSQL. That would conflate process liveness with
  dependency readiness and could cause unnecessary container restarts during a transient
  database outage.

## Tradeoffs / Risks

The project has two persistence adapters whose behavior must remain consistent.
`create_schema()` is intentionally lightweight bootstrap behavior; production schema
evolution will need migrations before the data model becomes more complex. The SQL
repository currently reads all interactions for the baseline ranker, which will need a
more selective query strategy as data volume grows.

The configured SQL path currently fails fast if schema initialization cannot connect to
the database. That is deliberate for the MVP so a misconfigured deployment does not
silently fall back to ephemeral memory.

## How it fits the architecture

The HTTP layer depends on the repository port. Recommendation algorithms consume
interaction snapshots and remain independent of storage. Local development can use the
in-memory adapter, while a PostgreSQL-backed adapter provides the durable path for hosted
environments. `/health` answers whether the API process is alive; `/ready` answers
whether its selected persistence dependency can serve traffic.

## Interview explanation

"I put interaction persistence behind a repository port, with an in-memory adapter for
zero-setup development and a SQLAlchemy adapter for PostgreSQL. The running app chooses
the adapter from DATABASE_URL, so the API and ranking code do not change between local and
hosted environments. I also separated liveness from readiness: /health only proves the
process is up, while /ready verifies the active persistence backend. The tradeoff is
maintaining two adapters, but it keeps infrastructure concerns isolated and makes the
PostgreSQL migration and deployment behavior easy to test."
