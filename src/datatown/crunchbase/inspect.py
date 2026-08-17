"""Read-only PostgreSQL catalog inspection for the existing Crunchbase scrape."""

from __future__ import annotations

from dataclasses import dataclass, replace

from psycopg import Connection, sql
from psycopg.errors import QueryCanceled

from datatown.config import DatabaseConfig
from datatown.db import connect_database

CRUNCHBASE_SCHEMA = "public"
DEFAULT_COUNT_TIMEOUT_SECONDS = 60

SCHEMAS_QUERY = """
SELECT
    n.nspname,
    count(c.oid) FILTER (
        WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
    )::bigint AS relation_count
FROM pg_namespace AS n
LEFT JOIN pg_class AS c ON c.relnamespace = n.oid
WHERE n.nspname NOT LIKE 'pg_%'
  AND n.nspname <> 'information_schema'
GROUP BY n.nspname
ORDER BY n.nspname
"""

RELATIONS_QUERY = """
SELECT
    n.nspname,
    c.relname,
    c.relkind,
    CASE WHEN c.reltuples < 0 THEN NULL ELSE c.reltuples::bigint END,
    pg_relation_size(c.oid),
    pg_indexes_size(c.oid),
    pg_total_relation_size(c.oid)
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = %s
  AND c.relkind IN ('r', 'p')
ORDER BY c.relname
"""

COLUMNS_QUERY = """
SELECT
    c.relname,
    a.attnum,
    a.attname,
    pg_catalog.format_type(a.atttypid, a.atttypmod),
    a.attnotnull
FROM pg_attribute AS a
JOIN pg_class AS c ON c.oid = a.attrelid
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = %s
  AND c.relkind IN ('r', 'p')
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""

PRIMARY_KEYS_QUERY = """
SELECT
    c.relname,
    con.conname,
    pg_get_constraintdef(con.oid)
FROM pg_constraint AS con
JOIN pg_class AS c ON c.oid = con.conrelid
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = %s
  AND con.contype = 'p'
