from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from redis import Redis
from redis.exceptions import RedisError

from app.recommender import Recommendation, RecommendationReason


@dataclass(frozen=True, slots=True)
class CacheLookup:
    generation: int
    recommendations: tuple[Recommendation, ...] | None


class RecommendationCache(Protocol):
    """Cache-aside boundary for ranked recommendation results."""

    @property
    def backend(self) -> str:
        ...

    def get(
        self, user_id: str, strategy: str, model_version: str, limit: int
    ) -> tuple[Recommendation, ...] | None:
        ...

    def lookup(
        self, user_id: str, strategy: str, model_version: str, limit: int
    ) -> CacheLookup:
        """Read a result and the generation used for that lookup."""
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

    def set_if_current(
        self,
        user_id: str,
        strategy: str,
        model_version: str,
        limit: int,
        recommendations: Sequence[Recommendation],
        expected_generation: int,
    ) -> bool:
        """Store only if no invalidation occurred since lookup."""
        ...

    def invalidate(self) -> None:
        """Invalidate all rankings after interaction or catalog changes."""
        ...

    def is_ready(self) -> bool:
        ...


def _key(
    namespace: str,
    generation: int,
    user_id: str,
    strategy: str,
    model_version: str,
    limit: int,
) -> str:
    _validate_model_version(model_version)
    # Hash the user ID to avoid delimiter ambiguity and exposing raw IDs in Redis keys.
    user_token = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    return f"{namespace}:g{generation}:{strategy}:{model_version}:{limit}:{user_token}"


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
        self._generation = 0
        self._entries: dict[str, tuple[float, tuple[Recommendation, ...]]] = {}

    @property
    def backend(self) -> str:
        return "memory"

    def get(
        self, user_id: str, strategy: str, model_version: str, limit: int
    ) -> tuple[Recommendation, ...] | None:
        return self.lookup(user_id, strategy, model_version, limit).recommendations

    def lookup(
        self, user_id: str, strategy: str, model_version: str, limit: int
    ) -> CacheLookup:
        key = _key(
            self._namespace,
            self._generation,
            user_id,
            strategy,
            model_version,
            limit,
        )
        entry = self._entries.get(key)
        if entry is None:
            return CacheLookup(self._generation, None)
        expires_at, results = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            return CacheLookup(self._generation, None)
        return CacheLookup(self._generation, results)

    def set(
        self,
        user_id: str,
        strategy: str,
        model_version: str,
        limit: int,
        recommendations: Sequence[Recommendation],
    ) -> None:
        self.set_if_current(
            user_id,
            strategy,
            model_version,
            limit,
            recommendations,
            self._generation,
        )

    def set_if_current(
        self,
        user_id: str,
        strategy: str,
        model_version: str,
        limit: int,
        recommendations: Sequence[Recommendation],
        expected_generation: int,
    ) -> bool:
        if expected_generation != self._generation:
            return False
        self._entries[
            _key(
                self._namespace,
                self._generation,
                user_id,
                strategy,
                model_version,
                limit,
            )
        ] = (
            self._clock() + self._ttl_seconds,
            tuple(recommendations),
        )
        return True

    def invalidate(self) -> None:
        self._generation += 1
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
    """Redis cache with validated JSON and atomic generation invalidation."""

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
        self._generation_key = f"{namespace}:generation"

    def _generation(self) -> int:
        value = self._client.get(self._generation_key)
        if value is None:
            return 0
        try:
            generation = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RedisError("Recommendation cache generation is invalid.") from exc
        if generation < 0:
            raise RedisError("Recommendation cache generation is invalid.")
        return generation

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
        return self.lookup(user_id, strategy, model_version, limit).recommendations

    def lookup(
        self, user_id: str, strategy: str, model_version: str, limit: int
    ) -> CacheLookup:
        generation = self._generation()
        key = _key(
            self._namespace,
            generation,
            user_id,
            strategy,
            model_version,
            limit,
        )
        payload = self._client.get(key)
        if payload is None:
            return CacheLookup(generation, None)
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
                    or item.get("reason") not in RecommendationReason
                    or type(item.get("supporting_item_count")) is not int
                    or item["supporting_item_count"] < 0
                ):
                    raise ValueError("Invalid cached recommendation.")
                results.append(
                    Recommendation(
                        item_id=item["item_id"],
                        score=float(item["score"]),
                        reason=RecommendationReason(item["reason"]),
                        supporting_item_count=item["supporting_item_count"],
                    )
                )
            return CacheLookup(generation, tuple(results))
        except (ValueError, TypeError, OverflowError):
            # Do not serve invalid or incompatible cached results.
            self._client.delete(key)
            return CacheLookup(generation, None)

    def set(
        self,
        user_id: str,
        strategy: str,
        model_version: str,
        limit: int,
        recommendations: Sequence[Recommendation],
    ) -> None:
        generation = self._generation()
        self.set_if_current(
            user_id,
            strategy,
            model_version,
            limit,
            recommendations,
            generation,
        )

    def set_if_current(
        self,
        user_id: str,
        strategy: str,
        model_version: str,
        limit: int,
        recommendations: Sequence[Recommendation],
        expected_generation: int,
    ) -> bool:
        payload = json.dumps(
            [
                {
                    "item_id": item.item_id,
                    "score": item.score,
                    "reason": item.reason,
                    "supporting_item_count": item.supporting_item_count,
                }
                for item in recommendations
            ],
            allow_nan=False,
            separators=(",", ":"),
        )
        key = _key(
            self._namespace,
            expected_generation,
            user_id,
            strategy,
            model_version,
            limit,
        )
        stored = self._client.eval(
            """
            local current = redis.call('GET', KEYS[1])
            if not current then current = '0' end
            if current ~= ARGV[1] then return 0 end
            redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
            return 1
            """,
            2,
            self._generation_key,
            key,
            str(expected_generation),
            payload,
            self._ttl_seconds,
        )
        return bool(stored)

    def invalidate(self) -> None:
        # A new generation makes every prior key unreachable in one atomic Redis
        # operation. Old result keys expire naturally under their bounded TTL.
        self._client.incr(self._generation_key)

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
