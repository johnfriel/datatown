"""Minimal PostgreSQL repository for archived dataset provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from psycopg import Connection

from datatown.config import DatabaseConfig
from datatown.db import connect_database
from datatown.metadata.models import DatasetFileInput, DatasetSnapshotInput


class MetadataError(RuntimeError):
    """Raised when existing provenance conflicts with a requested archive."""


@dataclass(frozen=True, slots=True)
class RecordedSnapshot:
    snapshot_id: UUID
    created: bool
    file_ids: tuple[UUID, ...]


def _uuid(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise MetadataError(f"Expected UUID from metadata query, received {type(value).__name__}")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise MetadataError(f"Expected text from metadata query, received {type(value).__name__}")
    return value


def _nullable_text(value: object) -> str | None:
    return None if value is None else _text(value)


def _datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise MetadataError(
            f"Expected datetime from metadata query, received {type(value).__name__}"
        )
    return value.astimezone(UTC)


def _integer(value: object) -> int:
    if not isinstance(value, int):
        raise MetadataError(
            f"Expected integer from metadata query, received {type(value).__name__}"
        )
    return value


def _ensure_initialized(connection: Connection[tuple[object, ...]]) -> None:
    row = connection.execute(
        """
        SELECT
            to_regclass('meta.dataset_snapshots'),
            to_regclass('meta.dataset_files'),
            to_regclass('meta.import_runs')
        """
    ).fetchone()
    if row is None or any(value is None for value in row):
        raise MetadataError("Metadata tables are not initialized; run `datatown migrate` first")


def _compare_snapshot_row(row: tuple[object, ...], requested: DatasetSnapshotInput) -> UUID:
    snapshot_id = _uuid(row[0])
    existing = (
        _text(row[1]),
        _text(row[2]),
        _datetime(row[3]),
        _nullable_text(row[4]),
        _nullable_text(row[5]),
        _text(row[6]),
    )
    expected = (
        requested.source,
        requested.dataset,
        requested.acquired_at.astimezone(UTC),
        requested.source_url,
        requested.description,
        requested.manifest_object_key,
    )
    if existing != expected:
        raise MetadataError(
            "Existing snapshot metadata conflicts with the requested source/dataset/acquisition"
        )
    return snapshot_id


def _compare_file_row(
    row: tuple[object, ...], requested: DatasetFileInput, snapshot_id: UUID
) -> UUID:
    file_id = _uuid(row[0])
    existing = (
        _uuid(row[1]),
        _text(row[2]),
        _text(row[3]),
        _text(row[4]),
        _text(row[5]),
        _text(row[6]),
        _integer(row[7]),
    )
    expected = (
        snapshot_id,
        requested.role,
        requested.filename,
        requested.object_key,
        requested.format,
        requested.sha256,
        requested.byte_size,
    )
    if existing != expected:
        raise MetadataError(
            f"Existing dataset-file metadata conflicts with {requested.role} ({requested.sha256})"
        )
    return file_id


def _preflight_on_connection(
    connection: Connection[tuple[object, ...]], requested: DatasetSnapshotInput
) -> UUID | None:
    _ensure_initialized(connection)
    snapshot_row = connection.execute(
        """
        SELECT id, source, dataset, acquired_at, source_url, description, manifest_object_key
        FROM meta.dataset_snapshots
        WHERE source = %s AND dataset = %s AND acquired_at = %s
        """,
        (requested.source, requested.dataset, requested.acquired_at),
    ).fetchone()
    snapshot_id = (
        _compare_snapshot_row(snapshot_row, requested) if snapshot_row is not None else None
    )

    hashes = [file.sha256 for file in requested.files]
    object_keys = [file.object_key for file in requested.files]
    collision_rows = connection.execute(
        """
        SELECT id, snapshot_id, role, filename, object_key, format, sha256, byte_size
        FROM meta.dataset_files
        WHERE sha256 = ANY(%s) OR object_key = ANY(%s)
        """,
        (hashes, object_keys),
    ).fetchall()

    for row in collision_rows:
        requested_file = next(
            (
                file
                for file in requested.files
                if file.sha256 == _text(row[6]) or file.object_key == _text(row[4])
            ),
            None,
        )
        if requested_file is None or snapshot_id is None:
            raise MetadataError(
                "A source hash or object key is already assigned to another snapshot"
            )
        _compare_file_row(row, requested_file, snapshot_id)

    if snapshot_id is not None:
        snapshot_file_rows = connection.execute(
            """
            SELECT id, snapshot_id, role, filename, object_key, format, sha256, byte_size
            FROM meta.dataset_files
            WHERE snapshot_id = %s
            """,
            (snapshot_id,),
        ).fetchall()
        requested_by_role = {file.role: file for file in requested.files}
        for row in snapshot_file_rows:
            role = _text(row[2])
            requested_file = requested_by_role.get(role)
            if requested_file is None:
                raise MetadataError(f"Existing snapshot contains unexpected file role: {role}")
            _compare_file_row(row, requested_file, snapshot_id)

    return snapshot_id


def preflight_snapshot(config: DatabaseConfig, requested: DatasetSnapshotInput) -> UUID | None:
    """Reject metadata collisions before uploading large source artifacts."""
    with connect_database(config) as connection, connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        return _preflight_on_connection(connection, requested)


def record_snapshot(config: DatabaseConfig, requested: DatasetSnapshotInput) -> RecordedSnapshot:
    """Atomically record one snapshot and its files, or verify an exact prior record."""
    with connect_database(config) as connection, connection.transaction():
        snapshot_id = _preflight_on_connection(connection, requested)
        created = snapshot_id is None
        if snapshot_id is None:
            snapshot_id = uuid4()
            connection.execute(
                """
                INSERT INTO meta.dataset_snapshots (
                    id, source, dataset, acquired_at, source_url, description,
                    manifest_object_key
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    snapshot_id,
                    requested.source,
                    requested.dataset,
                    requested.acquired_at,
                    requested.source_url,
                    requested.description,
                    requested.manifest_object_key,
                ),
            )

        file_ids: list[UUID] = []
        for requested_file in requested.files:
            existing_row = connection.execute(
                """
                SELECT id, snapshot_id, role, filename, object_key, format, sha256, byte_size
                FROM meta.dataset_files
                WHERE snapshot_id = %s AND role = %s
                """,
                (snapshot_id, requested_file.role),
            ).fetchone()
            if existing_row is not None:
                file_ids.append(_compare_file_row(existing_row, requested_file, snapshot_id))
                continue

            file_id = uuid4()
            connection.execute(
                """
                INSERT INTO meta.dataset_files (
                    id, snapshot_id, role, filename, object_key, format, sha256, byte_size
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    file_id,
                    snapshot_id,
                    requested_file.role,
                    requested_file.filename,
                    requested_file.object_key,
                    requested_file.format,
                    requested_file.sha256,
                    requested_file.byte_size,
                ),
            )
            file_ids.append(file_id)

    return RecordedSnapshot(snapshot_id=snapshot_id, created=created, file_ids=tuple(file_ids))
