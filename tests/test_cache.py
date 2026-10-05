import json

import pytest
from redis.exceptions import RedisError

from app.cache import (
    InMemoryRecommendationCache,
    RedisRecommendationCache,
    build_recommendation_cache,
)
from app.recommender import Recommendation, RecommendationReason


class FakeRedis:
    def __init__(self) -> None:
        self.entries: dict[str, str] = {}
        self.expirations: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self.entries.get(key)

    def set(self, key: str, value: str, ex: int) -> None:
        self.entries[key] = value
        self.expirations[key] = ex

    def incr(self, key: str) -> int:
        value = int(self.entries.get(key, "0")) + 1
        self.entries[key] = str(value)
        return value

    def eval(self, script: str, number_of_keys: int, *values) -> int:
        assert number_of_keys == 2
        generation_key, result_key, expected, payload, ttl = values
        current = self.entries.get(generation_key, "0")
        if current != expected:
            return 0
        self.set(result_key, payload, ex=int(ttl))
        return 1

    def delete(self, *keys: str) -> None:
        for key in keys:
            self.entries.pop(key, None)
            self.expirations.pop(key, None)

    def ping(self) -> bool:
        return True


def test_memory_cache_respects_ttl_and_can_store_empty_results() -> None:
    clock = [10.0]
    cache = InMemoryRecommendationCache(ttl_seconds=5, clock=lambda: clock[0])
    cache.set("user", "personalized", "user-cosine-v1", 10, [])

    assert cache.get("user", "personalized", "user-cosine-v1", 10) == ()
    clock[0] = 15.0
    assert cache.get("user", "personalized", "user-cosine-v1", 10) is None


def test_cache_key_separates_strategy_limit_and_user() -> None:
    cache = InMemoryRecommendationCache()
    result = [Recommendation("item-a", 2.0)]
    cache.set("alice", "popular", "weighted-popularity-v1", 5, result)

    assert cache.get("alice", "popular", "weighted-popularity-v1", 5) == tuple(result)
    assert cache.get("alice", "personalized", "weighted-popularity-v1", 5) is None
    assert cache.get("alice", "popular", "weighted-popularity-v1", 10) is None
    assert cache.get("bob", "popular", "weighted-popularity-v1", 5) is None


def test_cache_key_separates_model_versions() -> None:
    cache = InMemoryRecommendationCache()
    result = [Recommendation("item-a", 2.0)]
    cache.set("alice", "personalized", "user-cosine-v1", 10, result)

    assert cache.get("alice", "personalized", "user-cosine-v1", 10) == tuple(result)
    assert cache.get("alice", "personalized", "user-cosine-v2", 10) is None


def test_memory_invalidation_clears_all_users_and_strategies() -> None:
    cache = InMemoryRecommendationCache()
    cache.set("alice", "popular", "weighted-popularity-v1", 5, [Recommendation("a", 1.0)])
    cache.set("bob", "personalized", "user-cosine-v1", 10, [Recommendation("b", 3.0)])

    cache.invalidate()

    assert cache.get("alice", "popular", "weighted-popularity-v1", 5) is None
    assert cache.get("bob", "personalized", "user-cosine-v1", 10) is None


def test_redis_cache_round_trip_ttl_and_generation_invalidation() -> None:
    client = FakeRedis()
    cache = RedisRecommendationCache(client, ttl_seconds=60, namespace="recommendations:v2")
    other = RedisRecommendationCache(client, ttl_seconds=60, namespace="other:v1")
    result = [
        Recommendation(
            "item-a",
            0.75,
            RecommendationReason.SIMILAR_USERS,
            2,
        )
    ]
    cache.set("alice", "popular", "weighted-popularity-v1", 10, result)
    cache.set("bob", "personalized", "user-cosine-v1", 5, [])
    other.set("alice", "popular", "weighted-popularity-v1", 10, result)

    assert cache.get("alice", "popular", "weighted-popularity-v1", 10) == tuple(result)
    assert cache.get("bob", "personalized", "user-cosine-v1", 5) == ()
    assert set(client.expirations.values()) == {60}
    assert all("alice" not in key and "bob" not in key for key in client.entries)
    assert cache.is_ready() is True

    cache.invalidate()

    assert cache.get("alice", "popular", "weighted-popularity-v1", 10) is None
    assert other.get("alice", "popular", "weighted-popularity-v1", 10) == tuple(result)
    assert client.entries["recommendations:v2:generation"] == "1"
    assert any(key.startswith("recommendations:v2:g0:") for key in client.entries)

    cache.set("alice", "popular", "weighted-popularity-v1", 10, result)
    assert any(key.startswith("recommendations:v2:g1:") for key in client.entries)
    assert cache.get("alice", "popular", "weighted-popularity-v1", 10) == tuple(result)


