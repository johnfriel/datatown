"""Small, checksum-verified SQL migration runner."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib.resources import files

from datatown.config import DatabaseConfig
from datatown.db import connect_database

MIGRATIONS_PACKAGE = "datatown.migrations"
CREATE_MIGRATION_STATE_SQL = """
CREATE SCHEMA IF NOT EXISTS meta;
CREATE TABLE IF NOT EXISTS meta.schema_migrations (
    version text PRIMARY KEY,
    name text NOT NULL,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    """Raised when migration state is invalid or a migration cannot be applied safely."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    name: str
    sha256: str
    sql: str


@dataclass(frozen=True, slots=True)
class MigrationResult:
    migration: Migration
    applied: bool


def discover_migrations() -> tuple[Migration, ...]:
    """Load ordered SQL migrations bundled in the package."""
    discovered: list[Migration] = []
    for resource in sorted(files(MIGRATIONS_PACKAGE).iterdir(), key=lambda item: item.name):
        if not resource.name.endswith(".sql"):
            continue
        version, separator, _description = resource.name.partition("_")
        if not separator or not version.isdigit():
            raise MigrationError(f"Invalid migration filename: {resource.name}")
        contents = resource.read_text(encoding="utf-8")
        discovered.append(
            Migration(
                version=version,
                name=resource.name,
                sha256=hashlib.sha256(contents.encode("utf-8")).hexdigest(),
                sql=contents,
            )
        )
    if not discovered:
        raise MigrationError("No bundled SQL migrations were found")
    return tuple(discovered)


def apply_migrations(config: DatabaseConfig) -> tuple[MigrationResult, ...]:
    """Apply pending migrations atomically and reject changed applied files."""
    migrations = discover_migrations()
    results: list[MigrationResult] = []
    with connect_database(config) as connection, connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(hashtext('datatown:migrations'))")
        connection.execute(CREATE_MIGRATION_STATE_SQL, prepare=False)
        applied_rows = connection.execute(
            "SELECT version, name, sha256 FROM meta.schema_migrations"
        ).fetchall()
        applied = {str(row[0]): (str(row[1]), str(row[2])) for row in applied_rows}

        for migration in migrations:
            existing = applied.get(migration.version)
            if existing is not None:
                existing_name, existing_sha256 = existing
                if existing_name != migration.name or existing_sha256 != migration.sha256:
                    raise MigrationError(
                        f"Applied migration {migration.version} does not match bundled "
                        f"{migration.name}"
                    )
                results.append(MigrationResult(migration=migration, applied=False))
                continue

            connection.execute(migration.sql, prepare=False)
            connection.execute(
                """
                INSERT INTO meta.schema_migrations (version, name, sha256)
                VALUES (%s, %s, %s)
                """,
                (migration.version, migration.name, migration.sha256),
            )
            results.append(MigrationResult(migration=migration, applied=True))

    return tuple(results)
