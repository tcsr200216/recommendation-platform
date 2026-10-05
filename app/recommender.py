from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class InteractionType(StrEnum):
    VIEW = "view"
    CLICK = "click"
    LIKE = "like"
    PURCHASE = "purchase"


class RecommendationReason(StrEnum):
    POPULAR = "popular"
    SIMILAR_USERS = "similar_users"
    POPULARITY_FALLBACK = "popularity_fallback"


INTERACTION_WEIGHTS: dict[InteractionType, float] = {
    InteractionType.VIEW: 1.0,
    InteractionType.CLICK: 2.0,
    InteractionType.LIKE: 3.0,
    InteractionType.PURCHASE: 5.0,
}
INTERACTION_HALF_LIFE_DAYS = 30.0


@dataclass(frozen=True, slots=True)
class Interaction:
    user_id: str
    item_id: str
    interaction_type: InteractionType
    occurred_at: datetime | None = None
    recommendation_request_id: str | None = None

    def __post_init__(self) -> None:
        if self.occurred_at is None:
            return
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone offset")
        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class Recommendation:
    item_id: str
    score: float
    reason: RecommendationReason = RecommendationReason.POPULAR
    supporting_item_count: int = 0


def _reference_time(interactions: tuple[Interaction, ...]) -> datetime | None:
    timestamps = [event.occurred_at for event in interactions if event.occurred_at is not None]
    return max(timestamps) if timestamps else None


def _temporal_strength(interaction: Interaction, reference_time: datetime | None) -> float:
    """Apply deterministic exponential decay while preserving legacy undated events."""
    strength = INTERACTION_WEIGHTS[interaction.interaction_type]
    if interaction.occurred_at is None or reference_time is None:
        return strength
    age_seconds = max(0.0, (reference_time - interaction.occurred_at).total_seconds())
    half_life_seconds = INTERACTION_HALF_LIFE_DAYS * 24 * 60 * 60
    return strength * math.pow(0.5, age_seconds / half_life_seconds)


class PopularityRecommender:
    """Time-decayed weighted-popularity baseline for cold-start recommendations."""

    model_version = "catalog-aware-time-decayed-popularity-v4-30d"

    def __init__(
        self,
        interactions: Iterable[Interaction],
        eligible_item_ids: AbstractSet[str] | None = None,
    ) -> None:
        self._interactions = tuple(interactions)
        self._reference_time = _reference_time(self._interactions)
        self._eligible_item_ids = eligible_item_ids

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
            scores[interaction.item_id] += _temporal_strength(
                interaction, self._reference_time
            )

        ranked = (
            Recommendation(
                item_id=item_id,
                score=score,
                reason=RecommendationReason.POPULAR,
            )
            for item_id, score in scores.items()
            if item_id not in seen_items
            and (self._eligible_item_ids is None or item_id in self._eligible_item_ids)
        )

        return sorted(
            ranked,
            key=lambda recommendation: (-recommendation.score, recommendation.item_id),
        )[:limit]


class PersonalizedRecommender:
    """User-based collaborative ranking over implicit interaction strengths.

    Each (user, item) is assigned its strongest time-decayed interaction weight,
    preventing repeated events from artificially inflating a profile. Positive
    cosine-similarity neighbors vote for items the target user has not seen.
    If no personalized candidates exist, the original popularity baseline is
    used unchanged for cold-start and sparse-overlap scenarios.
    """

    model_version = "catalog-aware-time-decayed-user-cosine-v4-30d"

    def __init__(
        self,
        interactions: Iterable[Interaction],
        eligible_item_ids: AbstractSet[str] | None = None,
    ) -> None:
        self._interactions = tuple(interactions)
        self._eligible_item_ids = eligible_item_ids
        self._fallback = PopularityRecommender(self._interactions, eligible_item_ids)
        self._reference_time = _reference_time(self._interactions)
        profiles: dict[str, dict[str, float]] = defaultdict(dict)

        for interaction in self._interactions:
            weight = _temporal_strength(interaction, self._reference_time)
            profile = profiles[interaction.user_id]
            profile[interaction.item_id] = max(profile.get(interaction.item_id, 0.0), weight)

        self._profiles = dict(profiles)

    def recommend(self, user_id: str, limit: int = 10) -> list[Recommendation]:
        if limit <= 0:
            return []

        target = self._profiles.get(user_id)
        if not target:
            return self._fallback_recommend(user_id, limit)

        target_norm = math.sqrt(math.fsum(value * value for value in target.values()))
        if target_norm == 0:
            return self._fallback_recommend(user_id, limit)

        scores: dict[str, float] = defaultdict(float)
        evidence: dict[str, set[str]] = defaultdict(set)
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

            shared_items = target.keys() & profile.keys()
            for item_id, strength in profile.items():
                if item_id not in target and (
                    self._eligible_item_ids is None or item_id in self._eligible_item_ids
                ):
                    scores[item_id] += similarity * strength
                    evidence[item_id].update(shared_items)

        if not scores:
            return self._fallback_recommend(user_id, limit)

        return sorted(
            (
                Recommendation(
                    item_id=item_id,
                    score=score,
                    reason=RecommendationReason.SIMILAR_USERS,
                    supporting_item_count=len(evidence[item_id]),
                )
                for item_id, score in scores.items()
            ),
            key=lambda recommendation: (-recommendation.score, recommendation.item_id),
        )[:limit]

    def _fallback_recommend(self, user_id: str, limit: int) -> list[Recommendation]:
        return [
            Recommendation(
                item_id=item.item_id,
                score=item.score,
                reason=RecommendationReason.POPULARITY_FALLBACK,
            )
            for item in self._fallback.recommend(user_id, limit)
        ]
