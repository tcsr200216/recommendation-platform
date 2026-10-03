from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from app.recommender import Interaction, InteractionType
from app.repository import SqlInteractionRepository
from scripts.seed_sample_data import load_sample_interactions, seed_repository


def new_repository() -> SqlInteractionRepository:
    repository = SqlInteractionRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    return repository


def test_load_real_fixture_preserves_duplicate_event_signal() -> None:
    events = load_sample_interactions()
    assert len(events) > 2
    assert events[0] == Interaction("alice", "book-python", InteractionType.PURCHASE)
    assert sum(e.user_id == "alice" and e.item_id == "course-ml" for e in events) == 2


def test_seed_is_atomic_and_repeatable() -> None:
    repository = new_repository()
    fixture = load_sample_interactions()
    assert seed_repository(repository, fixture).startswith("Inserted ")
    assert repository.list_all() == fixture
    assert seed_repository(repository, fixture).startswith("Sample interactions already present")
    assert repository.list_all() == fixture


def test_seed_refuses_unrelated_existing_interactions_without_mutation() -> None:
    repository = new_repository()
    original = Interaction("real-user", "real-item", InteractionType.CLICK)
    repository.add(original)

    with pytest.raises(RuntimeError, match="refusing to merge"):
        seed_repository(repository, load_sample_interactions())
    assert repository.list_all() == (original,)


def test_seed_rejects_empty_fixture() -> None:
    with pytest.raises(ValueError, match="empty fixture"):
        seed_repository(new_repository(), ())


def test_fixture_validation_rejects_invalid_shape(tmp_path: Path) -> None:
    invalid = tmp_path / "broken.json"
    invalid.write_text('[{"user_id":"alice","item_id":"book"}]', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid fields"):
        load_sample_interactions(invalid)


def test_batch_write_rolls_back_on_constraint_violation() -> None:
    repository = new_repository()
    valid = Interaction("alice", "book", InteractionType.LIKE)
    invalid = Interaction(None, "item", InteractionType.VIEW)
    with pytest.raises(IntegrityError):
        repository.add_many((valid, invalid))
    assert repository.list_all() == ()
