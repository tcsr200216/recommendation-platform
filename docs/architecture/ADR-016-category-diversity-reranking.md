# ADR-016: Deterministic category-diversity reranking

## Context / Problem

Pure relevance sorting can fill a short recommendation list with near-substitutes from
one category. That is valid ranking behavior but a weak discovery experience, especially
when the durable catalog already contains trustworthy category metadata. Diversity must
not silently change default behavior, poison relevance-only cache entries, or make the
served order differ from the durable impression ledger.

## Decision

Offer `diversity=category` as an opt-in post-ranking policy. The base personalized or
popularity model produces a relevance-sorted candidate pool of five times the requested
limit, capped at 500 candidates. A deterministic first pass takes the highest-ranked
item from each category. A second pass fills remaining positions in the original
relevance order. Missing categories form one unknown group, which preserves the original
order when catalog metadata is unavailable.

The policy suffixes the base model version with `category-coverage-v1-pool5x`. That
version participates in the existing cache key, metrics, response header, and impression
records. The default `diversity=none` remains byte-for-byte compatible with the existing
ranking path.

## Why this approach

- It separates candidate relevance from presentation constraints.
- It is deterministic, explainable, and straightforward to test in an interview.
- The bounded pool limits latency and memory while giving lower-ranked categories a
  realistic chance to enter the final list.
- Versioning prevents collisions with relevance-only cache entries and makes logged
  exposures reproducible.
- Recording after reranking means feedback attribution reflects what users actually saw.

## Alternatives considered

- Penalize same-category items inside both rankers: rejected because it couples a
  presentation policy to collaborative and popularity model internals.
- Maximal marginal relevance over item embeddings: potentially richer, but rejected
  because the service has no trustworthy item-embedding boundary yet.
- Hard category quotas: rejected because they require product-specific allocation rules
  and behave poorly when categories are sparse.
- Rerank only the already-limited response: rejected because no unseen category can enter
  when the base top K is homogeneous.

## Tradeoffs / risks

- The policy may move a lower-score item above a higher-score same-category item.
- A bounded pool can miss categories ranked below its depth; the header/model version
  names the 5x policy so this is not hidden.
- Category quality and granularity determine the usefulness of the result.
- The policy optimizes category coverage, not measured business or user outcomes; those
  claims require representative offline data and controlled online experiments.

## How it fits the architecture

The existing rankers remain pure candidate generators. The API selects the versioned
post-ranking policy, reads/writes through the existing generation-safe cache, enriches
the final list with catalog metadata, and then records final ranks through the impression
repository. Existing cache invalidation, attribution validation, and bounded-cardinality
observability require no new storage dependency.

## Interview explanation

“I kept relevance scoring and list composition separate. When a client opts into category
diversity, I fetch a bounded relevance pool, take the best item from each category, then
fill remaining slots in original score order. The policy has its own model version, so it
cannot collide with ordinary cached results, and impressions capture the final reranked
order. It is intentionally a transparent heuristic—not a fabricated claim of engagement
lift—and it creates a clean seam for a future embedding-based or experimentally tuned
reranker.”
