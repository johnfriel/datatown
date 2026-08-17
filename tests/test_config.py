from __future__ import annotations

import pytest

from datatown.config import (
    ConfigurationError,
    DatabaseConfig,
    Settings,
    StorageConfig,
    redact_secrets,
)


def complete_environment() -> dict[str, str]:
    return {
        "DATATOWN_DATABASE_URL": "postgresql://operator:db-secret@db.example.test:6543/datatown",
        "DATATOWN_S3_ENDPOINT_URL": "https://storage.example.test/s3",
        "DATATOWN_S3_REGION": "us-test-1",
        "DATATOWN_S3_BUCKET": "third-party-datasets",
        "DATATOWN_S3_ACCESS_KEY_ID": "access-key",
        "DATATOWN_S3_SECRET_ACCESS_KEY": "storage-secret",
    }


def test_settings_loads_complete_environment() -> None:
    settings = Settings.from_env(complete_environment())

    assert settings.database.url.startswith("postgresql://")
    assert settings.storage.bucket == "third-party-datasets"


def test_database_config_reports_missing_variable() -> None:
    with pytest.raises(ConfigurationError, match="DATATOWN_DATABASE_URL"):
        DatabaseConfig.from_env({})


def test_storage_config_reports_all_missing_variables() -> None:
    with pytest.raises(ConfigurationError) as raised:
        StorageConfig.from_env({})

    message = str(raised.value)
    assert "DATATOWN_S3_ENDPOINT_URL" in message
    assert "DATATOWN_S3_SECRET_ACCESS_KEY" in message


def test_database_target_description_omits_credentials() -> None:
    config = DatabaseConfig.from_env(complete_environment())

    assert config.target_description() == "db.example.test:6543/datatown"
    assert "operator" not in config.target_description()
    assert "db-secret" not in config.target_description()
    assert config.redaction_values() == (
        complete_environment()["DATATOWN_DATABASE_URL"],
        "operator",
        "db-secret",
    )


def test_storage_target_description_omits_credentials() -> None:
    config = StorageConfig.from_env(complete_environment())

    assert config.target_description() == (
        "https://storage.example.test/s3/third-party-datasets (region us-test-1)"
    )
    assert "access-key" not in config.target_description()
    assert "storage-secret" not in config.target_description()
    assert config.redaction_values() == ("access-key", "storage-secret")


@pytest.mark.parametrize("endpoint", ["storage.example.test", "ftp://storage.example.test"])
def test_storage_endpoint_must_be_http_url(endpoint: str) -> None:
    environment = complete_environment()
    environment["DATATOWN_S3_ENDPOINT_URL"] = endpoint

    with pytest.raises(ConfigurationError, match="absolute http or https URL"):
        StorageConfig.from_env(environment)


def test_storage_endpoint_rejects_embedded_credentials() -> None:
    environment = complete_environment()
    environment["DATATOWN_S3_ENDPOINT_URL"] = "https://user:password@storage.example.test"

    with pytest.raises(ConfigurationError, match="must not contain credentials"):
        StorageConfig.from_env(environment)


def test_redact_secrets_replaces_every_known_secret() -> None:
    message = "database db-secret and storage storage-secret failed"

    assert redact_secrets(message, ("db-secret", "storage-secret")) == (
        "database [redacted] and storage [redacted] failed"
    )
