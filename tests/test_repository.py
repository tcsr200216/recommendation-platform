from app.recommender import Interaction, InteractionType
from app.repository import InMemoryInteractionRepository


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
