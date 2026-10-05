from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.recommender import Recommendation

CATEGORY_DIVERSITY_VERSION = "category-coverage-v1-pool5x"
CATEGORY_DIVERSITY_POOL_MULTIPLIER = 5
MAX_DIVERSITY_CANDIDATES = 500


def diversity_candidate_limit(limit: int) -> int:
    """Return a bounded candidate depth for category-aware reranking."""
    return min(MAX_DIVERSITY_CANDIDATES, limit * CATEGORY_DIVERSITY_POOL_MULTIPLIER)


def diversify_by_category(
    recommendations: Sequence[Recommendation],
    categories: Mapping[str, str | None],
    limit: int,
) -> list[Recommendation]:
    """Cover available categories once, then fill by the original relevance order.

    The input is already relevance sorted. The first pass takes the highest-ranked
    item from each category; the second pass fills remaining slots without changing
    the relative order of any unselected candidates. Missing metadata is grouped as
    one unknown category, so interaction-only deployments remain deterministic.
    """
    if limit <= 0:
        return []

    selected: list[Recommendation] = []
    selected_ids: set[str] = set()
    covered_categories: set[str | None] = set()
    for recommendation in recommendations:
        category = categories.get(recommendation.item_id)
        if category in covered_categories:
            continue
        selected.append(recommendation)
        selected_ids.add(recommendation.item_id)
        covered_categories.add(category)
        if len(selected) == limit:
            return selected

    for recommendation in recommendations:
        if recommendation.item_id in selected_ids:
            continue
        selected.append(recommendation)
        if len(selected) == limit:
            break
    return selected
