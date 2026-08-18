"""Bulk import the archived PDL company JSONL into an atomically replaced table."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from psycopg import Connection

from datatown import __version__
from datatown.config import DatabaseConfig
from datatown.db import connect_database
from datatown.hashing import sha256_file

EXPECTED_COLUMNS = (
    "id",
    "website",
    "name",
    "founded",
    "size",
    "locality",
    "region",
    "country",
    "industry",
    "linkedin_url",
)
EXPECTED_SCHEMA = (
    ("id", "text", "NO"),
    ("website", "text", "YES"),
    ("name", "text", "NO"),
    ("founded", "integer", "YES"),
    ("size", "text", "YES"),
    ("locality", "text", "YES"),
    ("region", "text", "YES"),
    ("country", "text", "YES"),
    ("industry", "text", "YES"),
    ("linkedin_url", "text", "YES"),
)
COPY_CHUNK_SIZE = 8 * 1024 * 1024
PROGRESS_INTERVAL = 512 * 1024 * 1024
IMPORTER = f"datatown.pdl.import_companies@{__version__}"
ADVISORY_LOCK_SQL = "SELECT pg_advisory_lock(hashtext('datatown:pdl:import-companies'))"
COPY_SQL = """
COPY pdl.companies_next (
    id, website, name, founded, size, locality, region, country, industry, linkedin_url
)
FROM STDIN WITH (FORMAT csv, FREEZE true)
"""
JQ_FILTER = r"""
def fail($message): error("line \(input_line_number): \($message)");
def string_or_null($value): ($value == null or ($value | type) == "string");
def integer_or_null($value):
    ($value == null or
        (($value | type) == "number" and
         ($value | floor) == $value and
         $value >= -2147483648 and
         $value <= 2147483647));

if type != "object" then
    fail("record must be an object")
elif keys != [
    "country", "founded", "id", "industry", "linkedin_url",
    "locality", "name", "region", "size", "website"
] then
    fail("unexpected field set: \(keys | join(","))")
elif ((.id | type) != "string" or (.id | length) == 0) then
    fail("id must be a non-empty string")
elif ((.name | type) != "string" or (.name | length) == 0) then
    fail("name must be a non-empty string")
elif (string_or_null(.website) | not) then
    fail("website must be a string or null")
elif (integer_or_null(.founded) | not) then
    fail("founded must be a PostgreSQL integer or null")
elif (string_or_null(.size) | not) then
    fail("size must be a string or null")
elif (string_or_null(.locality) | not) then
    fail("locality must be a string or null")
elif (string_or_null(.region) | not) then
    fail("region must be a string or null")
elif (string_or_null(.country) | not) then
    fail("country must be a string or null")
elif (string_or_null(.industry) | not) then
    fail("industry must be a string or null")
elif (string_or_null(.linkedin_url) | not) then
    fail("linkedin_url must be a string or null")
else
    [.id, .website, .name, .founded, .size, .locality, .region, .country, .industry, .linkedin_url]
    | @csv
end
"""


class PDLImportError(RuntimeError):
    """Raised when a PDL import cannot be completed without replacing the current table."""


@dataclass(frozen=True, slots=True)
class ArchivedJSONFile:
    snapshot_id: UUID
    file_id: UUID
    sha256: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class ImportResult:
    run_id: UUID
    snapshot_id: UUID
    row_count: int
    relation_size: int
    source_sha256: str


Reporter = Callable[[str], None]


def _uuid(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise PDLImportError(f"Expected UUID from PostgreSQL, received {type(value).__name__}")
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int):
        raise PDLImportError(f"Expected integer from PostgreSQL, received {type(value).__name__}")
    return value


def _resolve_archived_json(
    config: DatabaseConfig,
    *,
    sha256: str,
    byte_size: int,
    snapshot_id: UUID | None,
) -> ArchivedJSONFile:
    with connect_database(config) as connection, connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        row = connection.execute(
            """
            SELECT f.snapshot_id, f.id, f.sha256, f.byte_size
            FROM meta.dataset_files AS f
            JOIN meta.dataset_snapshots AS s ON s.id = f.snapshot_id
            WHERE s.source = 'pdl'
              AND s.dataset = 'company'
              AND f.role = 'original_json'
              AND f.sha256 = %s
              AND f.byte_size = %s
              AND (%s::uuid IS NULL OR f.snapshot_id = %s::uuid)
            """,
            (sha256, byte_size, snapshot_id, snapshot_id),
        ).fetchone()
    if row is None:
        raise PDLImportError(
            "The JSONL file does not match an archived PDL original; run `datatown pdl archive` "
            "first or check --snapshot-id"
        )
    return ArchivedJSONFile(
        snapshot_id=_uuid(row[0]),
        file_id=_uuid(row[1]),
        sha256=str(row[2]),
        byte_size=_integer(row[3]),
    )


def _jq_executable(requested: str) -> str:
    executable = shutil.which(requested)
    if executable is None:
        raise PDLImportError(
            "jq is required for the streaming PDL import but was not found on PATH"
        )
    return executable


def _git_revision() -> str | None:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return f"{revision}-dirty" if dirty else revision


def _verify_target_schema(connection: Connection[tuple[object, ...]]) -> None:
    relation = connection.execute("SELECT to_regclass('pdl.companies')").fetchone()
    if relation is None or relation[0] is None:
        raise PDLImportError("pdl.companies is not initialized; run `datatown migrate` first")
    rows = connection.execute(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'pdl' AND table_name = 'companies'
        ORDER BY ordinal_position
        """
    ).fetchall()
    observed = tuple((str(row[0]), str(row[1]), str(row[2])) for row in rows)
    if observed != EXPECTED_SCHEMA:
        raise PDLImportError(
            "pdl.companies does not match the importer schema; apply the expected migration "
            "before importing"
        )


