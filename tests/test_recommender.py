import pytest

from app.recommender import (
    Interaction,
    InteractionType,
    PersonalizedRecommender,
    PopularityRecommender,
)


def test_weighted_signals_rank_stronger_interactions_higher() -> None:
    interactions = [
        Interaction("u1", "item-a", InteractionType.VIEW),
        Interaction("u2", "item-a", InteractionType.CLICK),
        Interaction("u3", "item-b", InteractionType.PURCHASE),
    ]

    recommendations = PopularityRecommender(interactions).recommend("new-user")

    assert [item.item_id for item in recommendations] == ["item-b", "item-a"]
    assert recommendations[0].score == 5.0
    assert recommendations[1].score == 3.0


def test_seen_items_are_excluded_for_target_user() -> None:
    interactions = [
        Interaction("target", "item-a", InteractionType.VIEW),
        Interaction("other-1", "item-a", InteractionType.PURCHASE),
        Interaction("other-2", "item-b", InteractionType.LIKE),
    ]

    recommendations = PopularityRecommender(interactions).recommend("target")

    assert [item.item_id for item in recommendations] == ["item-b"]


def test_limit_caps_number_of_recommendations() -> None:
    interactions = [
        Interaction("u1", "item-a", InteractionType.PURCHASE),
        Interaction("u2", "item-b", InteractionType.LIKE),
        Interaction("u3", "item-c", InteractionType.CLICK),
    ]

    recommendations = PopularityRecommender(interactions).recommend("new-user", limit=2)

    assert len(recommendations) == 2
    assert [item.item_id for item in recommendations] == ["item-a", "item-b"]


def test_ties_are_broken_deterministically_by_item_id() -> None:
    interactions = [
        Interaction("u1", "item-b", InteractionType.CLICK),
        Interaction("u2", "item-a", InteractionType.CLICK),
    ]

    recommendations = PopularityRecommender(interactions).recommend("new-user")

    assert [item.item_id for item in recommendations] == ["item-a", "item-b"]


@pytest.mark.parametrize("limit", [0, -1, -10])
def test_non_positive_limits_return_empty_results(limit: int) -> None:
    interactions = [Interaction("u1", "item-a", InteractionType.PURCHASE)]

    assert PopularityRecommender(interactions).recommend("new-user", limit=limit) == []


def test_empty_interactions_return_no_recommendations() -> None:
    assert PopularityRecommender([]).recommend("new-user") == []


def test_personalized_ranker_uses_neighbor_overlap_not_global_popularity() -> None:
    interactions = [
        Interaction("target", "shared", InteractionType.LIKE),
        Interaction("neighbor", "shared", InteractionType.PURCHASE),
        Interaction("neighbor", "niche", InteractionType.PURCHASE),
        Interaction("stranger-1", "popular", InteractionType.PURCHASE),
        Interaction("stranger-2", "popular", InteractionType.PURCHASE),
    ]

    result = PersonalizedRecommender(interactions).recommend("target")

    assert [item.item_id for item in result] == ["niche"]
    assert result[0].score > 0


def test_personalized_ranker_uses_stronger_candidate_interactions() -> None:
    interactions = [
        Interaction("target", "shared", InteractionType.CLICK),
        Interaction("neighbor", "shared", InteractionType.CLICK),
        Interaction("neighbor", "weak", InteractionType.VIEW),
        Interaction("neighbor", "strong", InteractionType.PURCHASE),
    ]

    result = PersonalizedRecommender(interactions).recommend("target")

    assert [item.item_id for item in result] == ["strong", "weak"]
    assert result[0].score > result[1].score


def test_duplicate_events_do_not_inflate_profile_strengths() -> None:
    base = [
        Interaction("target", "shared", InteractionType.LIKE),
        Interaction("neighbor", "shared", InteractionType.CLICK),
        Interaction("neighbor", "candidate", InteractionType.PURCHASE),
    ]
    duplicates = base + [
        Interaction("neighbor", "shared", InteractionType.VIEW),
        Interaction("neighbor", "shared", InteractionType.CLICK),
        Interaction("neighbor", "candidate", InteractionType.VIEW),
        Interaction("target", "shared", InteractionType.VIEW),
    ]

    assert PersonalizedRecommender(base).recommend("target") == (
        PersonalizedRecommender(duplicates).recommend("target")
    )


def test_personalized_ranker_excludes_seen_and_breaks_ties_by_item_id() -> None:
    interactions = [
        Interaction("target", "shared", InteractionType.LIKE),
        Interaction("neighbor", "shared", InteractionType.LIKE),
        Interaction("neighbor", "item-b", InteractionType.CLICK),
        Interaction("neighbor", "item-a", InteractionType.CLICK),
    ]

    result = PersonalizedRecommender(interactions).recommend("target", limit=1)

    assert [item.item_id for item in result] == ["item-a"]


def test_personalized_cold_start_matches_popularity_exactly() -> None:
    interactions = [
        Interaction("other", "item-a", InteractionType.VIEW),
        Interaction("other", "item-b", InteractionType.PURCHASE),
    ]

    assert PersonalizedRecommender(interactions).recommend("new-user") == (
        PopularityRecommender(interactions).recommend("new-user")
    )


def test_personalized_sparse_overlap_falls_back_to_popularity_exactly() -> None:
    interactions = [
        Interaction("target", "seen", InteractionType.VIEW),
        Interaction("other", "unseen", InteractionType.PURCHASE),
    ]

    assert PersonalizedRecommender(interactions).recommend("target") == (
        PopularityRecommender(interactions).recommend("target")
    )


@pytest.mark.parametrize("limit", [0, -1])
def test_personalized_non_positive_limit_returns_empty(limit: int) -> None:
    interactions = [Interaction("other", "item", InteractionType.VIEW)]

    assert PersonalizedRecommender(interactions).recommend("new-user", limit) == []
