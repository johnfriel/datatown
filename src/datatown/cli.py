"""Operator-oriented Datatown command-line interface."""

from __future__ import annotations

from collections.abc import Callable

import typer

from datatown.config import (
    ConfigurationError,
    DatabaseConfig,
    StorageConfig,
    redact_secrets,
)
from datatown.db import probe_database
from datatown.storage import probe_storage

app = typer.Typer(
    help="Archive, inspect, and query third-party datasets used by Peoplebot.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


def _load_config[ConfigT: (DatabaseConfig, StorageConfig)](
    loader: Callable[[], ConfigT],
) -> tuple[ConfigT | None, str | None]:
    try:
        return loader(), None
    except ConfigurationError as error:
        return None, str(error)


def _safe_error(error: Exception, secrets: tuple[str, ...]) -> str:
    message = str(error).strip() or error.__class__.__name__
    return redact_secrets(message, secrets)


def _print_section(name: str, target: str | None, status: str, detail: str) -> None:
    typer.echo(name)
    typer.echo(f"  target: {target or 'unavailable'}")
    typer.echo(f"  status: {status}")
    typer.echo(f"  {detail}")


@app.callback()
def main() -> None:
    """Operate Datatown's source-data archive and queryable database."""


@app.command()
def doctor() -> None:
    """Verify read-only connectivity to PostgreSQL and object storage."""
    typer.echo("Datatown doctor")
    typer.echo()

    database, database_config_error = _load_config(DatabaseConfig.from_env)
    storage, storage_config_error = _load_config(StorageConfig.from_env)
    all_ok = True

    if database is None:
        all_ok = False
        _print_section("Database", None, "FAILED", f"error: {database_config_error}")
    else:
        try:
            database_result = probe_database(database)
        except Exception as error:  # CLI boundary: report service errors without a traceback.
            all_ok = False
            detail = _safe_error(error, database.redaction_values())
            _print_section("Database", database.target_description(), "FAILED", f"error: {detail}")
        else:
            _print_section(
                "Database",
                database.target_description(),
                "OK",
                (
                    f"database: {database_result.database_name}; "
                    f"PostgreSQL {database_result.server_version}"
                ),
            )

    typer.echo()

    if storage is None:
        all_ok = False
        _print_section("Object storage", None, "FAILED", f"error: {storage_config_error}")
    else:
        try:
            storage_result = probe_storage(storage)
        except Exception as error:  # CLI boundary: report service errors without a traceback.
            all_ok = False
            detail = _safe_error(error, storage.redaction_values())
            _print_section(
                "Object storage", storage.target_description(), "FAILED", f"error: {detail}"
            )
        else:
            _print_section(
                "Object storage",
                storage.target_description(),
                "OK",
                f"bucket: {storage_result.bucket}",
            )

    if not all_ok:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
