import pytest

from app.recommender import (
    Interaction,
    InteractionType,
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
    interactions = [
        Interaction("u1", "item-a", InteractionType.PURCHASE),
    ]

    assert PopularityRecommender(interactions).recommend("new-user", limit=limit) == []


def test_empty_interactions_return_no_recommendations() -> None:
    assert PopularityRecommender([]).recommend("new-user") == []
