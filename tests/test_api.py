import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.cache import InMemoryRecommendationCache
from app.recommender import Interaction, InteractionType
from app.repository import InMemoryInteractionRepository


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch) -> None:
    monkeypatch.setattr(main, "recommendation_cache", InMemoryRecommendationCache())


class UnavailableRepository(InMemoryInteractionRepository):
    @property
    def backend(self) -> str:
        return "unavailable"

    def is_ready(self) -> bool:
        return False


class UnavailableCache(InMemoryRecommendationCache):
    def is_ready(self) -> bool:
        return False


def test_ready_reports_active_storage_backend(monkeypatch) -> None:
    monkeypatch.setattr(main, "interaction_repository", InMemoryInteractionRepository())

    response = TestClient(main.app).get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "storage": "memory", "cache": "memory"}


def test_ready_returns_503_when_repository_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(main, "interaction_repository", UnavailableRepository())

    response = TestClient(main.app).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "Interaction repository is unavailable."


def test_ready_returns_503_when_cache_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(main, "interaction_repository", InMemoryInteractionRepository())
    monkeypatch.setattr(main, "recommendation_cache", UnavailableCache())

    response = TestClient(main.app).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "Recommendation cache is unavailable."


def test_api_exposes_personalized_ranking_and_original_popularity(monkeypatch) -> None:
    repository = InMemoryInteractionRepository()
    for interaction in [
        Interaction("target", "shared", InteractionType.LIKE),
        Interaction("neighbor", "shared", InteractionType.PURCHASE),
        Interaction("neighbor", "niche", InteractionType.PURCHASE),
        Interaction("other-1", "popular", InteractionType.PURCHASE),
        Interaction("other-2", "popular", InteractionType.PURCHASE),
    ]:
        repository.add(interaction)
    monkeypatch.setattr(main, "interaction_repository", repository)
    client = TestClient(main.app)

    personalized = client.get("/recommendations/target")
    popular = client.get("/recommendations/target?strategy=popular")

    assert personalized.status_code == 200
    assert [item["item_id"] for item in personalized.json()] == ["niche"]
    assert popular.status_code == 200
    assert [item["item_id"] for item in popular.json()] == ["popular", "niche"]


def test_api_rejects_unknown_ranking_strategy(monkeypatch) -> None:
    monkeypatch.setattr(main, "interaction_repository", InMemoryInteractionRepository())

    response = TestClient(main.app).get("/recommendations/target?strategy=unknown")

    assert response.status_code == 422


def test_recording_new_interaction_invalidates_cached_rankings(monkeypatch) -> None:
    repository = InMemoryInteractionRepository()
    for interaction in [
        Interaction("target", "common", InteractionType.LIKE),
        Interaction("neighbor", "common", InteractionType.CLICK),
        Interaction("neighbor", "candidate", InteractionType.PURCHASE),
    ]:
        repository.add(interaction)
    monkeypatch.setattr(main, "interaction_repository", repository)
    client = TestClient(main.app)

    first = client.get("/recommendations/target")
    second = client.get("/recommendations/target")
    assert first.json() == second.json()
    assert [item["item_id"] for item in first.json()] == ["candidate"]

    recorded = client.post(
        "/interactions",
        json={"user_id": "target", "item_id": "candidate", "interaction_type": "view"},
    )
    assert recorded.status_code == 201

    updated = client.get("/recommendations/target")
    assert updated.status_code == 200
    assert updated.json() == []
