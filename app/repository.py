from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC
from typing import Protocol

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.recommender import Interaction, InteractionType


class InteractionRepository(Protocol):
    """Persistence boundary for user-item interactions."""

    @property
    def backend(self) -> str:
        """Return a short name for the active persistence backend."""
        ...

    def add(self, interaction: Interaction, idempotency_key: str | None = None) -> bool:
        """Persist once, returning False for an exact idempotent replay."""
        ...

    def list_all(self) -> Sequence[Interaction]:
        """Return a stable snapshot of all recorded interactions."""
        ...

    def is_ready(self) -> bool:
        """Return whether the persistence dependency can serve traffic."""
        ...


class InMemoryInteractionRepository:
    """Process-local repository used by the MVP and tests."""

    def __init__(self) -> None:
        self._interactions: list[Interaction] = []
        self._idempotency_records: dict[str, Interaction] = {}

    @property
    def backend(self) -> str:
        return "memory"

    def add(self, interaction: Interaction, idempotency_key: str | None = None) -> bool:
        if idempotency_key is not None:
            existing = self._idempotency_records.get(idempotency_key)
            if existing is not None:
                if existing != interaction:
                    raise IdempotencyConflictError(idempotency_key)
                return False
            self._idempotency_records[idempotency_key] = interaction
        self._interactions.append(interaction)
        return True

    def list_all(self) -> tuple[Interaction, ...]:
        return tuple(self._interactions)

    def is_ready(self) -> bool:
        return True


metadata = MetaData()
interactions_table = Table(
    "interactions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String(128), nullable=False, index=True),
    Column("item_id", String(128), nullable=False, index=True),
    Column("interaction_type", String(32), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=True),
    Column("recommendation_request_id", String(36), nullable=True),
)
idempotency_table = Table(
    "interaction_idempotency",
    metadata,
    Column("idempotency_key", String(128), primary_key=True),
    Column("user_id", String(128), nullable=False),
    Column("item_id", String(128), nullable=False),
    Column("interaction_type", String(32), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=True),
    Column("recommendation_request_id", String(36), nullable=True),
)


class IdempotencyConflictError(ValueError):
    """Raised when a key is reused for a different interaction payload."""

    def __init__(self, idempotency_key: str) -> None:
        super().__init__(f"Idempotency key '{idempotency_key}' is already used.")


class SqlInteractionRepository:
    """SQLAlchemy-backed interaction repository for PostgreSQL deployments."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @property
    def backend(self) -> str:
        return "sql"

    @classmethod
    def from_url(cls, database_url: str) -> SqlInteractionRepository:
        return cls(create_engine(database_url, pool_pre_ping=True))

    def create_schema(self) -> None:
        metadata.create_all(self._engine)
        if self._engine.dialect.name == "postgresql":
            with self._engine.begin() as connection:
                for table_name in ("interactions", "interaction_idempotency"):
                    connection.execute(
                        text(
                            f"ALTER TABLE {table_name} "
                            "ADD COLUMN IF NOT EXISTS occurred_at TIMESTAMPTZ"
                        )
                    )
                    connection.execute(
                        text(
                            f"ALTER TABLE {table_name} "
                            "ADD COLUMN IF NOT EXISTS recommendation_request_id VARCHAR(36)"
                        )
                    )

    def add(self, interaction: Interaction, idempotency_key: str | None = None) -> bool:
        try:
            with self._engine.begin() as connection:
                if idempotency_key is not None:
                    connection.execute(
                        insert(idempotency_table).values(
                            idempotency_key=idempotency_key,
                            user_id=interaction.user_id,
                            item_id=interaction.item_id,
                            interaction_type=interaction.interaction_type.value,
                            occurred_at=interaction.occurred_at,
                            recommendation_request_id=interaction.recommendation_request_id,
                        )
                    )
                connection.execute(
                    insert(interactions_table).values(
                        user_id=interaction.user_id,
                        item_id=interaction.item_id,
                        interaction_type=interaction.interaction_type.value,
                        occurred_at=interaction.occurred_at,
                        recommendation_request_id=interaction.recommendation_request_id,
                    )
                )
            return True
        except IntegrityError:
            if idempotency_key is None:
                raise

        statement = select(
            idempotency_table.c.user_id,
            idempotency_table.c.item_id,
            idempotency_table.c.interaction_type,
            idempotency_table.c.occurred_at,
            idempotency_table.c.recommendation_request_id,
        ).where(idempotency_table.c.idempotency_key == idempotency_key)
        with self._engine.connect() as connection:
            row = connection.execute(statement).one_or_none()

        if row is None:
            raise SQLAlchemyError("Idempotency conflict occurred without a durable record.")
        occurred_at = row.occurred_at
        if occurred_at is not None and occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        existing = Interaction(
            row.user_id,
            row.item_id,
            InteractionType(row.interaction_type),
            occurred_at,
            row.recommendation_request_id,
        )
        if existing != interaction:
            raise IdempotencyConflictError(idempotency_key)
        return False

    def add_many(self, interactions: Sequence[Interaction]) -> None:
        """Insert a fixture batch in one transaction, or roll it all back."""
        rows = [
            {
                "user_id": interaction.user_id,
                "item_id": interaction.item_id,
                "interaction_type": interaction.interaction_type.value,
                "occurred_at": interaction.occurred_at,
                "recommendation_request_id": interaction.recommendation_request_id,
            }
            for interaction in interactions
        ]
        if not rows:
            return
        with self._engine.begin() as connection:
            connection.execute(insert(interactions_table), rows)

    def list_all(self) -> tuple[Interaction, ...]:
        statement = select(
            interactions_table.c.user_id,
            interactions_table.c.item_id,
            interactions_table.c.interaction_type,
            interactions_table.c.occurred_at,
            interactions_table.c.recommendation_request_id,
        ).order_by(interactions_table.c.id)

        with self._engine.connect() as connection:
            rows = connection.execute(statement).all()

        return tuple(
            Interaction(
                user_id=row.user_id,
                item_id=row.item_id,
                interaction_type=InteractionType(row.interaction_type),
                occurred_at=(
                    row.occurred_at.replace(tzinfo=UTC)
                    if row.occurred_at is not None and row.occurred_at.tzinfo is None
                    else row.occurred_at
                ),
                recommendation_request_id=row.recommendation_request_id,
            )
            for row in rows
        )

    def is_ready(self) -> bool:
        try:
            with self._engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except SQLAlchemyError:
            return False


def build_interaction_repository(database_url: str | None) -> InteractionRepository:
    """Select the runtime repository without coupling API handlers to storage."""
    if not database_url:
        return InMemoryInteractionRepository()

    repository = SqlInteractionRepository.from_url(database_url)
    repository.create_schema()
    return repository
