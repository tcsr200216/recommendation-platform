# ADR-003: Versioned cache-aside recommendation results

## Context / Problem

The personalized algorithm currently rebuilds all profiles and computes neighbors on
every read. Global popularity and collaborative ranking can change when *any* user
records an interaction, not just the user requesting recommendations. The system needs
a locally runnable cache while enabling Redis for shared hosted deployments.

## Decision

Use a `RecommendationCache` port with in-memory TTL and Redis adapters selected by
`REDIS_URL`. Separate results by namespace, atomic generation, strategy, model version,
user ID hash, and requested limit. Cache empty result lists as valid hits. The Redis
adapter validates JSON and discards malformed entries.

Each interaction or catalog write atomically increments one namespace generation key.
Old result keys immediately become unreachable and expire under their existing TTL.
A lookup returns both its result and generation. After live ranking, Redis uses a Lua
compare-and-set to write only when that generation is still current; a request that
raced an invalidation therefore cannot publish a stale snapshot into the new generation.
The in-memory adapter applies the same generation contract. `/ready` checks the
configured cache. A transient Redis read or write error does not prevent live ranking,
but failure to invalidate after a durable write is reported explicitly.

## Why this approach

The cache port keeps ranking independent of Redis and preserves zero-setup development.
Cache-aside means storage remains the source of truth, and namespacing allows changing
ranking or cache payload versions without interpreting stale incompatible results.
Conservative global invalidation is needed because a new interaction may change other
users' neighbor similarities and the cold-start popularity ordering. A generation
increment makes that global invalidation O(1), while the compare-and-set closes the
stale-repopulation race without locking recommendation computation.

## Alternatives considered

- Per-user invalidation: cheap, but incorrectly retains recommendations affected by
  another user's events.
- Cache inside HTTP handlers without an adapter boundary: smaller initially, but
  couples the API to backend-specific serialization and failure semantics.
- Cache rankings indefinitely: fast, but violates freshness after interaction writes.
- Redis KEYS for invalidation: compact but can block a busy Redis server.
- Redis SCAN plus batched deletion: non-blocking per batch, but O(cached keys),
  non-atomic, and vulnerable to concurrent stale repopulation.
- Delete-on-invalidation with a distributed lock: stronger serialization, but adds
  lock expiry and availability failure modes to the read path.
- Precompute candidate lists on ingestion: attractive at scale, but adds worker jobs,
  durable versioning, and another consistency path before offline evaluation exists.

## Tradeoffs / Risks

Each cache lookup now reads the generation in addition to the result, and invalidated
keys occupy Redis memory until their bounded TTL expires. Generation counters are
namespace-local and must not be reset while old keys exist. The Lua script couples this
design to Redis semantics, although the port keeps that detail out of HTTP and ranking
code. A reader may still return a value concurrently with the write that invalidates it;
the guarantee is that stale computation cannot be stored or served by later-generation
requests. If Redis fails after recording an interaction, the API reports the partial
outcome rather than implying it is safe to retry. The in-memory adapter is process-local
and is not suitable for shared cross-instance caching.

## How it fits the architecture

Request → generation-aware cache lookup → if miss, repository snapshot → chosen ranker
→ compare-and-set with the lookup generation → response. Interaction or catalog write
→ durable write → atomic generation increment. Both cache adapters conform to the same
port and expose readiness, so the API and rankers do not depend on Redis-specific
commands.

## Interview explanation

"I added cache-aside around the ranking layer with local and Redis adapters. Because one
interaction can change other users' collaborative scores, per-user invalidation is
incorrect. I use an atomic namespace generation so global invalidation is constant time,
and include that generation in every result key. The request carries its lookup generation
through ranking, then a Redis Lua compare-and-set refuses the write if invalidation raced
the computation. That avoids both a namespace scan and stale repopulation, while TTLs
reclaim unreachable keys and model-versioned keys protect ranking compatibility."
