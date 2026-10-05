import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from app import main
from app.cache import InMemoryRecommendationCache
from app.catalog import InMemoryItemCatalog, Item
from app.impressions import InMemoryImpressionRepository
from app.recommender import Interaction, InteractionType, Recommendation
from app.repository import InMemoryInteractionRepository


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch) -> None:
    monkeypatch.setattr(main, "recommendation_cache", InMemoryRecommendationCache())
    monkeypatch.setattr(main, "item_catalog", InMemoryItemCatalog())
    monkeypatch.setattr(main, "impression_repository", InMemoryImpressionRepository())


class UnavailableRepository(InMemoryInteractionRepository):
    @property
    def backend(self) -> str:
        return "unavailable"

    def is_ready(self) -> bool:
        return False


class UnavailableCache(InMemoryRecommendationCache):
    def is_ready(self) -> bool:
        return False


class UnavailableCatalog(InMemoryItemCatalog):
    def is_ready(self) -> bool:
        return False


class UnavailableImpressionRepository(InMemoryImpressionRepository):
    def is_ready(self) -> bool:
        return False


def test_ready_reports_active_storage_backend(monkeypatch) -> None:
    monkeypatch.setattr(main, "interaction_repository", InMemoryInteractionRepository())

    response = TestClient(main.app).get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "storage": "memory",
        "catalog": "memory",
        "cache": "memory",
        "impressions": "memory",
    }


def _metric_value(payload: str, name: str, labels: dict[str, str]) -> float:
    for family in text_string_to_metric_families(payload):
        for sample in family.samples:
            if sample.name == name and all(
                sample.labels.get(key) == value for key, value in labels.items()
            ):
                return sample.value
    return 0.0


def test_responses_include_generated_request_id_and_metrics_avoid_raw_paths() -> None:
    client = TestClient(main.app)
    before = client.get("/metrics").text
    labels = {"method": "GET", "route": "/recommendations/{user_id}", "status": "404"}
    initial = _metric_value(before, "recommendation_http_requests_total", labels)

    response = client.get("/recommendations/a-user-that-must-not-be-a-metric-label")

    assert response.status_code == 404
    assert len(response.headers["X-Request-ID"]) == 36
    metrics = client.get("/metrics").text
    assert _metric_value(metrics, "recommendation_http_requests_total", labels) == initial + 1
    assert "a-user-that-must-not-be-a-metric-label" not in metrics


def test_metrics_report_live_then_cached_ranking(monkeypatch) -> None:
    repository = InMemoryInteractionRepository()
    repository.add(Interaction("target-observed", "seen", InteractionType.LIKE))
    repository.add(Interaction("neighbor-observed", "seen", InteractionType.LIKE))
    repository.add(Interaction("neighbor-observed", "candidate", InteractionType.PURCHASE))
    monkeypatch.setattr(main, "interaction_repository", repository)
    client = TestClient(main.app)

    live_labels = {
        "strategy": "personalized",
        "model_version": "catalog-aware-time-decayed-user-cosine-v4-30d",
        "source": "live",
    }
    cache_labels = {**live_labels, "source": "cache"}
    before = client.get("/metrics").text
    live_before = _metric_value(before, "recommendation_ranking_requests_total", live_labels)
    cache_before = _metric_value(before, "recommendation_ranking_requests_total", cache_labels)

    assert client.get("/recommendations/target-observed").status_code == 200
    assert client.get("/recommendations/target-observed").status_code == 200

    metrics = client.get("/metrics").text
    assert (
        _metric_value(metrics, "recommendation_ranking_requests_total", live_labels)
        == live_before + 1
    )
    assert (
        _metric_value(metrics, "recommendation_ranking_requests_total", cache_labels)
        == cache_before + 1
    )


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


def test_ready_returns_503_when_catalog_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(main, "interaction_repository", InMemoryInteractionRepository())
    monkeypatch.setattr(main, "item_catalog", UnavailableCatalog())

    response = TestClient(main.app).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "Item catalog is unavailable."


def test_ready_returns_503_when_impression_repository_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(main, "interaction_repository", InMemoryInteractionRepository())
    monkeypatch.setattr(main, "impression_repository", UnavailableImpressionRepository())

    response = TestClient(main.app).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "Impression repository is unavailable."