ORDER BY c.relname, con.conname
"""

INDEXES_QUERY = """
SELECT tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = %s
ORDER BY tablename, indexname
"""


@dataclass(frozen=True, slots=True)
class SchemaInfo:
    name: str
    relation_count: int


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    position: int
    name: str
    data_type: str
    not_null: bool


@dataclass(frozen=True, slots=True)
class PrimaryKeyInfo:
    name: str
    definition: str


@dataclass(frozen=True, slots=True)
class IndexInfo:
    name: str
    definition: str


@dataclass(frozen=True, slots=True)
class RelationInfo:
    schema: str
    name: str
    kind: str
    estimated_rows: int | None
    heap_bytes: int
    index_bytes: int
    total_bytes: int
    columns: tuple[ColumnInfo, ...]
    primary_key: PrimaryKeyInfo | None
    indexes: tuple[IndexInfo, ...]
    exact_rows: int | None = None
    count_error: str | None = None


@dataclass(frozen=True, slots=True)
class CrunchbaseInventory:
    schemas: tuple[SchemaInfo, ...]
    relations: tuple[RelationInfo, ...]
    exact_counts_requested: bool


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"Expected text from PostgreSQL catalog, received {type(value).__name__}")
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int):
        raise TypeError(
            f"Expected integer from PostgreSQL catalog, received {type(value).__name__}"
        )
    return value


def _optional_integer(value: object) -> int | None:
    return None if value is None else _integer(value)


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError(
            f"Expected boolean from PostgreSQL catalog, received {type(value).__name__}"
        )
    return value


def _collect_catalog(
    connection: Connection[tuple[object, ...]],
) -> tuple[tuple[SchemaInfo, ...], tuple[RelationInfo, ...]]:
    schema_rows = connection.execute(SCHEMAS_QUERY).fetchall()
    relation_rows = connection.execute(RELATIONS_QUERY, (CRUNCHBASE_SCHEMA,)).fetchall()
    column_rows = connection.execute(COLUMNS_QUERY, (CRUNCHBASE_SCHEMA,)).fetchall()
    primary_key_rows = connection.execute(PRIMARY_KEYS_QUERY, (CRUNCHBASE_SCHEMA,)).fetchall()
    index_rows = connection.execute(INDEXES_QUERY, (CRUNCHBASE_SCHEMA,)).fetchall()

    schemas = tuple(
        SchemaInfo(name=_text(row[0]), relation_count=_integer(row[1])) for row in schema_rows
    )

    columns_by_relation: dict[str, list[ColumnInfo]] = {}
    for row in column_rows:
        columns_by_relation.setdefault(_text(row[0]), []).append(
            ColumnInfo(
                position=_integer(row[1]),
                name=_text(row[2]),
                data_type=_text(row[3]),
                not_null=_boolean(row[4]),
            )
        )

    primary_keys = {
        _text(row[0]): PrimaryKeyInfo(name=_text(row[1]), definition=_text(row[2]))
        for row in primary_key_rows
    }

    indexes_by_relation: dict[str, list[IndexInfo]] = {}
    for row in index_rows:
        indexes_by_relation.setdefault(_text(row[0]), []).append(
            IndexInfo(name=_text(row[1]), definition=_text(row[2]))
        )

    relations = tuple(
        RelationInfo(
            schema=_text(row[0]),
            name=_text(row[1]),
            kind=_text(row[2]),
            estimated_rows=_optional_integer(row[3]),
            heap_bytes=_integer(row[4]),
            index_bytes=_integer(row[5]),
            total_bytes=_integer(row[6]),
            columns=tuple(columns_by_relation.get(_text(row[1]), [])),
            primary_key=primary_keys.get(_text(row[1])),
            indexes=tuple(indexes_by_relation.get(_text(row[1]), [])),
        )
        for row in relation_rows
    )
    return schemas, relations


def _exact_row_count(
    connection: Connection[tuple[object, ...]],
    relation: RelationInfo,
    timeout_seconds: int,
) -> int:
    with connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        connection.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (f"{timeout_seconds}s",),
        )
        row = connection.execute(
            sql.SQL("SELECT count(*) FROM {}.{}").format(
                sql.Identifier(relation.schema), sql.Identifier(relation.name)
            )
        ).fetchone()
    if row is None:
        raise RuntimeError(f"Exact count returned no result for {relation.schema}.{relation.name}")
    return _integer(row[0])


def inspect_crunchbase(
    config: DatabaseConfig,
    *,
    exact_counts: bool = False,
    count_timeout_seconds: int = DEFAULT_COUNT_TIMEOUT_SECONDS,
) -> CrunchbaseInventory:
    """Inspect the current public-schema Crunchbase layout without modifying it."""
    if count_timeout_seconds <= 0:
        raise ValueError("count_timeout_seconds must be greater than zero")

    with connect_database(config) as connection:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            schemas, relations = _collect_catalog(connection)

        if exact_counts:
            counted_relations: list[RelationInfo] = []
            for relation in relations:
                try:
                    exact_rows = _exact_row_count(connection, relation, count_timeout_seconds)
                except QueryCanceled:
                    counted_relations.append(
                        replace(
                            relation,
                            count_error=f"timed out after {count_timeout_seconds} seconds",
                        )
                    )
                else:
                    counted_relations.append(replace(relation, exact_rows=exact_rows))
            relations = tuple(counted_relations)

    return CrunchbaseInventory(
        schemas=schemas,
        relations=relations,
        exact_counts_requested=exact_counts,
    )


def format_bytes(byte_count: int) -> str:
    """Format a non-negative byte count using binary units."""
    if byte_count < 0:
        raise ValueError("byte_count must not be negative")
    value = float(byte_count)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def _row_count_description(relation: RelationInfo) -> str:
    if relation.exact_rows is not None:
        return f"{relation.exact_rows:,} (exact)"
    estimate = "unknown" if relation.estimated_rows is None else f"~{relation.estimated_rows:,}"
    if relation.count_error:
        return f"{estimate} (estimate; exact count {relation.count_error})"
    return f"{estimate} (catalog estimate)"


def render_inventory(inventory: CrunchbaseInventory) -> str:
    """Render a stable, human-readable operator report."""
    lines = ["Schemas"]
    for schema in inventory.schemas:
        lines.append(f"  {schema.name}: {schema.relation_count} relation(s)")

    total_size = sum(relation.total_bytes for relation in inventory.relations)
    lines.extend(
        [
            "",
            "Current Crunchbase placement",
            f"  schema: {CRUNCHBASE_SCHEMA}",
            f"  relations: {len(inventory.relations)}",
            f"  total size: {format_bytes(total_size)}",
            (
                "  row counts: exact with bounded per-table timeout"
                if inventory.exact_counts_requested
                else "  row counts: PostgreSQL catalog estimates"
            ),
        ]
    )

    if not inventory.relations:
        lines.extend(["", "No ordinary or partitioned tables found in public."])
        return "\n".join(lines)

    for relation in inventory.relations:
        relation_kind = "partitioned table" if relation.kind == "p" else "table"
        lines.extend(
            [
                "",
                f"{relation.schema}.{relation.name}",
                f"  type: {relation_kind}",
                f"  rows: {_row_count_description(relation)}",
                (
                    f"  size: {format_bytes(relation.total_bytes)} total "
                    f"({format_bytes(relation.heap_bytes)} heap, "
                    f"{format_bytes(relation.index_bytes)} indexes)"
                ),
                f"  columns ({len(relation.columns)}):",
            ]
        )
        for column in relation.columns:
            nullability = "NOT NULL" if column.not_null else "NULL"
            lines.append(f"    {column.name}: {column.data_type} {nullability}")

        if relation.primary_key is None:
            lines.append("  primary key: none")
        else:
            lines.append(
                f"  primary key: {relation.primary_key.name} ({relation.primary_key.definition})"
            )

        if relation.indexes:
            lines.append(f"  indexes ({len(relation.indexes)}):")
            for index in relation.indexes:
                lines.append(f"    {index.name}: {index.definition}")
        else:
            lines.append("  indexes: none")

        if relation.exact_rows == 0 and relation.total_bytes >= 100 * 1024 * 1024:
            lines.append(
                "  warning: exact count is zero, but the relation still occupies substantial space"
            )

    return "\n".join(lines)
