# Datatown

Datatown archives, imports, inspects, and queries third-party datasets used by Peoplebot.
It preserves what external sources say and where those claims came from. It does not decide
which claims Peoplebot should believe, perform entity resolution, or serve application traffic.

The canonical raw representation of a dataset is its original artifact in private object
storage. PostgreSQL contains queryable representations that should be reproducible from those
artifacts and this repository.

## Current scope

Phase 1 provides the Python package, shared configuration/connectivity primitives, streaming
SHA-256 hashing, and the read-only `datatown doctor` command. It does not inspect or import the
local People Data Labs files and does not modify the existing Crunchbase database objects.

Phase 2 adds read-only inspection of the Crunchbase scrape already present in PostgreSQL. The
observed layout and operational caveats are recorded in [docs/crunchbase.md](docs/crunchbase.md).

## Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- Credentials for the Datatown PostgreSQL database and private S3-compatible bucket

## Setup

Install the application and development tools:

```bash
uv sync --all-groups
cp .env.example .env
```

Fill in `.env` with the Datatown service credentials. The file is ignored by Git. Datatown reads
configuration from the process environment; `uv` can load the local file when running a command:

```bash
uv run --env-file .env datatown doctor
```

## Configuration

| Variable | Purpose |
| --- | --- |
| `DATATOWN_DATABASE_URL` | PostgreSQL connection string for the Datatown Supabase project |
| `DATATOWN_S3_ENDPOINT_URL` | Base URL for the S3-compatible API |
| `DATATOWN_S3_REGION` | Storage region used for request signing |
| `DATATOWN_S3_BUCKET` | Private bucket containing source artifacts |
| `DATATOWN_S3_ACCESS_KEY_ID` | S3-compatible access key ID |
| `DATATOWN_S3_SECRET_ACCESS_KEY` | S3-compatible secret access key |

## Connectivity check

`datatown doctor` identifies the configured targets without printing credentials, checks the
database with a read-only transaction, and checks bucket access with the S3 `HeadBucket`
operation. It does not create or modify database rows, schemas, buckets, or objects.

The database and storage checks run independently, so the command reports both results even when
one check fails. It exits with status 0 only when both checks succeed.

```bash
uv run --env-file .env datatown doctor
uv run datatown --help
```

## Crunchbase inspection

Inspect the existing tables using fast PostgreSQL catalog row estimates:

```bash
uv run --env-file .env datatown crunchbase inspect
```

Request bounded exact row counts when an operator needs to verify the estimates:

```bash
uv run --env-file .env datatown crunchbase inspect --exact-counts
```

Both forms use a read-only transaction. Exact counts have a per-table timeout so an inspection
cannot run without a bound on a large relation.

## Development

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

An opt-in integration test exercises the same read-only probes as `doctor`:

```bash
DATATOWN_RUN_INTEGRATION=1 uv run --env-file .env pytest -m integration
```

The real vendor files under `data/` are intentionally ignored. Tests must use small synthetic
inputs rather than PDL or Crunchbase data.
