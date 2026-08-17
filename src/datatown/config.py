"""Environment-based configuration with credential-safe target descriptions."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from psycopg.conninfo import conninfo_to_dict
from psycopg.errors import ProgrammingError

DATABASE_URL_ENV = "DATATOWN_DATABASE_URL"
STORAGE_ENV_VARS = (
    "DATATOWN_S3_ENDPOINT_URL",
    "DATATOWN_S3_REGION",
    "DATATOWN_S3_BUCKET",
    "DATATOWN_S3_ACCESS_KEY_ID",
    "DATATOWN_S3_SECRET_ACCESS_KEY",
)


class ConfigurationError(ValueError):
    """Raised when required Datatown configuration is missing or invalid."""


def _environment(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _required_values(environ: Mapping[str, str], names: tuple[str, ...]) -> dict[str, str]:
    values = {name: environ.get(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        joined = ", ".join(missing)
        raise ConfigurationError(f"Missing required environment variables: {joined}")
    return values


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    """Connection settings for the Datatown PostgreSQL database."""

    url: str
    connect_timeout_seconds: int = 10

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DatabaseConfig:
        values = _required_values(_environment(environ), (DATABASE_URL_ENV,))
        config = cls(url=values[DATABASE_URL_ENV])
        config.connection_parameters()
        return config

    def connection_parameters(self) -> dict[str, str | int | None]:
        """Parse libpq connection parameters without exposing their values in output."""
        try:
            return conninfo_to_dict(self.url)
        except ProgrammingError as error:
            raise ConfigurationError(f"{DATABASE_URL_ENV} is not a valid PostgreSQL DSN") from error

    def target_description(self) -> str:
        """Return a target label containing no username, password, or query parameters."""
        parameters = self.connection_parameters()
        host = parameters.get("host") or "default"
        port = parameters.get("port") or "5432"
        database = parameters.get("dbname") or "default"
        return f"{host}:{port}/{database}"

    def redaction_values(self) -> tuple[str, ...]:
        """Return connection values that must never appear in operator output."""
        parameters = self.connection_parameters()
        credentials = tuple(
            str(parameters[name])
            for name in ("user", "password")
            if parameters.get(name) is not None
        )
        return (self.url, *credentials)


@dataclass(frozen=True, slots=True)
class StorageConfig:
    """Connection settings for private S3-compatible object storage."""

    endpoint_url: str
    region: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    connect_timeout_seconds: int = 5
    read_timeout_seconds: int = 10

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> StorageConfig:
        values = _required_values(_environment(environ), STORAGE_ENV_VARS)
        config = cls(
            endpoint_url=values["DATATOWN_S3_ENDPOINT_URL"],
            region=values["DATATOWN_S3_REGION"],
            bucket=values["DATATOWN_S3_BUCKET"],
            access_key_id=values["DATATOWN_S3_ACCESS_KEY_ID"],
            secret_access_key=values["DATATOWN_S3_SECRET_ACCESS_KEY"],
        )
        config.validate_endpoint()
        return config

    def validate_endpoint(self) -> None:
        parsed = urlsplit(self.endpoint_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ConfigurationError(
                "DATATOWN_S3_ENDPOINT_URL must be an absolute http or https URL"
            )
        if parsed.username or parsed.password:
            raise ConfigurationError(
                "DATATOWN_S3_ENDPOINT_URL must not contain credentials; "
                "use the access-key variables"
            )

    def target_description(self) -> str:
        """Return a target label containing no storage credentials."""
        parsed = urlsplit(self.endpoint_url)
        endpoint = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
        return f"{endpoint}/{self.bucket} (region {self.region})"

    def redaction_values(self) -> tuple[str, ...]:
        """Return storage credentials that must never appear in operator output."""
        return (self.access_key_id, self.secret_access_key)


@dataclass(frozen=True, slots=True)
class Settings:
    """Complete Datatown configuration for commands that need both services."""

    database: DatabaseConfig
    storage: StorageConfig

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        environment = _environment(environ)
        return cls(
            database=DatabaseConfig.from_env(environment),
            storage=StorageConfig.from_env(environment),
        )


def redact_secrets(message: str, secrets: tuple[str, ...]) -> str:
    """Replace known non-empty secrets before displaying an exception message."""
    redacted = message
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted
