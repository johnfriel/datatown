"""PostgreSQL connections and read-only health checks."""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
from psycopg import Connection

from datatown.config import DatabaseConfig


@dataclass(frozen=True, slots=True)
class DatabaseProbeResult:
    """Identity returned by a successful read-only database probe."""

    database_name: str
    server_version: str


def connect_database(config: DatabaseConfig) -> Connection[tuple[object, ...]]:
    """Open a PostgreSQL connection using the configured finite timeout."""
    return psycopg.connect(config.url, connect_timeout=config.connect_timeout_seconds)


def probe_database(config: DatabaseConfig) -> DatabaseProbeResult:
    """Check connectivity without permitting writes in the probe transaction."""
    with connect_database(config) as connection, connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        row = connection.execute(
            "SELECT current_database(), current_setting('server_version')"
        ).fetchone()

    if row is None:
        raise RuntimeError("Database connectivity probe returned no result")

    return DatabaseProbeResult(database_name=str(row[0]), server_version=str(row[1]))
