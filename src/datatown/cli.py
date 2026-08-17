"""Operator-oriented Datatown command-line interface."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from datatown.config import (
    ConfigurationError,
    DatabaseConfig,
    StorageConfig,
    redact_secrets,
)
from datatown.crunchbase.inspect import inspect_crunchbase, render_inventory
from datatown.db import probe_database
from datatown.metadata.migrations import apply_migrations
from datatown.pdl.archive import (
    ArchiveArtifact,
    archive_plan,
    build_archive_plan,
    parse_acquired_at,
)
from datatown.pdl.inspect import PDLInspectionError, inspect_pdl_files, render_inspection
from datatown.storage import probe_storage

app = typer.Typer(
    help="Archive, inspect, and query third-party datasets used by Peoplebot.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
crunchbase_app = typer.Typer(
    help="Inspect the existing Crunchbase scrape without changing it.",
    no_args_is_help=True,
)
app.add_typer(crunchbase_app, name="crunchbase")
pdl_app = typer.Typer(
    help="Inspect and eventually archive/import People Data Labs datasets.",
    no_args_is_help=True,
)
app.add_typer(pdl_app, name="pdl")


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


@app.command()
def migrate() -> None:
    """Create or update Datatown's metadata tables."""
    database, config_error = _load_config(DatabaseConfig.from_env)
    if database is None:
        typer.echo(f"Configuration error: {config_error}")
        raise typer.Exit(code=1)

    typer.echo("Datatown metadata migration")
    typer.echo(f"  database: {database.target_description()}")
    typer.echo("  target schema: meta")
    typer.echo()

    try:
        results = apply_migrations(database)
    except Exception as error:  # CLI boundary: report service errors without a traceback.
        detail = _safe_error(error, database.redaction_values())
        typer.echo(f"Migration failed: {detail}")
        raise typer.Exit(code=1) from error

    for result in results:
        status = "applied" if result.applied else "already applied"
        typer.echo(f"  {result.migration.name}: {status}")


@crunchbase_app.command("inspect")
def crunchbase_inspect(
    exact_counts: Annotated[
        bool,
        typer.Option(
            "--exact-counts",
            help="Run bounded COUNT(*) queries instead of relying only on catalog estimates.",
        ),
    ] = False,
) -> None:
    """Report schemas, tables, columns, keys, indexes, row counts, and sizes."""
    database, config_error = _load_config(DatabaseConfig.from_env)
    if database is None:
        typer.echo(f"Configuration error: {config_error}")
        raise typer.Exit(code=1)

    typer.echo("Crunchbase inspection")
    typer.echo(f"  database: {database.target_description()}")
    typer.echo("  mode: read-only")
    typer.echo()

    try:
        inventory = inspect_crunchbase(database, exact_counts=exact_counts)
    except Exception as error:  # CLI boundary: report service errors without a traceback.
        detail = _safe_error(error, database.redaction_values())
        typer.echo(f"Inspection failed: {detail}")
        raise typer.Exit(code=1) from error

    typer.echo(render_inventory(inventory))


@pdl_app.command("inspect")
def pdl_inspect(
    csv_path: Annotated[
        Path,
        typer.Option(
            "--csv",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Path to the original PDL company CSV file.",
        ),
    ],
    json_path: Annotated[
        Path,
        typer.Option(
            "--json",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Path to the original PDL company JSON file.",
        ),
    ],
    sample_size: Annotated[
        int,
        typer.Option("--sample-size", min=1, max=20, help="Records to display."),
    ] = 3,
    compare_records: Annotated[
        int,
        typer.Option(
            "--compare-records",
            min=1,
            help="Leading records to compare between representations.",
        ),
    ] = 100_000,
    sha256: Annotated[
        bool,
        typer.Option(
            "--sha256",
            help="Compute full-file SHA-256 hashes; this reads both artifacts completely.",
        ),
    ] = False,
) -> None:
    """Inspect and compare the actual PDL company CSV and JSON artifacts."""
    typer.echo("PDL company dataset inspection")
    typer.echo(f"  CSV: {csv_path}")
    typer.echo(f"  JSON: {json_path}")
    typer.echo(f"  comparison window: {compare_records:,} records")
    typer.echo(f"  full-file hashing: {'enabled' if sha256 else 'disabled'}")
    typer.echo()

    try:
        inspection = inspect_pdl_files(
            csv_path,
            json_path,
            sample_size=sample_size,
            compare_records=compare_records,
            compute_sha256=sha256,
        )
    except (OSError, PDLInspectionError, ValueError) as error:
        typer.echo(f"Inspection failed: {error}")
        raise typer.Exit(code=1) from error

    typer.echo(render_inspection(inspection))