def test_catalog_metadata_enriches_and_filters_recommendations(monkeypatch) -> None:
    repository = InMemoryInteractionRepository()
    for interaction in [
        Interaction("target", "shared", InteractionType.LIKE),
        Interaction("neighbor", "shared", InteractionType.LIKE),
        Interaction("neighbor", "available", InteractionType.CLICK),
        Interaction("neighbor", "retired", InteractionType.PURCHASE),
    ]:
        repository.add(interaction)
    catalog = InMemoryItemCatalog()
    catalog.add_many(
        (
            Item("shared", "Shared signal", "signals"),
            Item("available", "Available course", "courses"),
            Item("retired", "Retired course", "courses", False),
        )
    )
    monkeypatch.setattr(main, "interaction_repository", repository)
    monkeypatch.setattr(main, "item_catalog", catalog)
    client = TestClient(main.app)

    response = client.get("/recommendations/target")

    assert response.status_code == 200
    assert response.json()[0] == {
        "item_id": "available",
        "title": "Available course",
        "category": "courses",
        "score": response.json()[0]["score"],
        "reason": "similar_users",
        "supporting_item_count": 1,
    }
    assert all(item["item_id"] != "retired" for item in response.json())


def test_category_diversity_uses_separate_cache_version_and_logs_served_order(
    monkeypatch,
) -> None:
    repository = InMemoryInteractionRepository()
    for interaction in [
        Interaction("u1", "sports-1", InteractionType.PURCHASE),
        Interaction("u2", "sports-1", InteractionType.PURCHASE),
        Interaction("u1", "sports-2", InteractionType.PURCHASE),
        Interaction("u2", "sports-2", InteractionType.LIKE),
        Interaction("u1", "news-1", InteractionType.PURCHASE),
        Interaction("u2", "news-1", InteractionType.CLICK),
        Interaction("u1", "music-1", InteractionType.PURCHASE),
    ]:
        repository.add(interaction)
    catalog = InMemoryItemCatalog()
    catalog.add_many(
        (
            Item("sports-1", "Sports one", "sports"),
            Item("sports-2", "Sports two", "sports"),
            Item("news-1", "News one", "news"),
            Item("music-1", "Music one", "music"),
        )
    )
    impressions = InMemoryImpressionRepository()
    monkeypatch.setattr(main, "interaction_repository", repository)
    monkeypatch.setattr(main, "item_catalog", catalog)
    monkeypatch.setattr(main, "impression_repository", impressions)
    client = TestClient(main.app)

    relevance = client.get("/recommendations/new-user?strategy=popular&limit=3")
    diversified = client.get(
        "/recommendations/new-user?strategy=popular&limit=3&diversity=category"
    )

    assert [item["item_id"] for item in relevance.json()] == [
        "sports-1",
        "sports-2",
        "news-1",
    ]
    assert [item["item_id"] for item in diversified.json()] == [
        "sports-1",
        "news-1",
        "music-1",
    ]
    model_version = diversified.headers["X-Recommendation-Model-Version"]
    assert model_version.endswith("category-coverage-v1-pool5x")
    request_id = diversified.headers["X-Recommendation-Request-ID"]
    logged = impressions.list_by_request(request_id)
    assert [item.item_id for item in logged] == ["sports-1", "news-1", "music-1"]
    assert all(item.model_version == model_version for item in logged)


def test_recommendations_record_ranked_impressions_and_attribute_feedback(
    monkeypatch,
) -> None:
    repository = InMemoryInteractionRepository()
    repository.add(Interaction("other", "candidate", InteractionType.PURCHASE))
    impressions = InMemoryImpressionRepository()
    monkeypatch.setattr(main, "interaction_repository", repository)
    monkeypatch.setattr(main, "impression_repository", impressions)
    client = TestClient(main.app)

    recommendation_response = client.get("/recommendations/new-user")

    assert recommendation_response.status_code == 200
    request_id = recommendation_response.headers["X-Recommendation-Request-ID"]
    logged = impressions.list_by_request(request_id)
    assert len(logged) == 1
    assert logged[0].user_id == "new-user"
    assert logged[0].item_id == "candidate"
    assert logged[0].rank == 1
    assert logged[0].strategy == "personalized"
    assert logged[0].source == "live"

    feedback = client.post(
        "/interactions",
        headers={"Idempotency-Key": "attributed-click-1"},
        json={
            "user_id": "new-user",
            "item_id": "candidate",
            "interaction_type": "click",
            "recommendation_request_id": request_id,
        },
    )

    assert feedback.status_code == 201
    assert feedback.json()["recommendation_request_id"] == request_id
    assert repository.list_all()[-1].recommendation_request_id == request_id


def test_interaction_rejects_attribution_to_an_unexposed_item(monkeypatch) -> None:
    repository = InMemoryInteractionRepository()
    repository.add(Interaction("other", "candidate", InteractionType.PURCHASE))
    monkeypatch.setattr(main, "interaction_repository", repository)
    client = TestClient(main.app)
    recommendation_response = client.get("/recommendations/new-user")
    request_id = recommendation_response.headers["X-Recommendation-Request-ID"]

    response = client.post(
        "/interactions",
        json={
            "user_id": "new-user",
            "item_id": "not-shown",
            "interaction_type": "click",
            "recommendation_request_id": request_id,
        },
    )

    assert response.status_code == 422
    assert repository.list_all() == (Interaction("other", "candidate", InteractionType.PURCHASE),)


