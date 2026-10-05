from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import Boolean, Column, MetaData, String, Table, create_engine, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


@dataclass(frozen=True, slots=True)
class Item:
    item_id: str
    title: str
    category: str
    is_active: bool = True


class ItemCatalog(Protocol):
    @property
    def backend(self) -> str: ...

    def upsert(self, item: Item) -> None: ...

    def get(self, item_id: str) -> Item | None: ...

    def list_all(self) -> Sequence[Item]: ...

    def is_ready(self) -> bool: ...


class InMemoryItemCatalog:
    def __init__(self) -> None:
        self._items: dict[str, Item] = {}

    @property
    def backend(self) -> str:
        return "memory"

    def upsert(self, item: Item) -> None:
        self._items[item.item_id] = item

    def get(self, item_id: str) -> Item | None:
        return self._items.get(item_id)

    def list_all(self) -> tuple[Item, ...]:
        return tuple(self._items[item_id] for item_id in sorted(self._items))

    def add_many(self, items: Sequence[Item]) -> None:
        self._items.update((item.item_id, item) for item in items)

    def is_ready(self) -> bool:
        return True


catalog_metadata = MetaData()
items_table = Table(
    "items",
    catalog_metadata,
    Column("item_id", String(128), primary_key=True),
    Column("title", String(256), nullable=False),
    Column("category", String(128), nullable=False, index=True),
    Column("is_active", Boolean, nullable=False),
)


class SqlItemCatalog:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @property
    def backend(self) -> str:
        return "sql"

    @classmethod
    def from_url(cls, database_url: str) -> SqlItemCatalog:
        return cls(create_engine(database_url, pool_pre_ping=True))

    def create_schema(self) -> None:
        catalog_metadata.create_all(self._engine)

    def upsert(self, item: Item) -> None:
        values = {
            "item_id": item.item_id,
            "title": item.title,
            "category": item.category,
            "is_active": item.is_active,
        }
        if self._engine.dialect.name == "postgresql":
            statement = postgresql_insert(items_table).values(**values)
        elif self._engine.dialect.name == "sqlite":
            statement = sqlite_insert(items_table).values(**values)
        else:
            raise ValueError("Item catalog supports PostgreSQL and SQLite only.")
        statement = statement.on_conflict_do_update(
            index_elements=[items_table.c.item_id],
            set_={
                "title": statement.excluded.title,
                "category": statement.excluded.category,
                "is_active": statement.excluded.is_active,
            },
        )
        with self._engine.begin() as connection:
            connection.execute(statement)

    def get(self, item_id: str) -> Item | None:
        statement = select(items_table).where(items_table.c.item_id == item_id)
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().one_or_none()
        return None if row is None else self._item_from_row(row)

    def list_all(self) -> tuple[Item, ...]:
        with self._engine.connect() as connection:
            rows = connection.execute(select(items_table).order_by(items_table.c.item_id)).mappings()
            return tuple(self._item_from_row(row) for row in rows)

    def add_many(self, items: Sequence[Item]) -> None:
        if not items:
            return
        with self._engine.begin() as connection:
            connection.execute(
                items_table.insert(),
                [
                    {
                        "item_id": item.item_id,
                        "title": item.title,
                        "category": item.category,
                        "is_active": item.is_active,
                    }
                    for item in items
                ],
            )

    def is_ready(self) -> bool:
        try:
            with self._engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except SQLAlchemyError:
            return False

    @staticmethod
    def _item_from_row(row) -> Item:
        return Item(
            item_id=row.item_id,
            title=row.title,
            category=row.category,
            is_active=row.is_active,
        )


def build_item_catalog(database_url: str | None) -> ItemCatalog:
    if not database_url:
        return InMemoryItemCatalog()
    catalog = SqlItemCatalog.from_url(database_url)
    catalog.create_schema()
    return catalog