def test_redis_invalidation_is_constant_time_regardless_of_cached_users() -> None:
    client = FakeRedis()
    cache = RedisRecommendationCache(client)
    for index in range(250):
        cache.set(
            f"user-{index}",
            "popular",
            "weighted-popularity-v1",
            10,
            [Recommendation(f"item-{index}", 1.0)],
        )

    cache.invalidate()

    assert client.entries["recommendations:v1:generation"] == "1"
    assert cache.get("user-0", "popular", "weighted-popularity-v1", 10) is None
    assert cache.get("user-249", "popular", "weighted-popularity-v1", 10) is None


@pytest.mark.parametrize("value", ["not-an-integer", "-1"])
def test_redis_rejects_corrupt_generation(value: str) -> None:
    client = FakeRedis()
    client.entries["recommendations:v1:generation"] = value
    cache = RedisRecommendationCache(client)

    with pytest.raises(RedisError, match="generation is invalid"):
        cache.get("user", "popular", "weighted-popularity-v1", 10)


def test_generation_guard_rejects_ranking_computed_before_invalidation() -> None:
    client = FakeRedis()
    cache = RedisRecommendationCache(client)
    lookup = cache.lookup("user", "popular", "weighted-popularity-v1", 10)

    cache.invalidate()
    stored = cache.set_if_current(
        "user",
        "popular",
        "weighted-popularity-v1",
        10,
        [Recommendation("stale", 1.0)],
        lookup.generation,
    )

    assert stored is False
    assert cache.get("user", "popular", "weighted-popularity-v1", 10) is None


def test_malformed_redis_payload_is_deleted_and_treated_as_miss() -> None:
    client = FakeRedis()
    cache = RedisRecommendationCache(client)
    cache.set("user", "popular", "weighted-popularity-v1", 10, [Recommendation("valid", 1.0)])
    key = next(iter(client.entries))
    for payload in (
        "not json",
        json.dumps({"item_id": "a"}),
        json.dumps([{"item_id": "x", "score": "bad"}]),
        json.dumps([
            {
                "item_id": "x",
                "score": 1.0,
                "reason": "private_neighbor",
                "supporting_item_count": 0,
            }
        ]),
    ):
        client.entries[key] = payload
        assert cache.get("user", "popular", "weighted-popularity-v1", 10) is None
        assert key not in client.entries


def test_factory_preserves_no_infrastructure_local_fallback() -> None:
    cache = build_recommendation_cache(None)
    assert isinstance(cache, InMemoryRecommendationCache)
    assert cache.backend == "memory"


@pytest.mark.parametrize("ttl", [0, -1])
def test_rejects_invalid_ttl(ttl: int) -> None:
    with pytest.raises(ValueError, match="TTL"):
        InMemoryRecommendationCache(ttl_seconds=ttl)


def test_rejects_unsafe_namespace_characters() -> None:
    with pytest.raises(ValueError, match="namespace"):
        RedisRecommendationCache(FakeRedis(), namespace="recommendations:*")


@pytest.mark.parametrize("model_version", ["", "user:cosine", "user cosine", "v1*"])
def test_rejects_unsafe_model_version(model_version: str) -> None:
    cache = InMemoryRecommendationCache()
    with pytest.raises(ValueError, match="Model version"):
        cache.get("user", "personalized", model_version, 10)