def _start_import_run(
    connection: Connection[tuple[object, ...]], archived: ArchivedJSONFile
) -> UUID:
    run_id = uuid4()
    with connection.transaction():
        connection.execute(
            """
            UPDATE meta.import_runs
            SET status = 'failed',
                finished_at = now(),
                error = 'Superseded after a prior importer process ended'
            WHERE status = 'running'
              AND target_schema = 'pdl'
              AND target_table = 'companies'
            """
        )
        connection.execute(
            """
            INSERT INTO meta.import_runs (
                id, snapshot_id, status, target_schema, target_table, importer, git_commit
            )
            VALUES (%s, %s, 'running', 'pdl', 'companies', %s, %s)
            """,
            (run_id, archived.snapshot_id, IMPORTER, _git_revision()),
        )
    return run_id


def _cleanup_scratch(connection: Connection[tuple[object, ...]]) -> None:
    with connection.transaction():
        connection.execute("DROP TABLE IF EXISTS pdl.companies_next")
        connection.execute("DROP TABLE IF EXISTS pdl.companies_previous")


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _copy_jsonl(
    connection: Connection[tuple[object, ...]],
    source: Path,
    jq: str,
    report: Reporter,
) -> int:
    process = subprocess.Popen(
        [jq, "--exit-status", "--unbuffered", "--raw-output", JQ_FILTER, str(source)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdout is None or process.stderr is None:
        _stop_process(process)
        raise PDLImportError("Could not open jq output streams")

    transformed_bytes = 0
    next_report = PROGRESS_INTERVAL
    try:
        with connection.cursor() as cursor:
            with cursor.copy(COPY_SQL) as copy:
                while chunk := process.stdout.read(COPY_CHUNK_SIZE):
                    copy.write(chunk)
                    transformed_bytes += len(chunk)
                    if transformed_bytes >= next_report:
                        report(
                            f"sent {transformed_bytes / (1024**3):.1f} GiB of validated CSV "
                            "to PostgreSQL"
                        )
                        next_report += PROGRESS_INTERVAL

            copied_rows = cursor.rowcount

        stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
        return_code = process.wait()
        if return_code != 0:
            detail = stderr[-4000:] if stderr else f"jq exited with status {return_code}"
            raise PDLImportError(f"PDL JSONL validation failed: {detail}")
        if copied_rows < 1:
            raise PDLImportError("PDL JSONL import produced no rows")
        return copied_rows
    finally:
        _stop_process(process)
        process.stdout.close()
        process.stderr.close()


def _load_next_table(
    connection: Connection[tuple[object, ...]],
    source: Path,
    jq: str,
    report: Reporter,
) -> int:
    with connection.transaction():
        connection.execute(
            """
            CREATE TABLE pdl.companies_next (
                LIKE pdl.companies
                INCLUDING DEFAULTS
                INCLUDING CONSTRAINTS
                INCLUDING STORAGE
                INCLUDING COMMENTS
            )
            """
        )
        return _copy_jsonl(connection, source, jq, report)


def _index_and_validate(
    connection: Connection[tuple[object, ...]], copied_rows: int, report: Reporter
) -> tuple[int, int]:
    report("building primary-key index")
    with connection.transaction():
        connection.execute("SET LOCAL maintenance_work_mem = '256MB'")
        connection.execute(
            """
            ALTER TABLE pdl.companies_next
            ADD CONSTRAINT companies_next_pkey PRIMARY KEY (id)
            """
        )
        report("building website lookup index")
        connection.execute(
            """
            CREATE INDEX companies_next_website_idx
            ON pdl.companies_next (website)
            WHERE website IS NOT NULL
            """
        )
        report("building LinkedIn lookup index")
        connection.execute(
            """
            CREATE INDEX companies_next_linkedin_url_idx
            ON pdl.companies_next (linkedin_url)
            WHERE linkedin_url IS NOT NULL
            """
        )

    report("validating row count and required identifiers")
    row = connection.execute(
        """
        SELECT
            count(*),
            count(*) FILTER (WHERE id IS NULL OR id = ''),
            count(*) FILTER (WHERE name IS NULL OR name = '')
        FROM pdl.companies_next
        """
    ).fetchone()
    if row is None:
        raise PDLImportError("Post-import validation returned no result")
    row_count = _integer(row[0])
    missing_ids = _integer(row[1])
    missing_names = _integer(row[2])
    if row_count != copied_rows:
        raise PDLImportError(
            f"COPY reported {copied_rows:,} rows but PostgreSQL contains {row_count:,}"
        )
    if missing_ids or missing_names:
        raise PDLImportError(
            f"Required-field validation failed: {missing_ids} IDs and {missing_names} names"
        )

    with connection.transaction():
        connection.execute("ANALYZE pdl.companies_next")
    size_row = connection.execute("SELECT pg_total_relation_size('pdl.companies_next')").fetchone()
    if size_row is None:
        raise PDLImportError("Could not measure pdl.companies_next")
    return row_count, _integer(size_row[0])


def _swap_current_table(
    connection: Connection[tuple[object, ...]],
    *,
    run_id: UUID,
    row_count: int,
) -> None:
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout = '30s'")
        connection.execute("LOCK TABLE pdl.companies IN ACCESS EXCLUSIVE MODE")
        connection.execute("ALTER TABLE pdl.companies RENAME TO companies_previous")
        connection.execute("ALTER TABLE pdl.companies_next RENAME TO companies")
        connection.execute("DROP TABLE pdl.companies_previous")
        connection.execute(
            """
            ALTER TABLE pdl.companies
            RENAME CONSTRAINT companies_next_pkey TO companies_pkey
            """
        )
        connection.execute(
            """
            ALTER INDEX pdl.companies_next_website_idx
            RENAME TO companies_website_idx
            """
        )
        connection.execute(
            """
            ALTER INDEX pdl.companies_next_linkedin_url_idx
            RENAME TO companies_linkedin_url_idx
            """
        )
        updated = connection.execute(
            """
            UPDATE meta.import_runs
            SET status = 'succeeded', finished_at = now(), row_count = %s
            WHERE id = %s AND status = 'running'
            """,
            (row_count, run_id),
        )
        if updated.rowcount != 1:
            raise PDLImportError(f"Import run {run_id} was not in the running state")


def _record_failed_import(config: DatabaseConfig, run_id: UUID, error: BaseException) -> None:
    message = str(error).strip() or error.__class__.__name__
    with connect_database(config) as connection:
        connection.autocommit = True
        # Supabase can temporarily default new transactions to read-only after a disk-full
        # event. Its documented recovery path permits an explicitly read-write session to
        # remove data, which is exactly what this bounded scratch-table cleanup needs.
        connection.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
        connection.execute(ADVISORY_LOCK_SQL)

        # Commit the space-releasing drop before the metadata update. If the disk is so full
        # that the update still fails, a later import can safely mark the stale run failed.
        with connection.transaction():
            connection.execute("DROP TABLE IF EXISTS pdl.companies_next")
            connection.execute("DROP TABLE IF EXISTS pdl.companies_previous")
        with connection.transaction():
            connection.execute(
                """
                UPDATE meta.import_runs
                SET status = 'failed', finished_at = now(), error = %s
                WHERE id = %s AND status = 'running'
                """,
                (message[-8000:], run_id),
            )


def import_companies(
    config: DatabaseConfig,
    json_path: str | Path,
    *,
    snapshot_id: UUID | None = None,
    jq_executable: str = "jq",
    report: Reporter | None = None,
) -> ImportResult:
    """Validate, bulk-load, index, and atomically replace the current PDL companies table."""
    reporter = report if report is not None else lambda _message: None
    source = Path(json_path)
    if not source.is_file():
        raise PDLImportError(f"PDL JSONL file does not exist: {source}")

    reporter("hashing local JSONL original")
    source_sha256 = sha256_file(source)
    archived = _resolve_archived_json(
        config,
        sha256=source_sha256,
        byte_size=source.stat().st_size,
        snapshot_id=snapshot_id,
    )
    jq = _jq_executable(jq_executable)

    run_id: UUID | None = None
    try:
        with connect_database(config) as connection:
            connection.autocommit = True
            connection.execute("SET statement_timeout = 0")
            connection.execute(ADVISORY_LOCK_SQL)
            _verify_target_schema(connection)
            _cleanup_scratch(connection)
            run_id = _start_import_run(connection, archived)

            reporter(f"import run {run_id} started for snapshot {archived.snapshot_id}")
            copied_rows = _load_next_table(connection, source, jq, reporter)
            reporter(f"COPY loaded {copied_rows:,} validated records")
            row_count, relation_size = _index_and_validate(connection, copied_rows, reporter)
            reporter("atomically replacing pdl.companies")
            _swap_current_table(connection, run_id=run_id, row_count=row_count)
    except BaseException as error:
        if run_id is not None:
            try:
                _record_failed_import(config, run_id, error)
            except Exception as cleanup_error:
                raise PDLImportError(
                    f"Import failed ({error}); cleanup also failed ({cleanup_error})"
                ) from error
        raise

    return ImportResult(
        run_id=run_id,
        snapshot_id=archived.snapshot_id,
        row_count=row_count,
        relation_size=relation_size,
        source_sha256=source_sha256,
    )
