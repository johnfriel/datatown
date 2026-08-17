# Dataset provenance metadata

Phase 4 creates a deliberately small `meta` schema. These tables describe archived source
artifacts; they do not contain Peoplebot entities or vendor records.

## Tables

- `meta.schema_migrations` records the filename and SHA-256 of every applied SQL migration. An
  already-applied migration whose bundled checksum changes is rejected.
- `meta.dataset_snapshots` identifies one acquisition of a source dataset and points to its
  object-storage manifest.
- `meta.dataset_files` records each source file's role, original filename, object key, format,
  SHA-256, and byte size.
- `meta.import_runs` is reserved for attempts to build queryable source tables from a snapshot.
  Phase 4 creates it but does not write import runs.

Snapshot identity is unique by source, dataset, and acquisition timestamp. Object keys and source
file SHA-256 values are also unique. An exact repeated archive is verified and reused; conflicting
metadata or an occupied object key fails visibly.

## Applying migrations

```bash
uv run --env-file .env datatown migrate
```

The runner obtains a transaction-scoped advisory lock, initializes migration state, applies all
pending bundled SQL in order, and commits atomically. Running it again reports each migration as
already applied.

## Archive failure behavior

PDL archival performs a read-only metadata preflight before uploading. Each original is uploaded
without local transformation and checked again for local file changes before and after transfer.
The manifest is uploaded only after both originals are present and verified; PostgreSQL metadata
is committed last.

Supabase evaluates its project-wide and bucket-level file-size limits cumulatively during an S3
multipart upload. The configured limit must exceed the largest original; for the initial PDL JSON
artifact, set the global limit to at least `11 GB` before archiving.

Consequently, interruption can leave verified objects without a metadata row, but cannot leave a
metadata row claiming that missing objects were archived. Re-running the same command reuses the
verified objects and completes the remaining steps. Datatown never silently overwrites a remote
object whose recorded size or SHA-256 metadata differs.
