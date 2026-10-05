# ADR-014: Durable catalog-aware ranking

## Context / Problem

The service ranked only interaction item IDs. It could not attach user-facing item
metadata or prevent an unavailable product, course, or video from being recommended.
Filtering after ranking would return short result pages and could let stale cached
rankings bypass current eligibility.

## Decision

Add an `ItemCatalog` boundary with in-memory and PostgreSQL adapters. Catalog records
contain a stable item ID, title, category, and active flag. Expose idempotent item
upsert and lookup APIs. When at least one catalog record exists, pass the complete set
of active item IDs into both personalized and popularity rankers before sorting and
limiting. Enrich every recommendation response from the catalog after reading either
live rankings or cached ranking identities.

Catalog writes invalidate the whole ranking cache because activating or deactivating
one item can change result membership for every user. Bump both model versions so a
deployment cannot consume pre-catalog cached results. If the catalog is empty, retain
interaction-only ranking with nullable response metadata for backward compatibility;
once populated, unknown and inactive items are ineligible.

## Why this approach

Eligibility belongs inside candidate generation, not as a response-only filter. That
preserves the requested limit whenever enough active candidates exist and keeps both
ranking strategies consistent. Caching only ranking identities and scores means title
or category changes appear immediately after enrichment, while invalidation protects
the eligibility-changing active flag.

## Alternatives considered

- **Filter recommendations after ranking:** simpler, but can return too few items and
  wastes ranker capacity on unavailable candidates.
- **Copy item metadata into interactions:** duplicates mutable catalog data across an
  append-only event stream and makes corrections difficult.
- **Require catalog membership immediately:** cleaner for a new system, but would
  break existing local data and clients created before the catalog existed.
- **Cache fully enriched payloads:** avoids a catalog read, but makes metadata updates
  stale until every cached entry expires or is invalidated.

## Tradeoffs / risks

The API currently reads the full catalog and interaction snapshot on a cache miss;
this is correct but not suitable for a very large corpus. Global invalidation is also
conservative. Empty-catalog compatibility creates two explicit modes that operators
must understand. The schema is additive, but a production deployment should replace
runtime schema creation with managed migrations before destructive changes.

## How it fits the architecture

The HTTP layer owns catalog CRUD and joins metadata onto ranked identities. The
ranking boundary receives only an active-ID set, so it remains independent of SQL and
API models. PostgreSQL durably stores catalog and interaction data, while Redis keeps
versioned ranking results. Readiness now checks all three dependencies.

## Interview explanation

“A recommender must separate behavioral evidence from the catalog of things it is
allowed to serve. I added a storage boundary for item metadata and pushed active-item
eligibility into candidate generation before limit and sort. Cached rankings contain
only identities and scores, so metadata stays fresh, while catalog writes invalidate
rankings because availability changes result membership.”
