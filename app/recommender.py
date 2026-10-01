from __future__ import annotations

import math
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


class PersonalizedRecommender:
    """User-based collaborative ranking over implicit interaction strengths.

    Each (user, item) is assigned its strongest observed interaction weight,
    preventing repeated events from artificially inflating a profile. Positive
    cosine-similarity neighbors vote for items the target user has not seen.
    If no personalized candidates exist, the original popularity baseline is
    used unchanged for cold-start and sparse-overlap scenarios.
    """

    def __init__(self, interactions: Iterable[Interaction]) -> None:
        self._interactions = tuple(interactions)
        self._fallback = PopularityRecommender(self._interactions)
        profiles: dict[str, dict[str, float]] = defaultdict(dict)

        for interaction in self._interactions:
            weight = INTERACTION_WEIGHTS[interaction.interaction_type]
            profile = profiles[interaction.user_id]
            profile[interaction.item_id] = max(profile.get(interaction.item_id, 0.0), weight)

        self._profiles = dict(profiles)

    def recommend(self, user_id: str, limit: int = 10) -> list[Recommendation]:
        if limit <= 0:
            return []

        target = self._profiles.get(user_id)
        if not target:
            return self._fallback.recommend(user_id, limit)

        target_norm = math.sqrt(math.fsum(value * value for value in target.values()))
        if target_norm == 0:
            return self._fallback.recommend(user_id, limit)

        scores: dict[str, float] = defaultdict(float)
        for neighbor_id, profile in self._profiles.items():
            if neighbor_id == user_id:
                continue

            dot = math.fsum(
                target[item_id] * strength
                for item_id, strength in profile.items()
                if item_id in target
            )
            if dot <= 0:
                continue

            neighbor_norm = math.sqrt(math.fsum(value * value for value in profile.values()))
            similarity = dot / (target_norm * neighbor_norm)
            if similarity <= 0:
                continue

            for item_id, strength in profile.items():
                if item_id not in target:
                    scores[item_id] += similarity * strength

        if not scores:
            return self._fallback.recommend(user_id, limit)

        return sorted(
            (Recommendation(item_id=item_id, score=score) for item_id, score in scores.items()),
            key=lambda recommendation: (-recommendation.score, recommendation.item_id),
        )[:limit]
