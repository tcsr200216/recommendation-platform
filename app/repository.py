from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, insert, select
from sqlalchemy.engine import Engine

from app.recommender import Interaction, InteractionType


class InteractionRepository(Protocol):
    """Persistence boundary for user-item interactions."""

    def add(self, interaction: Interaction) -> None:
        """Persist one interaction."""
        ...

    def list_all(self) -> Sequence[Interaction]:
        """Return a stable snapshot of all recorded interactions."""
        ...


class InMemoryInteractionRepository:
    """Process-local repository used by the MVP and tests."""

    def __init__(self) -> None:
        self._interactions: list[Interaction] = []

    def add(self, interaction: Interaction) -> None:
        self._interactions.append(interaction)

    def list_all(self) -> tuple[Interaction, ...]:
        return tuple(self._interactions)


metadata = MetaData()
interactions_table = Table(
    "interactions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String(128), nullable=False, index=True),
    Column("item_id", String(128), nullable=False, index=True),
    Column("interaction_type", String(32), nullable=False),
)


class SqlInteractionRepository:
    """SQLAlchemy-backed interaction repository for PostgreSQL deployments."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @classmethod
    def from_url(cls, database_url: str) -> "SqlInteractionRepository":
        return cls(create_engine(database_url, pool_pre_ping=True))

    def create_schema(self) -> None:
        metadata.create_all(self._engine)

    def add(self, interaction: Interaction) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(interactions_table).values(
                    user_id=interaction.user_id,
                    item_id=interaction.item_id,
                    interaction_type=interaction.interaction_type.value,
                )
            )

    def list_all(self) -> tuple[Interaction, ...]:
        statement = select(
            interactions_table.c.user_id,
            interactions_table.c.item_id,
            interactions_table.c.interaction_type,
        ).order_by(interactions_table.c.id)

        with self._engine.connect() as connection:
            rows = connection.execute(statement).all()

        return tuple(
            Interaction(
                user_id=row.user_id,
                item_id=row.item_id,
                interaction_type=InteractionType(row.interaction_type),
            )
            for row in rows
        )
