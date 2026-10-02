# ADR-004: Deterministic offline ranking evaluation

Status: Accepted

## Context

Both rankers need a repeatable comparison outside the live serving path.
Interactions have user, item, and type, but no timestamp or item catalog.
Repeated events must not leak held-out user/item pairs into training.

## Decision

Hold out one distinct item per eligible user simultaneously, chosen by the
lexicographically greatest item ID. Remove all events for that pair. Retain
single-item users as training signals but report them as skipped for scoring.
Build both rankers from exactly the same training events and rank their full
training-derived candidate sets, excluding seen items as serving does.

Report HitRate@K and MRR@K with the same eligible-user denominator. Missing
training-catalog targets remain misses; report their count separately. Return
null metrics for an empty denominator rather than implying a measured zero.
Label reports with explicit algorithm versions; these identify implementation
families, not trained artifacts. Bump versions when ranking semantics change.

The CLI consumes a local JSON snapshot and does not connect to production
storage or cache. A synthetic sample makes the workflow runnable immediately.

## Consequences and limitations

The split is reproducible and groups duplicates correctly, but item IDs can
bias target selection. It does not model time, future-item availability,
exposure, or negative feedback. Simultaneous holdouts can remove an item from
the entire training catalog. Keeping those misses avoids quietly inflating
metrics, while their count makes this limitation visible.

Neither metric estimates revenue, click-through rate, fairness, or causal
impact. The sample is too small and synthetic for quality claims. Before a
production model decision, add timestamped real snapshots, chronological
splits, catalog-aware candidates, cohort analysis, uncertainty estimates,
and online experiments. Keep offline evaluation out of request handlers.
