# ADR-007: Include model versions in recommendation cache keys

Status: Accepted

## Context

Recommendation results depend on the ranking implementation as well as the user,
strategy, and requested limit. The cache namespace can be changed manually during
a deployment, but a ranking-code change can otherwise reuse results produced by
the previous algorithm until their TTL expires or an interaction invalidates them.
Each ranker already exposes a `model_version` for offline evaluation reports.

## Decision

Include the selected ranker's `model_version` in every in-memory and Redis cache
key. The API chooses the ranker type before lookup, uses its version for both the
read and write, and then instantiates the same type on a miss. Accept only short,
nonempty versions made of letters, digits, dots, dashes, and underscores.

Keep the configured namespace as a broader compatibility boundary for cache
payload or system-wide changes. Keep interaction invalidation scoped to the full
namespace so it removes entries for every model version and ranking strategy.
Old unversioned keys become unreachable immediately and expire under their TTL.

## Consequences

A deployment that bumps `user-cosine-v1` to `user-cosine-v2` recomputes results
instead of serving v1 scores. Popularity and personalized results can evolve on
separate schedules. The model version is readable in Redis keys, while user IDs
remain SHA-256 tokens.

Every ranking-semantics change now requires an intentional version bump. Forgetting
to bump the constant can still serve stale results, so regression tests verify that
different versions do not share entries and that the API ignores an older model's
cached result. Multiple versions may coexist until TTL expiry, which temporarily
uses additional cache memory.

## Alternatives considered

- Bump only `CACHE_NAMESPACE`: effective when coordinated perfectly, but manual and
  unnecessarily invalidates unrelated strategies.
- Store the version only inside the payload: detects a mismatch after reading and
  deserializing the wrong entry, and lets versions overwrite one another.
- Flush Redis during deployment: operationally disruptive and unsafe when Redis is
  shared with other workloads.

## Interview explanation

"I tied cache identity to the ranking algorithm version. A model deployment cannot
silently reuse results from older ranking logic, while the global namespace remains
available for payload-level migrations. Tests prove both key separation and the API
behavior when a stale prior-version entry exists."
