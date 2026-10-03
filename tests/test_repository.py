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

    first = Interaction("u1", "item-a", InteractionType.CLICK)
    second = Interaction("u2", "item-b", InteractionType.PURCHASE)
    repository.add(first)
    repository.add(second)

    assert repository.list_all() == (first, second)
    assert repository.is_ready() is True


def test_sql_repository_atomically_deduplicates_idempotency_key() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    repository = SqlInteractionRepository(engine)
    repository.create_schema()
    interaction = Interaction("u1", "item-a", InteractionType.PURCHASE)

    assert repository.add(interaction, "checkout-123") is True
    assert repository.add(interaction, "checkout-123") is False
    assert repository.list_all() == (interaction,)

    with pytest.raises(IdempotencyConflictError):
        repository.add(
            Interaction("u1", "item-b", InteractionType.PURCHASE), "checkout-123"
        )
    assert repository.list_all() == (interaction,)


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
