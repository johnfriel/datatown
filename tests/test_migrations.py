from __future__ import annotations

from datatown.metadata.migrations import discover_migrations


def test_meta_migration_is_bundled_and_checksum_verified() -> None:
    migrations = discover_migrations()

    assert [migration.name for migration in migrations] == ["001_meta.sql"]
    assert len(migrations[0].sha256) == 64
    assert "CREATE TABLE IF NOT EXISTS meta.dataset_snapshots" in migrations[0].sql
    assert "CREATE TABLE IF NOT EXISTS meta.dataset_files" in migrations[0].sql
    assert "CREATE TABLE IF NOT EXISTS meta.import_runs" in migrations[0].sql
