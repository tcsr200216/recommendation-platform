from sqlalchemy import create_engine

from app.catalog import InMemoryItemCatalog, Item, SqlItemCatalog


def test_memory_catalog_upserts_and_lists_in_stable_order() -> None:
    catalog = InMemoryItemCatalog()
    catalog.upsert(Item("item-b", "Old title", "books"))
    catalog.upsert(Item("item-a", "Course A", "courses"))
    catalog.upsert(Item("item-b", "New title", "books", False))

    assert catalog.get("missing") is None
    assert catalog.get("item-b") == Item("item-b", "New title", "books", False)
    assert [item.item_id for item in catalog.list_all()] == ["item-a", "item-b"]


def test_sql_catalog_persists_upserts_and_active_state() -> None:
    catalog = SqlItemCatalog(create_engine("sqlite+pysqlite:///:memory:"))
    catalog.create_schema()
    catalog.upsert(Item("item-a", "First title", "books"))
    catalog.upsert(Item("item-a", "Updated title", "courses", False))

    assert catalog.is_ready()
    assert catalog.get("item-a") == Item("item-a", "Updated title", "courses", False)
    assert catalog.list_all() == (Item("item-a", "Updated title", "courses", False),)