@pdl_app.command("archive")
def pdl_archive(
    csv_path: Annotated[
        Path,
        typer.Option(
            "--csv",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Path to the untouched PDL company CSV file.",
        ),
    ],
    json_path: Annotated[
        Path,
        typer.Option(
            "--json",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Path to the untouched PDL company JSON file.",
        ),
    ],
    acquired_at: Annotated[
        str,
        typer.Option(
            "--acquired-at",
            help="Vendor acquisition date or ISO timestamp, such as 2026-08-17.",
        ),
    ],
    source_url: Annotated[
        str | None,
        typer.Option("--source-url", help="Optional vendor download or dataset URL."),
    ] = None,
) -> None:
    """Archive both original PDL company files and record their provenance."""
    database, database_config_error = _load_config(DatabaseConfig.from_env)
    storage, storage_config_error = _load_config(StorageConfig.from_env)
    if database is None or storage is None:
        if database_config_error is not None:
            typer.echo(f"Database configuration error: {database_config_error}")
        if storage_config_error is not None:
            typer.echo(f"Object-storage configuration error: {storage_config_error}")
        raise typer.Exit(code=1)

    secrets = (*database.redaction_values(), *storage.redaction_values())
    typer.echo("PDL company archive")
    typer.echo(f"  database: {database.target_description()}")
    typer.echo(f"  object storage: {storage.target_description()}")
    typer.echo(f"  CSV: {csv_path}")
    typer.echo(f"  JSON: {json_path}")
    typer.echo("  hashing originals (streaming)...")

    try:
        plan = build_archive_plan(
            csv_path,
            json_path,
            acquired_at=parse_acquired_at(acquired_at),
            source_url=source_url,
        )
    except Exception as error:
        typer.echo(f"Archive planning failed: {_safe_error(error, secrets)}")
        raise typer.Exit(code=1) from error

    typer.echo(f"  acquired at: {plan.snapshot.acquired_at.isoformat()}")
    for artifact in plan.artifacts:
        typer.echo(
            f"  {artifact.metadata.role}: {artifact.metadata.object_key} "
            f"({artifact.metadata.byte_size:,} bytes, sha256 {artifact.metadata.sha256})"
        )
    typer.echo(f"  manifest: {plan.snapshot.manifest_object_key}")
    typer.echo()

    def report_progress(artifact: ArchiveArtifact, transferred: int, total: int) -> None:
        percentage = min(100, int(transferred * 100 / total)) if total else 100
        typer.echo(f"  upload {artifact.metadata.role}: {percentage}%")

    try:
        result = archive_plan(database, storage, plan, progress=report_progress)
    except Exception as error:  # CLI boundary: report service errors without a traceback.
        typer.echo(f"Archive failed: {_safe_error(error, secrets)}")
        raise typer.Exit(code=1) from error

    for key in result.uploaded_keys:
        typer.echo(f"  uploaded: {key}")
    for key in result.existing_keys:
        typer.echo(f"  already archived and verified: {key}")
    metadata_status = "created" if result.recorded.created else "already recorded and verified"
    typer.echo(f"  snapshot metadata: {metadata_status}")
    typer.echo(f"  snapshot id: {result.recorded.snapshot_id}")


if __name__ == "__main__":
    app()
