from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from app.recommender import Interaction, InteractionType
from app.repository import (
    IdempotencyConflictError,
    InMemoryInteractionRepository,
    SqlInteractionRepository,
    build_interaction_repository,
)


def test_in_memory_repository_stores_interactions_in_order() -> None:
    repository = InMemoryInteractionRepository()
    first = Interaction("u1", "item-a", InteractionType.VIEW)
    second = Interaction("u2", "item-b", InteractionType.PURCHASE)

    repository.add(first)
    repository.add(second)

    assert repository.list_all() == (first, second)


def test_interaction_normalizes_event_time_and_rejects_naive_values() -> None:
    normalized = Interaction(
        "u1",
        "item-a",
        InteractionType.VIEW,
        datetime.fromisoformat("2026-10-04T18:00:00-05:00"),
    )

    assert normalized.occurred_at == datetime(2026, 10, 4, 23, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="timezone offset"):
        Interaction(
            "u1",
            "item-a",
            InteractionType.VIEW,
            datetime(2026, 10, 4, 18, 0, tzinfo=UTC).replace(tzinfo=None),
        )


def test_repository_returns_snapshot_not_internal_mutable_list() -> None:
    repository = InMemoryInteractionRepository()
    interaction = Interaction("u1", "item-a", InteractionType.CLICK)
    repository.add(interaction)

    snapshot = repository.list_all()

    assert isinstance(snapshot, tuple)
    assert snapshot == (interaction,)


def test_in_memory_repository_replays_same_key_without_duplicate() -> None:
    repository = InMemoryInteractionRepository()
    interaction = Interaction("u1", "item-a", InteractionType.CLICK)

    assert repository.add(interaction, "event-123") is True
    assert repository.add(interaction, "event-123") is False
    assert repository.list_all() == (interaction,)


def test_in_memory_repository_rejects_key_reuse_with_different_payload() -> None:
    repository = InMemoryInteractionRepository()
    repository.add(Interaction("u1", "item-a", InteractionType.CLICK), "event-123")

    with pytest.raises(IdempotencyConflictError):
        repository.add(Interaction("u1", "item-b", InteractionType.CLICK), "event-123")


def test_sql_repository_round_trips_interactions() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    repository = SqlInteractionRepository(engine)
    repository.create_schema()

    first = Interaction(
        "u1",
        "item-a",
        InteractionType.CLICK,
        datetime(2026, 10, 4, 18, 0, tzinfo=UTC),
    )
    second = Interaction("u2", "item-b", InteractionType.PURCHASE)
    repository.add(first)
    repository.add(second)

    assert repository.list_all() == (first, second)
    assert repository.is_ready() is True


def test_sql_repository_atomically_deduplicates_idempotency_key() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    repository = SqlInteractionRepository(engine)
    repository.create_schema()
    interaction = Interaction(
        "u1",
        "item-a",
        InteractionType.PURCHASE,
        datetime(2026, 10, 4, 18, 0, tzinfo=UTC),
    )

    assert repository.add(interaction, "checkout-123") is True
    assert repository.add(interaction, "checkout-123") is False
    assert repository.list_all() == (interaction,)

    with pytest.raises(IdempotencyConflictError):
        repository.add(Interaction("u1", "item-b", InteractionType.PURCHASE), "checkout-123")
    assert repository.list_all() == (interaction,)


def test_idempotency_key_rejects_a_different_event_timestamp() -> None:
    repository = InMemoryInteractionRepository()
    first = Interaction(
        "u1", "item-a", InteractionType.CLICK, datetime(2026, 10, 4, 18, 0, tzinfo=UTC)
    )
    changed_time = Interaction(
        "u1", "item-a", InteractionType.CLICK, datetime(2026, 10, 4, 18, 1, tzinfo=UTC)
    )

    repository.add(first, "event-with-time")
    with pytest.raises(IdempotencyConflictError):
        repository.add(changed_time, "event-with-time")


def test_repository_factory_uses_memory_when_database_url_is_absent() -> None:
    repository = build_interaction_repository(None)

    assert isinstance(repository, InMemoryInteractionRepository)
    assert repository.backend == "memory"
    assert repository.is_ready() is True


def test_repository_factory_uses_sql_when_database_url_is_configured() -> None:
    repository = build_interaction_repository("sqlite+pysqlite:///:memory:")

    assert isinstance(repository, SqlInteractionRepository)
    assert repository.backend == "sql"
    assert repository.is_ready() is True
