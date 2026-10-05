from datetime import UTC, datetime

from sqlalchemy import create_engine

from app.impressions import (
    InMemoryImpressionRepository,
    RecommendationImpression,
    SqlImpressionRepository,
    build_impression_repository,
)


def _impression(rank: int = 1) -> RecommendationImpression:
    return RecommendationImpression(
        request_id="2b16392a-b129-4f7f-8e3c-bf9a970b8754",
        user_id="user-1",
        item_id=f"item-{rank}",
        rank=rank,
        strategy="personalized",
        model_version="model-v1",
        source="live",
        served_at=datetime(2026, 10, 5, 19, 0, tzinfo=UTC),
    )


def test_in_memory_repository_records_ordered_batch_and_checks_attribution() -> None:
    repository = InMemoryImpressionRepository()
    repository.record_batch((_impression(1), _impression(2)))

    assert repository.list_by_request(_impression().request_id) == (
        _impression(1),
        _impression(2),
    )
    assert repository.contains(_impression().request_id, "user-1", "item-2") is True
    assert repository.contains(_impression().request_id, "other-user", "item-2") is False


def test_sql_repository_round_trips_batch_atomically() -> None:
    repository = SqlImpressionRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    repository.record_batch((_impression(1), _impression(2)))

    assert repository.list_by_request(_impression().request_id) == (
        _impression(1),
        _impression(2),
    )
    assert repository.contains(_impression().request_id, "user-1", "item-1") is True
    assert repository.is_ready() is True


def test_factory_uses_configured_backend() -> None:
    assert isinstance(build_impression_repository(None), InMemoryImpressionRepository)
    assert isinstance(
        build_impression_repository("sqlite+pysqlite:///:memory:"),
        SqlImpressionRepository,
    )
