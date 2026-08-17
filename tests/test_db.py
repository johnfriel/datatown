from __future__ import annotations

import pytest

import datatown.db as db
from datatown.config import DatabaseConfig


class FakeCursor:
    def __init__(self, row: tuple[str, str] | None = None) -> None:
        self.row = row

    def fetchone(self) -> tuple[str, str] | None:
        return self.row


class FakeTransaction:
    def __enter__(self) -> None:
        return None

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        return None

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()

    def execute(self, statement: str) -> FakeCursor:
        self.statements.append(statement)
        if statement.startswith("SELECT"):
            return FakeCursor(("datatown", "17.2"))
        return FakeCursor()


def test_probe_database_marks_transaction_read_only_before_querying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    monkeypatch.setattr(db, "connect_database", lambda _config: connection)

    result = db.probe_database(DatabaseConfig("postgresql://unused"))

    assert connection.statements == [
        "SET TRANSACTION READ ONLY",
        "SELECT current_database(), current_setting('server_version')",
    ]
    assert result == db.DatabaseProbeResult(database_name="datatown", server_version="17.2")
