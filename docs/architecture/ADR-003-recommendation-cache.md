# ADR-003: Versioned cache-aside recommendation results

## Context / Problem

The personalized algorithm currently rebuilds all profiles and computes neighbors on
every read. Global popularity and collaborative ranking can change when *any* user
records an interaction, not just the user requesting recommendations. The system needs
a locally runnable cache while enabling Redis for shared hosted deployments.

## Decision

Add a `RecommendationCache` port with in-memory TTL and Redis adapters. Select the
adapter using `REDIS_URL`. Separate results by a versioned namespace, strategy, user ID
hash, and requested limit, and apply a configurable positive TTL. Cache empty result
lists as valid hits. The Redis adapter validates JSON and discards malformed entries.
On each successfully recorded interaction, invalidate the whole recommendation namespace;
the Redis implementation enumerates namespace keys via SCAN in batches rather than
using a blocking KEYS operation. `/ready` now checks the configured cache as well as
storage. A transient Redis read/write error does not prevent live ranking from storage,
but failure to invalidate after a successful interaction is reported explicitly.

## Why this approach

The cache port keeps ranking independent of Redis and preserves zero-setup development.
Cache-aside means storage remains the source of truth, and namespacing allows changing
ranking or cache payload versions without interpreting stale incompatible results.
Conservative global invalidation is needed because a new interaction may change other
users' neighbor similarities and the cold-start popularity ordering.

## Alternatives considered

- Per-user invalidation: cheap, but incorrectly retains recommendations affected by
  another user's events.
- Cache inside HTTP handlers without an adapter boundary: smaller initially, but
  couples the API to backend-specific serialization and failure semantics.
- Cache rankings indefinitely: fast, but violates freshness after interaction writes.
- Redis KEYS for invalidation: compact but can block a busy Redis server.
- Precompute candidate lists on ingestion: attractive at scale, but adds worker jobs,
  durable versioning, and another consistency path before offline evaluation exists.

## Tradeoffs / Risks

Namespace-wide invalidation is O(cached keys) and deliberately conservative; it will
be too expensive at higher interaction throughput. Redis SCAN is incremental but is
not atomic with concurrent cache writes, so a racing reader may repopulate a stale
result after invalidation. A production follow-up should use atomic generation tokens,
an event-driven invalidation stream, or versioned materialized snapshots for stronger
consistency. If Redis fails after recording an interaction, the API reports the partial
outcome rather than implying it is safe to retry. The in-memory adapter is process-local
and is not suitable for shared cross-instance caching.

## How it fits the architecture

Request → versioned cache lookup → if miss, repository snapshot → chosen ranker →
cache with TTL → response. Interaction ingestion → repository write → invalidate cached
rankings. Both cache adapters conform to the same port and expose readiness, so the
API and rankers do not depend on Redis-specific commands.

## Interview explanation

"I added cache-aside around the ranking layer, with a local TTL adapter and a Redis
adapter behind the same interface. I keyed results by algorithm version, strategy,
user, and limit, and used explicit TTLs plus payload validation. Because collaborative
ranking can change for unrelated users when one interaction is recorded, invalidating
only the writer's key would be wrong, so this phase uses conservative namespace-wide
invalidation with Redis SCAN. I can explain its race and cost limitations, and would
move to atomic generations or precomputed versioned snapshots at larger scale."
