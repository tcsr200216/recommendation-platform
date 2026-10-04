"""Guarded, repeatable sample interaction seeding for the SQL backend.

Run only against a disposable demo database:
    docker compose exec api python -m scripts.seed_sample_data

The seeded data is never sent to production by this command automatically.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.recommender import Interaction, InteractionType
from app.repository import SqlInteractionRepository

SAMPLE_PATH = Path(__file__).resolve().parents[1] / "data" / "sample_interactions.json"


def load_sample_interactions(path: Path = SAMPLE_PATH) -> tuple[Interaction, ...]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Sample file must be a nonempty JSON array.")

    interactions = []
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or set(row) != {
            "user_id",
            "item_id",
            "interaction_type",
            "occurred_at",
        }:
            raise ValueError(f"Sample event {number} has invalid fields.")
        user_id, item_id = row["user_id"], row["item_id"]
        if (
            not isinstance(user_id, str)
            or not 1 <= len(user_id.strip()) <= 128
            or not isinstance(item_id, str)
            or not 1 <= len(item_id.strip()) <= 128
        ):
            raise ValueError(f"Sample event {number} has invalid identifiers.")
        try:
            occurred_at = datetime.fromisoformat(row["occurred_at"])
        except (AttributeError, ValueError) as exc:
            raise ValueError(f"Sample event {number} has an invalid occurred_at.") from exc
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError(f"Sample event {number} occurred_at must include a timezone.")
        interactions.append(
            Interaction(
                user_id,
                item_id,
                InteractionType(row["interaction_type"]),
                occurred_at.astimezone(UTC),
            )
        )
    return tuple(interactions)


def seed_repository(
    repository: SqlInteractionRepository, interactions: tuple[Interaction, ...]
) -> str:
    """Seed only an empty DB; exact existing fixture is an idempotent no-op."""
    if not interactions:
        raise ValueError("Refusing to seed an empty fixture.")
    existing = repository.list_all()
    if existing == interactions:
        return "Sample interactions already present; no changes made."
    if existing:
        raise RuntimeError(
            "Database contains other interactions; refusing to merge or overwrite sample data."
        )
    repository.add_many(interactions)
    return f"Inserted {len(interactions)} sample interactions."


def main() -> None:
    if not settings.database_url:
        raise SystemExit(
            "DATABASE_URL is required: a standalone process cannot seed the API's "
            "process-local in-memory repository."
        )
    repository = SqlInteractionRepository.from_url(settings.database_url)
    repository.create_schema()
    print(seed_repository(repository, load_sample_interactions()))


if __name__ == "__main__":
    main()