def test_item_upsert_invalidates_rankings_and_can_deactivate_candidate(monkeypatch) -> None:
    repository = InMemoryInteractionRepository()
    repository.add(Interaction("other", "candidate", InteractionType.PURCHASE))
    catalog = InMemoryItemCatalog()
    catalog.upsert(Item("candidate", "Candidate", "books"))
    monkeypatch.setattr(main, "interaction_repository", repository)
    monkeypatch.setattr(main, "item_catalog", catalog)
    client = TestClient(main.app)

    assert client.get("/recommendations/new-user").json()[0]["item_id"] == "candidate"
    updated = client.put(
        "/items/candidate",
        json={"title": "Candidate", "category": "books", "is_active": False},
    )

    assert updated.status_code == 200
    assert updated.json()["is_active"] is False
    assert client.get("/items/candidate").json()["title"] == "Candidate"
    assert client.get("/recommendations/new-user").json() == []


def test_unknown_item_returns_404() -> None:
    response = TestClient(main.app).get("/items/missing")
    assert response.status_code == 404


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
    assert personalized.json()[0]["reason"] == "similar_users"
    assert personalized.json()[0]["supporting_item_count"] == 1
    assert popular.status_code == 200
    assert [item["item_id"] for item in popular.json()] == ["popular", "niche"]
    assert all(item["reason"] == "popular" for item in popular.json())
    assert all(item["supporting_item_count"] == 0 for item in popular.json())


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


def test_interaction_idempotency_replays_without_duplicate(monkeypatch) -> None:
    repository = InMemoryInteractionRepository()
    monkeypatch.setattr(main, "interaction_repository", repository)
    client = TestClient(main.app)
    payload = {
        "user_id": "u1",
        "item_id": "item-a",
        "interaction_type": "click",
        "occurred_at": "2026-10-04T18:00:00-05:00",
    }
    headers = {"Idempotency-Key": "client-event-123"}

    first = client.post("/interactions", json=payload, headers=headers)
    replay = client.post("/interactions", json=payload, headers=headers)

    assert first.status_code == 201
    assert first.json()["status"] == "recorded"
    assert first.json()["idempotency_key"] == "client-event-123"
    assert first.json()["occurred_at"] == "2026-10-04T23:00:00Z"
    assert replay.status_code == 200
    assert replay.json()["status"] == "replayed"
    assert repository.list_all()[0].occurred_at.isoformat() == "2026-10-04T23:00:00+00:00"


def test_interaction_rejects_timestamp_without_timezone(monkeypatch) -> None:
    monkeypatch.setattr(main, "interaction_repository", InMemoryInteractionRepository())

    response = TestClient(main.app).post(
        "/interactions",
        json={
            "user_id": "u1",
            "item_id": "item-a",
            "interaction_type": "click",
            "occurred_at": "2026-10-04T18:00:00",
        },
    )

    assert response.status_code == 422
    assert "timezone offset" in response.text


def test_interaction_idempotency_rejects_different_payload(monkeypatch) -> None:
    repository = InMemoryInteractionRepository()
    monkeypatch.setattr(main, "interaction_repository", repository)
    client = TestClient(main.app)
    headers = {"Idempotency-Key": "client-event-123"}

    first = client.post(
        "/interactions",
        json={"user_id": "u1", "item_id": "item-a", "interaction_type": "click"},
        headers=headers,
    )
    conflict = client.post(
        "/interactions",
        json={"user_id": "u1", "item_id": "item-b", "interaction_type": "click"},
        headers=headers,
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert "different interaction" in conflict.json()["detail"]
    assert len(repository.list_all()) == 1


def test_api_does_not_serve_results_from_an_older_model_version(monkeypatch) -> None:
    repository = InMemoryInteractionRepository()
    for interaction in [
        Interaction("target", "shared", InteractionType.LIKE),
        Interaction("neighbor", "shared", InteractionType.LIKE),
        Interaction("neighbor", "fresh", InteractionType.PURCHASE),
    ]:
        repository.add(interaction)
    monkeypatch.setattr(main, "interaction_repository", repository)
    main.recommendation_cache.set(
        "target",
        "personalized",
        "user-cosine-v0",
        10,
        [Recommendation("stale", 999.0)],
    )

    response = TestClient(main.app).get("/recommendations/target")

    assert response.status_code == 200
    assert [item["item_id"] for item in response.json()] == ["fresh"]
