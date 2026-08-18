from __future__ import annotations

from datatown.metadata.migrations import discover_migrations


def test_migrations_are_bundled_and_checksum_verified() -> None:
    migrations = discover_migrations()

    assert [migration.name for migration in migrations] == ["001_meta.sql", "002_pdl.sql"]
    assert all(len(migration.sha256) == 64 for migration in migrations)
    assert "CREATE TABLE IF NOT EXISTS meta.dataset_snapshots" in migrations[0].sql
    assert "CREATE TABLE IF NOT EXISTS meta.dataset_files" in migrations[0].sql
    assert "CREATE TABLE IF NOT EXISTS meta.import_runs" in migrations[0].sql
    assert "CREATE TABLE IF NOT EXISTS pdl.companies" in migrations[1].sql
    assert "CONSTRAINT companies_pkey PRIMARY KEY (id)" in migrations[1].sql
    assert "companies_website_idx" in migrations[1].sql
    assert "companies_linkedin_url_idx" in migrations[1].sql
