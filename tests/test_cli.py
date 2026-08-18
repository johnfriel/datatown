from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from typer.testing import CliRunner

import datatown.cli as cli
from datatown.db import DatabaseProbeResult
from datatown.pdl.import_companies import ImportResult
from datatown.storage import StorageProbeResult

runner = CliRunner()


@pytest.fixture
def configured_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    values = {
        "DATATOWN_DATABASE_URL": ("postgresql://operator:db-secret@db.example.test:5432/datatown"),
        "DATATOWN_S3_ENDPOINT_URL": "https://storage.example.test/s3",
        "DATATOWN_S3_REGION": "us-test-1",
        "DATATOWN_S3_BUCKET": "third-party-datasets",
        "DATATOWN_S3_ACCESS_KEY_ID": "access-key",
        "DATATOWN_S3_SECRET_ACCESS_KEY": "storage-secret",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    yield


def test_help_is_available_without_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATATOWN_DATABASE_URL", raising=False)

    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "doctor" in result.stdout
    assert "crunchbase" in result.stdout
    assert "pdl" in result.stdout


def test_crunchbase_inspect_help_is_available_without_configuration() -> None:
    result = runner.invoke(cli.app, ["crunchbase", "inspect", "--help"])

    assert result.exit_code == 0
    assert "--exact-counts" in result.stdout


def test_pdl_inspect_help_is_available_without_configuration() -> None:
    result = runner.invoke(cli.app, ["pdl", "inspect", "--help"])

    assert result.exit_code == 0
    assert "--csv" in result.stdout
    assert "--json" in result.stdout
    assert "--sha256" in result.stdout


def test_pdl_archive_help_is_available_without_configuration() -> None:
    result = runner.invoke(cli.app, ["pdl", "archive", "--help"])

    assert result.exit_code == 0
    assert "--csv" in result.stdout
    assert "--json" in result.stdout
    assert "--acquired-at" in result.stdout


def test_pdl_import_companies_help_is_available_without_configuration() -> None:
    result = runner.invoke(cli.app, ["pdl", "import-companies", "--help"])

    assert result.exit_code == 0
    assert "--json" in result.stdout
    assert "--snapshot-id" in result.stdout
    assert "atomically replace" in result.stdout


def test_migrate_help_is_available_without_configuration() -> None:
    result = runner.invoke(cli.app, ["migrate", "--help"])

    assert result.exit_code == 0
    assert "database schemas and tables" in result.stdout


def test_pdl_import_companies_reports_safe_success(
    tmp_path: Path,
    configured_environment: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    json_path = tmp_path / "companies.json"
    json_path.write_text('{"id":"company"}\n', encoding="utf-8")
    snapshot_id = UUID("ce01d408-a2be-416b-b333-f6aaed39dbdc")
    run_id = UUID("b8b624b0-0166-4aa3-8c1d-0d3143150d84")

    def succeed(_config, source, *, snapshot_id, report) -> ImportResult:
        assert source == json_path
        assert snapshot_id == UUID("ce01d408-a2be-416b-b333-f6aaed39dbdc")
        report("COPY loaded 35,828,989 validated records")
        return ImportResult(
            run_id=run_id,
            snapshot_id=snapshot_id,
            row_count=35_828_989,
            relation_size=4_294_967_296,
            source_sha256="e" * 64,
        )

    monkeypatch.setattr(cli, "import_companies", succeed)

    result = runner.invoke(
        cli.app,
        [
            "pdl",
            "import-companies",
            "--json",
            str(json_path),
            "--snapshot-id",
            str(snapshot_id),
        ],
    )

    assert result.exit_code == 0
    assert "stage, validate, then atomically swap" in result.stdout
    assert "35,828,989" in result.stdout
    assert "4.00 GiB" in result.stdout
    assert str(run_id) in result.stdout
    assert "db-secret" not in result.stdout


def test_doctor_reports_both_successes(
    configured_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli,
        "probe_database",
        lambda _config: DatabaseProbeResult("datatown", "17.2"),
    )
    monkeypatch.setattr(
        cli,
        "probe_storage",
        lambda _config: StorageProbeResult("third-party-datasets"),
    )

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 0
    assert "db.example.test:5432/datatown" in result.stdout
    assert "PostgreSQL 17.2" in result.stdout
    assert "storage.example.test/s3/third-party-datasets" in result.stdout
    assert result.stdout.count("status: OK") == 2
    assert "db-secret" not in result.stdout
    assert "storage-secret" not in result.stdout


def test_doctor_runs_storage_check_after_database_failure(
    configured_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage_checked = False

    def fail_database(_config) -> DatabaseProbeResult:
        raise RuntimeError("database unavailable; credential db-secret")

    def check_storage(_config) -> StorageProbeResult:
        nonlocal storage_checked
        storage_checked = True
        return StorageProbeResult("third-party-datasets")

    monkeypatch.setattr(cli, "probe_database", fail_database)
    monkeypatch.setattr(cli, "probe_storage", check_storage)

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 1
    assert storage_checked
    assert "database unavailable" in result.stdout
    assert "db-secret" not in result.stdout
    assert "[redacted]" in result.stdout
    assert "Object storage" in result.stdout
    assert "status: OK" in result.stdout


def test_doctor_reports_each_missing_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DATATOWN_DATABASE_URL",
        "DATATOWN_S3_ENDPOINT_URL",
        "DATATOWN_S3_REGION",
        "DATATOWN_S3_BUCKET",
        "DATATOWN_S3_ACCESS_KEY_ID",
        "DATATOWN_S3_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 1
    assert result.stdout.count("status: FAILED") == 2
    assert "DATATOWN_DATABASE_URL" in result.stdout
    assert "DATATOWN_S3_ENDPOINT_URL" in result.stdout
