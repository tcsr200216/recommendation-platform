from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Sequence
from typing import Protocol

from redis import Redis
from redis.exceptions import RedisError

from app.recommender import Recommendation


class RecommendationCache(Protocol):
    """Cache-aside boundary for ranked recommendation results."""

    @property
    def backend(self) -> str:
        ...

    def get(
        self, user_id: str, strategy: str, model_version: str, limit: int
    ) -> tuple[Recommendation, ...] | None:
        ...

    def set(
        self,
        user_id: str,
        strategy: str,
        model_version: str,
        limit: int,
        recommendations: Sequence[Recommendation],
    ) -> None:
        ...

    def invalidate(self) -> None:
        """Invalidate all cached rankings after a new interaction."""
        ...

    def is_ready(self) -> bool:
        ...


def _key(
    namespace: str, user_id: str, strategy: str, model_version: str, limit: int
) -> str:
    _validate_model_version(model_version)
    # Hash the user ID to avoid delimiter ambiguity and exposing raw IDs in Redis keys.
    user_token = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    return f"{namespace}:{strategy}:{model_version}:{limit}:{user_token}"


class InMemoryRecommendationCache:
    """Process-local TTL cache; useful without a Redis service."""

    def __init__(
        self,
        ttl_seconds: int = 300,
        namespace: str = "recommendations:v1",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        _validate_settings(ttl_seconds, namespace)
        self._ttl_seconds = ttl_seconds
        self._namespace = namespace
        self._clock = clock
        self._entries: dict[str, tuple[float, tuple[Recommendation, ...]]] = {}

    @property
    def backend(self) -> str:
        return "memory"

    def get(
        self, user_id: str, strategy: str, model_version: str, limit: int
    ) -> tuple[Recommendation, ...] | None:
        key = _key(self._namespace, user_id, strategy, model_version, limit)
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, results = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            return None
        return results

    def set(
        self,
        user_id: str,
        strategy: str,
        model_version: str,
        limit: int,
        recommendations: Sequence[Recommendation],
    ) -> None:
        self._entries[_key(self._namespace, user_id, strategy, model_version, limit)] = (
            self._clock() + self._ttl_seconds,
            tuple(recommendations),
        )

    def invalidate(self) -> None:
        self._entries.clear()

    def is_ready(self) -> bool:
        return True


def _validate_settings(ttl_seconds: int, namespace: str) -> None:
    if ttl_seconds <= 0:
        raise ValueError("Cache TTL must be greater than zero.")
    if not re.fullmatch(r"[A-Za-z0-9:_-]+", namespace):
        raise ValueError("Cache namespace must contain only letters, digits, colon, dash or underscore.")


def _validate_model_version(model_version: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", model_version):
        raise ValueError(
            "Model version must contain only letters, digits, dot, dash or underscore."
        )


class RedisRecommendationCache:
    """Redis cache with JSON validation and bounded SCAN-based invalidation."""

    def __init__(
        self,
        client: Redis,
        ttl_seconds: int = 300,
        namespace: str = "recommendations:v1",
    ) -> None:
        _validate_settings(ttl_seconds, namespace)
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._namespace = namespace

    @classmethod
    def from_url(
        cls, url: str, ttl_seconds: int = 300, namespace: str = "recommendations:v1"
    ) -> RedisRecommendationCache:
        return cls(
            Redis.from_url(url, decode_responses=True),
            ttl_seconds=ttl_seconds,
            namespace=namespace,
        )

    @property
    def backend(self) -> str:
        return "redis"

    def get(
        self, user_id: str, strategy: str, model_version: str, limit: int
    ) -> tuple[Recommendation, ...] | None:
        key = _key(self._namespace, user_id, strategy, model_version, limit)
        payload = self._client.get(key)
        if payload is None:
            return None
        try:
            value = json.loads(payload)
            if not isinstance(value, list):
                raise TypeError("Expected a list of recommendations.")
            results = []
            for item in value:
                if (
                    not isinstance(item, dict)
                    or type(item.get("item_id")) is not str
                    or not item["item_id"]
                    or type(item.get("score")) not in (float, int)
                    or not math.isfinite(item["score"])
                ):
                    raise ValueError("Invalid cached recommendation.")
                results.append(Recommendation(item_id=item["item_id"], score=float(item["score"])))
            return tuple(results)
        except (ValueError, TypeError, OverflowError):
            # Do not serve invalid or incompatible cached results.
            self._client.delete(key)
            return None

    def set(
        self,
        user_id: str,
        strategy: str,
        model_version: str,
        limit: int,
        recommendations: Sequence[Recommendation],
    ) -> None:
        payload = json.dumps(
            [
                {"item_id": item.item_id, "score": item.score}
                for item in recommendations
            ],
            allow_nan=False,
            separators=(",", ":"),
        )
        self._client.set(
            _key(self._namespace, user_id, strategy, model_version, limit),
            payload,
            ex=self._ttl_seconds,
        )

    def invalidate(self) -> None:
        # A single new event can change both global popularity and other users'
        # collaborative-neighbor scores; user-only invalidation is unsafe.
        batch: list[str] = []
        for key in self._client.scan_iter(match=f"{self._namespace}:*", count=100):
            batch.append(key)
            if len(batch) >= 100:
                self._client.delete(*batch)
                batch.clear()
        if batch:
            self._client.delete(*batch)

    def is_ready(self) -> bool:
        try:
            return bool(self._client.ping())
        except RedisError:
            return False


def build_recommendation_cache(
    redis_url: str | None,
    *,
    ttl_seconds: int = 300,
    namespace: str = "recommendations:v1",
) -> RecommendationCache:
    if redis_url:
        return RedisRecommendationCache.from_url(
            redis_url, ttl_seconds=ttl_seconds, namespace=namespace
        )
    return InMemoryRecommendationCache(ttl_seconds=ttl_seconds, namespace=namespace)
