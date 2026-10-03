# ADR-009: Idempotent interaction ingestion

## Context / Problem

Clients retry requests after timeouts and connection failures. The interaction API
previously appended every request, so a retry could create a duplicate event. Even
though the current personalized ranker keeps only the strongest user-item signal,
duplicates still corrupt raw event history and can bias other ranking or evaluation
strategies later.

## Decision

Accept an optional `Idempotency-Key` header on interaction writes. The repository
atomically stores the key and its normalized interaction payload with the event.
The first write returns `201 recorded`; an exact replay returns `200 replayed`
without another event. Reusing a key for a different payload returns `409`.

PostgreSQL and SQLite enforce uniqueness through a separate
`interaction_idempotency` table. This adds idempotency to existing deployments
without altering the existing interaction table. The in-memory adapter implements
the same contract for local runs and tests. Requests without a key remain append-only.

Cache invalidation is attempted for both a new write and a replay. That lets a retry
repair the case where the database commit succeeded but the original request failed
during cache invalidation.

## Why this approach

- A client-generated key identifies one logical event across transport retries.
- The key reservation and interaction insert share one SQL transaction.
- Stored payload fields distinguish a legitimate replay from accidental key reuse.
- A separate table avoids a destructive bootstrap migration of existing events.
- The repository boundary keeps HTTP handlers independent of database mechanics.

## Alternatives considered

- **Deduplicate by user, item, and type:** incorrectly collapses legitimate repeated
  views, clicks, or purchases.
- **Put the key directly on the interaction row:** simpler for a new schema, but
  requires altering an existing table and defining behavior for historical rows.
- **Cache idempotency keys in Redis:** fast, but expiry or cache loss could admit a
  duplicate even though the durable event remains.
- **Always return 201 for replays:** hides whether the current request created data.

## Tradeoffs / risks

Idempotency records add storage and currently have no retention policy. Production
operations should choose a retention window based on maximum client retry duration
and audit requirements. Clients must generate stable, sufficiently unique keys and
must not reuse them for different payloads. A replay performs cache invalidation
again, trading a small extra cache operation for recovery from a partial side-effect
failure.

## How it fits the architecture

The API validates the header and translates repository conflicts to HTTP 409. Both
repository adapters decide whether a write is new or replayed. Ranking models remain
unchanged and consume the same interaction snapshot. Prometheus metrics report
recorded, replayed, and conflict outcomes without using the key as a label.

## Interview explanation

“I made interaction writes retry-safe using a client idempotency key. The SQL adapter
reserves that key and appends the event in one transaction; exact retries are no-ops,
but the same key with a different payload is a conflict. I intentionally stored keys
durably instead of Redis because losing an optimization cache must not re-enable
duplicate writes. Retries also repeat cache invalidation, repairing a commit-success,
cache-failure scenario.”
