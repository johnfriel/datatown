from __future__ import annotations

import pytest

import datatown.crunchbase.inspect as crunchbase_inspect
from datatown.config import DatabaseConfig
from datatown.crunchbase.inspect import (
    ColumnInfo,
    CrunchbaseInventory,
    RelationInfo,
    SchemaInfo,
    format_bytes,
    inspect_crunchbase,
    render_inventory,
)


class FakeCursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class FakeTransaction:
    def __enter__(self) -> None:
        return None

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[object] = []

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        return None

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()

    def execute(self, statement, _parameters=None) -> FakeCursor:
        self.statements.append(statement)
        rows_by_query = {
            crunchbase_inspect.SCHEMAS_QUERY: [("public", 1), ("storage", 8)],
            crunchbase_inspect.RELATIONS_QUERY: [
                ("public", "organizations", "r", 12, 1024, 0, 8192)
            ],
            crunchbase_inspect.COLUMNS_QUERY: [
                ("organizations", 1, "id", "uuid", True),
                ("organizations", 2, "domain", "character varying", False),
            ],
            crunchbase_inspect.PRIMARY_KEYS_QUERY: [],
            crunchbase_inspect.INDEXES_QUERY: [],
        }
        return FakeCursor(rows_by_query.get(statement, []))


def test_inspect_crunchbase_collects_catalog_in_read_only_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    monkeypatch.setattr(crunchbase_inspect, "connect_database", lambda _config: connection)

    inventory = inspect_crunchbase(DatabaseConfig("postgresql://unused"))

    assert connection.statements[0] == "SET TRANSACTION READ ONLY"
    assert inventory.schemas == (SchemaInfo("public", 1), SchemaInfo("storage", 8))
    assert inventory.relations[0].name == "organizations"
    assert inventory.relations[0].columns == (
        ColumnInfo(1, "id", "uuid", True),
        ColumnInfo(2, "domain", "character varying", False),
    )
    assert inventory.relations[0].primary_key is None
    assert inventory.relations[0].indexes == ()


@pytest.mark.parametrize(
    ("byte_count", "expected"),
    [(0, "0 B"), (1024, "1.00 KiB"), (1024**2, "1.00 MiB"), (1024**3, "1.00 GiB")],
)
def test_format_bytes(byte_count: int, expected: str) -> None:
    assert format_bytes(byte_count) == expected


def test_render_inventory_includes_exact_count_structure_and_empty_table_warning() -> None:
    relation = RelationInfo(
        schema="public",
        name="organizations",
        kind="r",
        estimated_rows=0,
        exact_rows=0,
        heap_bytes=1024**3,
        index_bytes=0,
        total_bytes=1024**3,
        columns=(ColumnInfo(1, "id", "uuid", True),),
        primary_key=None,
        indexes=(),
    )
    inventory = CrunchbaseInventory(
        schemas=(SchemaInfo("public", 1),),
        relations=(relation,),
        exact_counts_requested=True,
    )

    report = render_inventory(inventory)

    assert "public.organizations" in report
    assert "rows: 0 (exact)" in report
    assert "id: uuid NOT NULL" in report
    assert "primary key: none" in report
    assert "indexes: none" in report
    assert "still occupies substantial space" in report
