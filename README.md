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

Phase 3 adds bounded, streaming inspection and comparison of the actual PDL company CSV and JSON
artifacts. The observed schema and proposed PostgreSQL mapping are in [docs/pdl.md](docs/pdl.md).

Phase 4 adds the small `meta.*` provenance schema and a command that archives both untouched PDL
originals to private S3-compatible storage. It records the snapshot only after every object has
been uploaded or matched to an already archived object and verified. The metadata design is
documented in [docs/metadata.md](docs/metadata.md).

Phase 5 adds the typed `pdl.companies` representation and a bulk JSONL importer. It validates the
complete upstream schema and scalar types, loads a separate staging table through PostgreSQL
`COPY`, builds and checks indexes, and only then atomically replaces the current table. The exact
source mapping and initial archive/import record are in [docs/pdl.md](docs/pdl.md). The initial
archived snapshot is loaded with 35,828,989 companies.

## Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- `jq` for streaming validation and transformation of PDL JSONL imports
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

## PDL file inspection

Inspect the actual files without loading either multi-gigabyte artifact into memory:

```bash
uv run datatown pdl inspect \
  --csv data/free_company_dataset.csv \
  --json data/free_company_dataset.json
```

The default comparison streams the first 100,000 records and probes JSON structure at several
byte offsets. Full-file SHA-256 hashing is opt-in because it must read both files completely:

```bash
uv run datatown pdl inspect \
  --csv data/free_company_dataset.csv \
  --json data/free_company_dataset.json \
  --sha256
```

## Database migrations and PDL archival

Apply the bundled, checksum-verified migrations for Datatown's `meta` and source schemas:

```bash
uv run --env-file .env datatown migrate
```

Archive both vendor-provided originals under one acquisition snapshot:

```bash
uv run --env-file .env datatown pdl archive \
  --csv data/free_company_dataset.csv \
  --json data/free_company_dataset.json \
  --acquired-at 2026-08-17
```

Use `--source-url` when the vendor download URL is known. The command streams both files to
compute SHA-256, shows the exact targets and identities, uploads large files with multipart S3
transfers, verifies object size and hash metadata, uploads `manifest.json`, then records the
snapshot and files in PostgreSQL. It never replaces an object at an occupied key. Repeating the
exact command verifies and reuses the existing objects and metadata.

The configured bucket must be private. Datatown uses ordinary S3-compatible calls and the layout
`pdl/company/<acquisition-date>/...`; it does not use Supabase-specific storage APIs.

For the current PDL originals, Supabase Storage's project-wide **Global file size limit** must be
larger than 10,719,027,713 bytes; use at least `11 GB` (or `20 GB` for headroom). A bucket-level
limit, if enabled, must also be at least that large. Supabase's Free plan cannot be configured
high enough for these artifacts. See the official [Supabase file-limit
documentation](https://supabase.com/docs/guides/storage/uploads/file-limits).

## PDL company import

Import the archived JSONL original into the typed queryable representation:

```bash
uv run --env-file .env datatown pdl import-companies \
  --json data/free_company_dataset.json \
  --snapshot-id ce01d408-a2be-416b-b333-f6aaed39dbdc
```

The local file's SHA-256 and byte size must match an archived `original_json` metadata record.
`--snapshot-id` is optional, but using it makes the intended acquisition explicit. The importer
requires the exact ten-field PDL schema documented below; an added, removed, or incorrectly typed
field fails the import visibly instead of being discarded.

The importer streams JSONL through `jq` into PostgreSQL's CSV `COPY` protocol. Python transfers
bounded chunks rather than constructing one object per vendor row. Data first lands in
`pdl.companies_next`; primary-key, website, and LinkedIn indexes plus exact count and required-field
checks are completed there. A short final transaction swaps it into `pdl.companies` and marks the
`meta.import_runs` row successful. Until that transaction commits, an existing current table is
left intact. Failed or interrupted attempts are recorded and the staging table is removed.

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
