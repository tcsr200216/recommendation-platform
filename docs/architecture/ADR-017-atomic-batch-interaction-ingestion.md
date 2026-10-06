# ADR-017: Atomic, retry-safe interaction batch ingestion

## Context / Problem

Production interaction data commonly arrives as clickstream or ETL micro-batches.
Sending one HTTP request per event adds network overhead and advances the global cache
generation once per event. A naive batch loop can partially commit before a failure,
and retrying the whole window then duplicates the already-written prefix.

## Decision

Add `POST /interactions/batch` for 1–500 ordered, fully validated interaction events.
The client supplies a stable `batch_id`. The repository computes a SHA-256 fingerprint
over the normalized ordered payload, including event time and recommendation attribution.
In SQL deployments, it reserves the batch ID and inserts every event in one database
transaction. The memory adapter implements the same observable contract.

The first write returns HTTP `201` with `status: recorded`. An exact replay returns
HTTP `200` with `status: replayed` and appends nothing. Reusing the ID for different
content returns `409`. Attributed events are checked against durable impressions before
the transaction begins. Cache invalidation occurs once after a successful batch and is
attempted again for an exact replay to repair a prior post-commit invalidation failure.

## Why this approach

- One transaction gives the batch an all-or-nothing durability boundary.
- A durable batch marker makes transport retries safe across API replicas and restarts.
- Fingerprinting normalized content distinguishes a replay from accidental ID reuse.
- The 500-event bound caps request memory, validation work, and transaction duration.
- One generation advance per batch reduces Redis traffic without weakening freshness.
- Preserving order and duplicates respects the interaction log rather than inventing
  user-item deduplication semantics.

## Alternatives considered

- Loop over the existing single-event endpoint: rejected because it permits partial
  success and performs cache invalidation for every event.
- Use one idempotency key per event: still useful for independent events, but it does not
  identify whether an entire imported window completed.
- Put batch IDs in Redis: rejected because cache loss or expiry must not re-enable
  duplicate durable events.
- Accept unbounded batches: rejected because large transactions increase lock time,
  memory usage, and retry cost.
- Asynchronous queue ingestion: appropriate at higher throughput, but requires broker,
  consumer-offset, dead-letter, and replay semantics beyond this service's current scope.

## Tradeoffs / risks

- The whole batch is retried when one event is invalid; clients should split poison
  records before resubmission.
- Batch markers require a retention policy aligned with the maximum replay window and
  audit requirements.
- Impression attribution validation and the interaction transaction cross repository
  boundaries, so an impression can be deleted after validation in a future system with
  retention jobs. Current append-only impression storage avoids that race.
- Global cache invalidation is conservative; a future dependency index could invalidate
  only affected users and strategies.

## How it fits the architecture

The FastAPI layer owns request bounds and attribution validation. The existing
`InteractionRepository` owns the atomic batch/idempotency contract for both memory and
SQL adapters. Ranking continues to consume the same ordered interaction snapshot, and
the existing generation-based cache remains the freshness boundary. Bounded metrics
record batch outcomes without exposing batch, user, or item identifiers. The Compose
smoke path uses this endpoint before exercising personalized ranking and feedback.

## Interview explanation

“Real event pipelines send micro-batches, so I added a 500-event endpoint with an
all-or-nothing repository operation. PostgreSQL stores a canonical payload fingerprint
under a client batch ID in the same transaction as the events. Exact retries become
no-ops, changed replays conflict, and cache invalidation happens once per batch. I kept
single-event idempotency for online feedback and documented why a queue would be the next
step only when throughput justifies its delivery and replay complexity.”
