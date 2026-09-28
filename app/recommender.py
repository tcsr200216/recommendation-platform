from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class InteractionType(StrEnum):
    VIEW = "view"
    CLICK = "click"
    LIKE = "like"
    PURCHASE = "purchase"


INTERACTION_WEIGHTS: dict[InteractionType, float] = {
    InteractionType.VIEW: 1.0,
    InteractionType.CLICK: 2.0,
    InteractionType.LIKE: 3.0,
    InteractionType.PURCHASE: 5.0,
}


@dataclass(frozen=True, slots=True)
class Interaction:
    user_id: str
    item_id: str
    interaction_type: InteractionType


@dataclass(frozen=True, slots=True)
class Recommendation:
    item_id: str
    score: float


class PopularityRecommender:
    """Simple weighted-popularity baseline for cold-start recommendations."""

    def __init__(self, interactions: Iterable[Interaction]) -> None:
        self._interactions = tuple(interactions)

    def recommend(self, user_id: str, limit: int = 10) -> list[Recommendation]:
        if limit <= 0:
            return []

        seen_items = {
            interaction.item_id
            for interaction in self._interactions
            if interaction.user_id == user_id
        }

        scores: dict[str, float] = defaultdict(float)
        for interaction in self._interactions:
            scores[interaction.item_id] += INTERACTION_WEIGHTS[interaction.interaction_type]

        ranked = (
            Recommendation(item_id=item_id, score=score)
            for item_id, score in scores.items()
            if item_id not in seen_items
        )

        return sorted(
            ranked,
            key=lambda recommendation: (-recommendation.score, recommendation.item_id),
        )[:limit]
