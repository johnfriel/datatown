from __future__ import annotations

import csv
import json
import shutil
import subprocess
from pathlib import Path
from uuid import UUID

import pytest

import datatown.pdl.import_companies as importer
from datatown.config import DatabaseConfig
from datatown.pdl.import_companies import EXPECTED_COLUMNS, JQ_FILTER

JQ = shutil.which("jq")
requires_jq = pytest.mark.skipif(JQ is None, reason="jq is not installed")


def _run_jq(source: Path) -> subprocess.CompletedProcess[str]:
    assert JQ is not None
    return subprocess.run(
        [JQ, "--exit-status", "--unbuffered", "--raw-output", JQ_FILTER, str(source)],
        capture_output=True,
        text=True,
        check=False,
    )


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as destination:
        for record in records:
            json.dump(record, destination, ensure_ascii=False)
            destination.write("\n")


@requires_jq
def test_jq_filter_preserves_csv_values_and_nulls(tmp_path: Path) -> None:
    source = tmp_path / "companies.json"
    _write_jsonl(
        source,
        [
            {
                "id": "company-1",
                "website": "https://example.test/a,b",
                "name": 'Quoted "name"\nwith a newline',
                "founded": 2020,
                "size": "1-10",
                "locality": "",
                "region": None,
                "country": "México",
                "industry": "software",
                "linkedin_url": "linkedin.com/company/example",
            },
            {
                "id": "company-2",
                "website": None,
                "name": "Minimal",
                "founded": None,
                "size": None,
                "locality": None,
                "region": None,
                "country": None,
                "industry": None,
                "linkedin_url": None,
            },
        ],
    )

    result = _run_jq(source)

    assert result.returncode == 0, result.stderr
    rows = list(csv.reader(result.stdout.splitlines(keepends=True)))
    assert all(len(row) == len(EXPECTED_COLUMNS) for row in rows)
    assert rows[0] == [
        "company-1",
        "https://example.test/a,b",
        'Quoted "name"\nwith a newline',
        "2020",
        "1-10",
        "",
        "",
        "México",
        "software",
        "linkedin.com/company/example",
    ]
    assert rows[1] == ["company-2", "", "Minimal", "", "", "", "", "", "", ""]
    # jq's @csv distinguishes null (an unquoted empty field, read by PostgreSQL as NULL) from
    # an empty string (a quoted empty field, preserved as an empty string).
    assert ',"",,' in result.stdout


@requires_jq
@pytest.mark.parametrize(
    ("change", "expected_error"),
    [
        ({"new_vendor_field": "surprise"}, "unexpected field set"),
        ({"founded": "2020"}, "founded must be a PostgreSQL integer or null"),
        ({"id": ""}, "id must be a non-empty string"),
    ],
)
def test_jq_filter_rejects_schema_drift_and_bad_values(
    tmp_path: Path,
    change: dict[str, object],
    expected_error: str,
) -> None:
    source = tmp_path / "companies.json"
    record: dict[str, object] = {
        "id": "company-1",
        "website": None,
        "name": "Example",
        "founded": None,
        "size": "1-10",
        "locality": None,
        "region": None,
        "country": None,
        "industry": None,
        "linkedin_url": "linkedin.com/company/example",
    }
    record.update(change)
    _write_jsonl(source, [record])

    result = _run_jq(source)

    assert result.returncode != 0
    assert "line 1" in result.stderr
    assert expected_error in result.stderr


def test_failure_cleanup_forces_read_write_and_releases_space_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeTransaction:
        def __enter__(self):
            events.append("begin")
            return self

        def __exit__(self, _type, _value, _traceback):
            events.append("commit")
            return False

    class FakeConnection:
        autocommit = False

        def __enter__(self):
            return self

        def __exit__(self, _type, _value, _traceback):
            events.append("close")
            return False

        def transaction(self):
            return FakeTransaction()

        def execute(self, query: str, _parameters=None):
            events.append(" ".join(query.split()))

    connection = FakeConnection()
    monkeypatch.setattr(importer, "connect_database", lambda _config: connection)

    importer._record_failed_import(
        DatabaseConfig("postgresql://example.test/datatown"),
        UUID("d92a9539-ddb1-4bfd-9c22-61532fae598a"),
        RuntimeError("disk full"),
    )

    assert connection.autocommit is True
    assert events == [
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
        "SELECT pg_advisory_lock(hashtext('datatown:pdl:import-companies'))",
        "begin",
        "DROP TABLE IF EXISTS pdl.companies_next",
        "DROP TABLE IF EXISTS pdl.companies_previous",
        "commit",
        "begin",
        (
            "UPDATE meta.import_runs SET status = 'failed', finished_at = now(), error = %s "
            "WHERE id = %s AND status = 'running'"
        ),
        "commit",
        "close",
    ]
