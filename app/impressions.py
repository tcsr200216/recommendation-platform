from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    insert,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


@dataclass(frozen=True, slots=True)
class RecommendationImpression:
    request_id: str
    user_id: str
    item_id: str
    rank: int
    strategy: str
    model_version: str
    source: str
    served_at: datetime


class ImpressionRepository(Protocol):
    @property
    def backend(self) -> str: ...

    def record_batch(self, impressions: Sequence[RecommendationImpression]) -> None: ...

    def contains(self, request_id: str, user_id: str, item_id: str) -> bool: ...

    def list_by_request(self, request_id: str) -> Sequence[RecommendationImpression]: ...

    def is_ready(self) -> bool: ...


class InMemoryImpressionRepository:
    def __init__(self) -> None:
        self._impressions: list[RecommendationImpression] = []

    @property
    def backend(self) -> str:
        return "memory"

    def record_batch(self, impressions: Sequence[RecommendationImpression]) -> None:
        self._impressions.extend(impressions)

    def contains(self, request_id: str, user_id: str, item_id: str) -> bool:
        return any(
            impression.request_id == request_id
            and impression.user_id == user_id
            and impression.item_id == item_id
            for impression in self._impressions
        )

    def list_by_request(self, request_id: str) -> tuple[RecommendationImpression, ...]:
        return tuple(
            impression
            for impression in self._impressions
            if impression.request_id == request_id
        )

    def is_ready(self) -> bool:
        return True


impression_metadata = MetaData()
impressions_table = Table(
    "recommendation_impressions",
    impression_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("request_id", String(36), nullable=False, index=True),
    Column("user_id", String(128), nullable=False, index=True),
    Column("item_id", String(128), nullable=False),
    Column("rank", Integer, nullable=False),
    Column("strategy", String(32), nullable=False),
    Column("model_version", String(128), nullable=False),
    Column("source", String(16), nullable=False),
    Column("served_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("request_id", "rank", name="uq_impression_request_rank"),
    Index("ix_impression_attribution", "request_id", "user_id", "item_id"),
)


class SqlImpressionRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @property
    def backend(self) -> str:
        return "sql"

    @classmethod
    def from_url(cls, database_url: str) -> SqlImpressionRepository:
        return cls(create_engine(database_url, pool_pre_ping=True))

    def create_schema(self) -> None:
        impression_metadata.create_all(self._engine)

    def record_batch(self, impressions: Sequence[RecommendationImpression]) -> None:
        if not impressions:
            return
        with self._engine.begin() as connection:
            connection.execute(
                insert(impressions_table),
                [
                    {
                        "request_id": impression.request_id,
                        "user_id": impression.user_id,
                        "item_id": impression.item_id,
                        "rank": impression.rank,
                        "strategy": impression.strategy,
                        "model_version": impression.model_version,
                        "source": impression.source,
                        "served_at": impression.served_at,
                    }
                    for impression in impressions
                ],
            )

    def contains(self, request_id: str, user_id: str, item_id: str) -> bool:
        statement = (
            select(impressions_table.c.id)
            .where(
                impressions_table.c.request_id == request_id,
                impressions_table.c.user_id == user_id,
                impressions_table.c.item_id == item_id,
            )
            .limit(1)
        )
        with self._engine.connect() as connection:
            return connection.execute(statement).first() is not None

    def list_by_request(self, request_id: str) -> tuple[RecommendationImpression, ...]:
        statement = (
            select(impressions_table)
            .where(impressions_table.c.request_id == request_id)
            .order_by(impressions_table.c.rank)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(
            RecommendationImpression(
                request_id=row.request_id,
                user_id=row.user_id,
                item_id=row.item_id,
                rank=row.rank,
                strategy=row.strategy,
                model_version=row.model_version,
                source=row.source,
                served_at=(
                    row.served_at.replace(tzinfo=UTC)
                    if row.served_at.tzinfo is None
                    else row.served_at
                ),
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


def build_impression_repository(database_url: str | None) -> ImpressionRepository:
    if not database_url:
        return InMemoryImpressionRepository()
    repository = SqlImpressionRepository.from_url(database_url)
    repository.create_schema()
    return repository
