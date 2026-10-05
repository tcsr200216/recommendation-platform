# ADR-015: Durable recommendation impressions and feedback attribution

## Context / Problem

Interaction events alone cannot tell whether a user ignored a recommendation or never
saw it. Offline evaluation and future online experiments need the exact items served,
their order, the model that ranked them, and whether the result came from cache. Client
supplied attribution is not trustworthy unless it is checked against a recorded serve.

## Decision

Record every non-empty recommendation response as a transactional batch of impressions
behind an `ImpressionRepository` boundary. Each row stores a server-generated request
ID, user and item IDs, one-based rank, strategy, model version, cache/live source, and
UTC serving time. Return the request ID in `X-Recommendation-Request-ID`.

An interaction may carry that ID as `recommendation_request_id`. Before persisting the
event, the API verifies that the recorded request exposed the same item to the same
user. The attribution value participates in idempotency comparison and is persisted
with the interaction. Memory adapters preserve local operation; PostgreSQL provides
durability. Recommendation delivery fails with `503` if a non-empty result cannot be
logged, preserving the invariant that every delivered candidate has an impression.

## Why this approach

- The server, rather than the client, is authoritative for exposure and rank.
- Batch insertion ensures a response is logged completely or not at all.
- Model and source fields make later cache, ranker, and experiment analysis possible.
- The repository boundary keeps API orchestration independent of PostgreSQL.
- Exact request/user/item validation prevents cross-user or fabricated attribution.

## Alternatives considered

- Infer exposures from interaction events: rejected because missing feedback is
  indistinguishable from missing exposure.
- Trust a client-provided model or item list: rejected because clients can be stale or
  tampered with.
- Emit impressions only to logs or metrics: rejected because both are operational
  signals, not a queryable event ledger, and high-cardinality IDs do not belong in
  Prometheus labels.
- Best-effort asynchronous logging: useful at larger scale, but rejected here because
  it requires a durable queue and introduces loss/reconciliation semantics not present
  in this architecture.

## Tradeoffs / risks

- Synchronous impression writes add database latency and make impression storage part
  of serving availability.
- The table grows with items served, so production needs retention, partitioning, and
  consent/deletion policies before large-scale use.
- A request ID proves exposure, not that the user visually noticed the item.
- No experiment assignment is modeled yet; the schema records the model inputs needed
  to add one without inventing historical assignments.

## How it fits the architecture

Ranking remains pure and cacheable. The API enriches results, records the final ordered
list through `ImpressionRepository`, and only then returns it. Interaction ingestion
optionally validates the response ID through the same boundary before the existing
atomic interaction/idempotency write. PostgreSQL stores both ledgers; memory adapters
support tests and dependency-free local development. Readiness and bounded-cardinality
metrics cover the new dependency.

## Interview explanation

“A recommender cannot learn reliably from clicks unless it knows what was shown. I
added a server-owned impression ledger that records the final ranked list—including
cache source and model version—in one transaction. Feedback can reference the response
ID, but the API verifies the user and item against the ledger before accepting it. I
chose synchronous fail-closed logging to make the demo's exposure invariant explicit;
at higher throughput I would move the same contract behind an outbox or durable event
stream and add retention and privacy lifecycle controls.”
