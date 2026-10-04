# ADR-012: Event time and temporal offline evaluation

## Context / Problem

Interactions had no event time, so offline evaluation selected held-out items by
lexicographic ID. That split was reproducible but did not model the real prediction
question: recommend what a user engages with next using only earlier behavior. It also
made it impossible to distinguish late-arriving events from ingestion order.

## Decision

Add nullable, timezone-aware `occurred_at` values to the interaction domain, HTTP
contract, idempotency record, and PostgreSQL persistence. Normalize API and fixture
timestamps to UTC. Event time is part of the idempotent payload, so reusing a key with
a different timestamp is a conflict.

Replace the lexicographic evaluator split with deterministic latest-item holdout. A
user is evaluated only when they have at least two distinct items and every event has
an event time. All events for the latest item are held out together; item ID breaks an
exact timestamp tie. Missing-time users remain in training but are explicitly counted
as skipped. Include normalized timestamps in the dataset fingerprint and bump the
report and split versions.

Ranking semantics do not change in this decision. The rankers continue to use
interaction type strengths; event time currently improves evaluation integrity and
creates a clean boundary for later recency-aware models.

## Why this approach

Client-supplied event time represents user behavior more accurately than database
insertion order, especially with mobile retries and delayed pipelines. UTC
normalization makes equivalent offsets canonical. Holding out the latest item is
closer to production recommendation than choosing an arbitrary item while remaining
small, deterministic, and interview-explainable.

## Alternatives considered

- **Use database insertion time:** always available, but measures arrival order rather
  than when behavior occurred.
- **Keep lexicographic holdout:** reproducible, but temporally unrealistic.
- **Invent timestamps for legacy events:** increases coverage by fabricating order and
  can produce misleading metrics.
- **Immediately add time-decay ranking:** valuable later, but would mix data-contract,
  evaluation, and model changes in one hard-to-interpret result.

## Tradeoffs / risks

Legacy events without timestamps cannot be scored by temporal evaluation. Client
clocks may be wrong, so production ingestion should eventually add validation against
a separate server receipt time. The current additive startup DDL is safe for nullable
columns but should be replaced by managed migrations before more complex schema
evolution. Offline scores remain synthetic demonstrations, not production-lift claims.

## How it fits the architecture

The API validates and normalizes event time, repositories preserve it, idempotency
compares the complete logical event, and the evaluator consumes the same domain model.
Ranking and cache boundaries are unchanged, so this slice improves data lineage and
evaluation without invalidating deployed recommendation payloads.

## Interview explanation

"I replaced an arbitrary lexicographic holdout with a temporal next-item evaluation.
Event timestamps are timezone-required, normalized to UTC, persisted atomically with
idempotency, and included in dataset versioning. I deliberately excluded missing-time
users from the metric denominator instead of inventing chronology, and kept ranking
unchanged so any metric movement is attributable to the evaluation design."
