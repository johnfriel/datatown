from __future__ import annotations

from collections.abc import Iterator

import pytest
from typer.testing import CliRunner

import datatown.cli as cli
from datatown.db import DatabaseProbeResult
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
