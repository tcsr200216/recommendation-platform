# ADR-002: User-based collaborative personalization

## Context / Problem

The first ranking API implements only global weighted popularity, so every user sees
essentially the same ordering after excluding their already-seen items. The next step
needs a real, explainable personalization strategy that works with the existing implicit
interaction events and both persistence adapters.

## Decision

Add `PersonalizedRecommender` alongside the unchanged `PopularityRecommender`.
Build a user-item strength profile from the maximum interaction weight per user/item,
rather than adding repeated event counts. Compare the target profile with other user
profiles using positive cosine similarity over shared items. For every positive-similarity
neighbor, score unseen candidate items by similarity multiplied by that neighbor's
interaction strength, sum contributions, and sort by descending score with an item-ID
tie-break. With no target profile or no personalized candidates, return the existing
popularity algorithm's results exactly. The API defaults to `strategy=personalized` and
accepts `strategy=popular` for explicit baseline comparison.

## Why this approach

User-based collaborative filtering offers meaningful personalization without requiring
item embeddings or a separate model service. Stronger implicit events contribute more
signal while maximum-weight deduplication stops duplicate views/clicks from turning into
artificially strong preferences. Retaining the baseline allows honest cold-start
behavior, regression comparisons, and straightforward offline evaluation later.

## Alternatives considered

- Keep weighted popularity only: inexpensive but unable to tailor ranking to interests.
- Sum every event per user/item: straightforward, but repeated tracking events can
  dominate similarity and overstate a user's preference.
- Use item-based nearest neighbors: useful, but introduces a different precomputation
  and serving path before we have evaluation and seed data.
- Train matrix factorization or neural recommenders immediately: promising with enough
  interactions, but adds training/versioning overhead before a transparent baseline.
- Blend popularity into every personalized result: fills sparse lists, but obscures
  whether personalization actually contributed to the presented ordering.

## Tradeoffs / Risks

This MVP computes user vectors and comparisons from a repository snapshot per request;
it will need precomputation or selective queries as volume grows. Cosine similarity over
positive implicit events is simple but cannot represent dislike or exposure bias, and
users without shared items fall back to global popularity. The candidate scores are
relative ranking signals, not predicted ratings or calibrated probabilities. A later
evaluation stage should compare popularity and personalized ranking against held-out
events before claiming an uplift.

## How it fits the architecture

The repository port supplies interactions independent of in-memory or SQL storage.
The API selects a ranking strategy without changing its response contract. The
personalized ranker handles user profiles and similarity; the popularity baseline
remains independently testable and provides cold-start fallback.

## Interview explanation

"I started with a weighted popularity baseline, then implemented user-based collaborative
filtering using implicit-event strengths. For each user/item I keep the strongest signal
to prevent duplicate events from gaming the profile. I compute cosine overlap with
neighbors and recommend their unseen items using similarity-weighted scores. If there
isn't enough shared history, I return the original popularity baseline. The design is
explainable and easy to evaluate, but for higher traffic I'd precompute similarities
and validate effectiveness with held-out interaction metrics."
