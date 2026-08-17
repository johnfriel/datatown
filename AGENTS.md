# Datatown

Datatown is a small Python codebase for **archiving, importing, inspecting, and querying third-party datasets used by Peoplebot**.

It is intentionally **not** the Peoplebot knowledgebase and is not part of the Peoplebot application runtime.

The core distinction is:

> **Datatown records what external data sources say. Peoplebot decides what to believe.**

For example, if People Data Labs says that a company has 137 employees, Datatown should make that fact easy to retrieve while preserving its provenance. It should not decide whether 137 is the correct Peoplebot estimate.

---

# 1. Goals

Datatown should:

1. Preserve original third-party dataset files in private object storage.
2. Load useful representations of those datasets into PostgreSQL.
3. Keep each source logically isolated and recognizably shaped like the upstream dataset.
4. Make large datasets easy to inspect and query.
5. Make imports reproducible.
6. Record enough provenance to answer questions such as:
   - Where did this dataset come from?
   - When was it acquired?
   - Which exact file was imported?
   - What SHA-256 hash did the original file have?
   - How many rows were loaded?
   - Which importer/version performed the load?
7. Provide simple command-line tools for occasional operator use.
8. Remain simple enough that source-specific import scripts are acceptable.

Datatown will initially support:

- **Crunchbase**
  - Existing scrape already loaded into the Supabase-hosted PostgreSQL database.
  - Original source was supplied as SQL by a third party.
- **People Data Labs (PDL)**
  - Free company dataset.
  - Both JSON and CSV versions currently exist as local files.
  - These need to be archived and imported.

Additional sources are expected later.

---

# 2. Non-goals

Do **not** turn Datatown into:

- the Peoplebot knowledge graph;
- an entity-resolution system;
- a canonical company/person database;
- a production microservice;
- an HTTP API;
- a workflow engine;
- a general-purpose ETL framework;
- a generic schema-inference system;
- an analytics warehouse for Peoplebot application events;
- a package that Peoplebot must import as a Python dependency.

Avoid abstractions whose only justification is that they might support hypothetical future datasets.

A source-specific script containing some boring PDL-specific logic is preferable to prematurely building an ETL framework.

---

# 3. Architectural model

There are three conceptual layers.

```text
                 THIRD-PARTY DATA

        ┌────────────────────────────┐
        │ Original source artifacts  │
        │                            │
        │ CSV / JSON / SQL / etc.    │
        │ Private object storage     │
        └─────────────┬──────────────┘
                      │
                      │ Datatown
                      ▼
        ┌────────────────────────────┐
        │ Queryable source database  │
        │                            │
        │ pdl.*                      │
        │ crunchbase.*               │
        │ future_source.*            │
        └─────────────┬──────────────┘
                      │
                      │ consumed as evidence
                      ▼
        ┌────────────────────────────┐
        │ Peoplebot                  │
        │                            │
        │ people                     │
        │ companies                  │
        │ employments                │
        │ knowledge/projections      │
        └────────────────────────────┘
```

## Canonical raw data

The **original files in object storage are the canonical raw representation**.

PostgreSQL tables are queryable, rebuildable representations.

Therefore:

> It should always be possible, in principle, to delete a source's PostgreSQL tables and recreate them from archived source artifacts plus the Datatown repository.

Do not rely on PostgreSQL as the only surviving copy of a vendor dataset.

---

# 4. Infrastructure

The PostgreSQL database is hosted by Supabase.

The Supabase project should be regarded as the **Datatown source database**.

Its display/project name is:

```text
datatown
```

It was previously called:

```text
brunch_craze
```

The existing Crunchbase data must be treated carefully: do not destroy, rewrite, or reorganize it merely to make the new repository aesthetically cleaner.

First inspect what already exists.

Eventually the desired database organization is approximately:

```text
meta.*
pdl.*
crunchbase.*
```

If the existing Crunchbase tables currently live somewhere else, such as `public`, leave them alone during the initial implementation. Moving them to a `crunchbase` schema can be a separate explicit migration after their current structure has been inspected.

---

# 5. Object storage

Use private S3-compatible object storage. Supabase Storage is the expected initial backend.

Do not couple the implementation tightly to Supabase-specific APIs when ordinary S3-compatible APIs suffice.

Suggested logical layout:

```text
third-party-datasets/
  pdl/
    company/
      2026-08-17/
        original.csv
        original.json
        manifest.json

  crunchbase/
    <snapshot-or-acquisition-date>/
      original.sql
      manifest.json
```

Actual original filenames may be retained where useful.

Never modify an original file before archiving it.

Derived artifacts may later live alongside originals, for example:

```text
companies.parquet
```

but they must be clearly distinguishable from originals.

## Manifests

Record metadata including, when available:

```json
{
  "source": "pdl",
  "dataset": "company",
  "acquired_at": "2026-08-17T...",
  "source_url": "...",
  "files": [
    {
      "role": "original_csv",
      "filename": "...",
      "sha256": "...",
      "bytes": 123
    },
    {
      "role": "original_json",
      "filename": "...",
      "sha256": "...",
      "bytes": 123
    }
  ]
}
```

The PostgreSQL metadata database should contain equivalent or complementary provenance.

---

# 6. Repository structure

Start simple.

A reasonable initial structure is:

```text
datatown/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── .gitignore
├── .env.example
│
├── migrations/
│   ├── 001_meta.sql
│   └── ...
│
├── src/
│   └── datatown/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── db.py
│       ├── storage.py
│       ├── hashing.py
│       │
│       ├── metadata/
│       │   └── ...
│       │
│       ├── pdl/
│       │   ├── __init__.py
│       │   ├── inspect.py
│       │   ├── archive.py
│       │   └── import_companies.py
│       │
│       └── crunchbase/
│           ├── __init__.py
│           └── inspect.py
│
└── tests/
    ├── ...
    └── fixtures/
```

This is guidance, not a mandate. Prefer fewer files until additional structure pays for itself.

---

# 7. Python approach

Use modern Python with a conventional `pyproject.toml`.

Favor boring, well-supported dependencies.

Likely useful dependencies include:

- `psycopg` for PostgreSQL;
- an S3-compatible client such as `boto3`;
- `typer` or similarly lightweight tooling for the CLI;
- `pytest` for tests;
- `ruff` for formatting/linting.

Do not add Pandas merely by default.

For large-file operations, prefer:

- PostgreSQL `COPY`;
- streaming I/O;
- DuckDB, Polars, PyArrow, or similar tools when they provide a concrete benefit.

Do not iterate through millions of records in ordinary Python merely because it is easy to write.

Performance matters primarily for bulk operations, but simplicity is more important than micro-optimization.

---

# 8. Configuration and secrets

All credentials must come from environment variables.

Possible configuration:

```text
DATATOWN_DATABASE_URL=

DATATOWN_S3_ENDPOINT_URL=
DATATOWN_S3_REGION=
DATATOWN_S3_BUCKET=
DATATOWN_S3_ACCESS_KEY_ID=
DATATOWN_S3_SECRET_ACCESS_KEY=
```

Names can be adjusted if a cleaner convention emerges.

Provide `.env.example`.

Never commit:

- `.env`;
- passwords;
- Supabase service credentials;
- database dumps;
- downloaded PDL data;
- Crunchbase source data;
- large derived artifacts.

The repository should contain **code describing how data is loaded, not the licensed/vendor data itself**.

Test fixtures must be synthetic or sufficiently tiny and legally safe to commit.

---

# 9. Database conventions

Use one PostgreSQL schema per upstream source:

```text
pdl
crunchbase
```

Use:

```text
meta
```

for Datatown's own bookkeeping.

The source schemas should remain relatively faithful to their vendors.

For example:

```text
pdl.companies
```

should mean something close to:

> A queryable representation of PDL's company dataset.

It should not mean:

> Peoplebot's interpretation of companies after reconciling PDL with other evidence.

Similarly, avoid Peoplebot-specific concepts such as:

```text
canonical_company
canonical_employee_count
peoplebot_company_id
best_domain
```

inside the source representation unless there is a very specific reason.

Mechanical normalization is encouraged:

- sensible PostgreSQL types;
- snake_case column names;
- `jsonb` for genuinely structured JSON;
- parsed integers/numerics/booleans;
- PostgreSQL arrays where appropriate;
- indexes on important vendor identifiers and lookup keys.

Semantic reconciliation belongs elsewhere.

---

# 10. Metadata schema

Create a small metadata system.

Do not over-engineer it.

At minimum we should be able to represent:

## `meta.dataset_snapshots`

One logical acquisition/version of a source dataset.

Possible fields:

```text
id
source
dataset
acquired_at
source_url
description
created_at
```

Potential examples:

```text
source = 'pdl'
dataset = 'company'
```

## `meta.dataset_files`

Files belonging to a snapshot.

Possible fields:

```text
id
snapshot_id
role
filename
object_key
format
sha256
byte_size
created_at
```

Example roles:

```text
original_csv
original_json
original_sql
derived_parquet
```

## `meta.import_runs`

Record attempts to turn a snapshot into PostgreSQL tables.

Possible fields:

```text
id
snapshot_id
started_at
finished_at
status
target_schema
target_table
row_count
importer
git_commit
error
```

Use ordinary PostgreSQL types and foreign keys.

Do not create a heavyweight provenance framework.

---

# 11. Snapshot philosophy

Object storage may retain **every snapshot indefinitely**.

PostgreSQL does not necessarily need to.

For a large vendor dataset, the default model should be:

```text
Object storage:
    snapshot A
    snapshot B
    snapshot C
    snapshot D

PostgreSQL:
    current queryable snapshot
```

Historical PostgreSQL tables should only be retained when there is a concrete reason.

The metadata tables should make it clear which archived snapshot currently backs a source table.

---

# 12. Import philosophy

Imports must be:

- explicit;
- inspectable;
- reasonably restartable;
- safe against accidental partial replacement;
- deterministic enough to reproduce;
- able to handle large files without loading them entirely into RAM.

For snapshot-style datasets, prefer a workflow resembling:

```text
local source file
      ↓
validate / inspect
      ↓
archive original
      ↓
record snapshot metadata
      ↓
load into temporary/staging relation
      ↓
validate row counts / important invariants
      ↓
replace current table
      ↓
create indexes
      ↓
record successful import
```

Avoid leaving the canonical queryable table half-loaded if an import crashes.

A straightforward strategy such as:

```text
pdl.companies_next
```

followed by an atomic rename/swap is acceptable.

Do not build generic orchestration infrastructure.

---

# 13. PDL company dataset

This is the first new source to implement.

The user currently has **both CSV and JSON versions on the local laptop**.

## Important rule

Do not invent the PDL schema from memory or assumptions.

The implementation should inspect the actual downloaded files.

Before defining `pdl.companies`:

1. inspect the CSV header;
2. inspect representative records;
3. inspect the JSON structure;
4. compare CSV and JSON representations;
5. determine which fields are scalars versus structured values;
6. identify PDL's stable record/company identifier;
7. identify useful lookup fields.

The importer should then explicitly encode the observed dataset schema.

## Source files

Archive **both** downloaded PDL files as originals.

The CSV will probably be the most convenient bulk-import source.

The JSON file is valuable as:

- another canonical vendor-provided representation;
- a way to understand fields that may be awkwardly serialized into CSV;
- a cross-check during importer development.

Do not load duplicate CSV and JSON copies into PostgreSQL.

Pick one representation as the import input after inspecting both.

## Initial indexes

After inspecting the actual schema, strongly consider indexes for fields corresponding to:

- PDL company ID;
- website/domain;
- LinkedIn company URL or identifier;
- company name, if justified by expected queries.

Do not blindly index every column.

One of the main intended Datatown workloads is lookup/join by company identity signals such as domains and external IDs.

---

# 14. Crunchbase

The Crunchbase scrape already exists in PostgreSQL.

It originated as SQL supplied by a friend.

Initial Crunchbase work should therefore be **inspection and documentation**, not re-import.

Implement tooling that can report:

- existing schemas;
- Crunchbase tables;
- approximate/exact row counts where practical;
- columns;
- primary keys;
- indexes;
- total relation sizes.

Do not alter the Crunchbase data during the first milestone.

Later we can decide whether:

1. to leave the existing structure unchanged;
2. to move existing tables into `crunchbase`;
3. to archive the original SQL file if it is still available;
4. to create indexes or views useful to Datatown consumers.

---

# 15. CLI

Datatown should expose a small operator-oriented CLI.

The exact syntax may evolve, but aim for something like:

```bash
datatown doctor
datatown status
```

PDL:

```bash
datatown pdl inspect --csv /path/to/file.csv --json /path/to/file.json

datatown pdl archive \
  --csv /path/to/file.csv \
  --json /path/to/file.json

datatown pdl import-companies --csv /path/to/file.csv

datatown pdl status
```

Crunchbase:

```bash
datatown crunchbase inspect
```

A higher-level convenience command may eventually combine the PDL steps:

```bash
datatown pdl ingest \
  --csv /path/to/file.csv \
  --json /path/to/file.json
```

But keep lower-level operations available enough to diagnose failures.

## CLI behavior

Commands should:

- print what they are doing;
- show important paths/table names;
- display row counts where useful;
- fail loudly;
- return non-zero exit status on failure;
- avoid dumping secrets;
- avoid enormous amounts of per-row logging.

Provide `--help`.

For destructive/replacement operations, make the action obvious.

---

# 16. File inspection

Large source files must not be assumed to fit in memory.

Inspection commands should use techniques such as:

- reading the first N records;
- streaming parsers;
- file size/stat information;
- sampling deliberately rather than loading everything;
- counting rows only when specifically requested or as part of an import.

Useful inspection output might include:

```text
Path
Format
Byte size
SHA-256
Columns
Sample records
Observed JSON value types
Potential identifiers
```

Do not perform expensive full-file scans just to produce trivial metadata if the information can be obtained cheaply.

---

# 17. Hashing

Compute SHA-256 hashes of archived source files.

Hash files using streaming reads rather than reading the whole file into memory.

A file's hash serves as its immutable identity.

Archiving the same exact artifact twice should not silently create ambiguous duplicate snapshot records.

Do not rely solely on filenames.

---

# 18. Database loading

For large CSV imports, prefer PostgreSQL's bulk-loading facilities rather than millions of individual `INSERT`s.

Where necessary:

1. create a staging table;
2. use `COPY`;
3. transform/cast into the final typed table with SQL.

This is often preferable to parsing every row in Python.

If vendor CSV serialization requires source-specific preprocessing, implement that preprocessing explicitly.

The importer may use temporary local intermediate files if required, but should clean them up and should not commit them to Git.

---

# 19. Data typing

The PostgreSQL representation should be query-friendly.

Do not make every field `text` merely because the source is CSV.

However, typing should be conservative.

Prefer:

```text
text
bigint
numeric
boolean
date
timestamptz
jsonb
text[]
```

when clearly supported by the actual data.

If a supposedly numeric vendor field contains unusual sentinel values or inconsistent representations, preserving it as text may be better than inventing semantics.

Document surprising coercions.

---

# 20. Failure safety

Large imports will occasionally fail.

Design so that:

```text
CTRL-C
network interruption
bad row
database disconnect
disk problem
```

does not leave `pdl.companies` containing an apparently valid but incomplete dataset.

Favor staging-and-swap rather than:

```sql
TRUNCATE pdl.companies;
-- spend 45 minutes loading...
```

with no recovery plan.

Keep failed import-run metadata where practical.

---

# 21. Idempotency

Repeated operations should behave sensibly.

Examples:

- Archiving a file already present with the same SHA-256 should recognize that fact.
- Running migrations twice should not corrupt the database.
- An import should identify the snapshot/file it is importing.
- Retrying a failed import should be straightforward.

Do not contort the implementation to make every command mathematically idempotent. Aim for operator-friendly repeatability.

---

# 22. Testing strategy

Most tests should not require the real PDL or Crunchbase datasets.

Use small synthetic fixtures representing:

- valid CSV;
- valid JSON;
- awkward quoting;
- null values;
- arrays/JSON encoded inside CSV cells;
- malformed input;
- duplicate identities if relevant.

Test:

- streaming SHA-256;
- manifest creation;
- metadata behavior;
- representative parser behavior;
- SQL generation where appropriate.

Integration tests against PostgreSQL are valuable.

Avoid mocks when spinning up or connecting to a disposable PostgreSQL database gives a much more meaningful test.

Never make the normal test suite modify the production Datatown database.

---

# 23. Documentation

Create a conventional `README.md` for humans.

It should eventually cover:

- what Datatown is;
- architectural boundary with Peoplebot;
- local setup;
- environment variables;
- CLI usage;
- current sources;
- how to add a new source;
- how snapshot/archive/import semantics work;
- operational notes.

Source-specific documentation can live near the relevant source code or in a small `docs/` directory if it becomes substantial.

Keep documentation close to reality. Do not document elaborate future infrastructure that does not exist.

---

# 24. Relationship with Peoplebot

Peoplebot may connect directly to the Datatown PostgreSQL database as a consumer.

For example, Peoplebot might eventually execute the conceptual equivalent of:

```sql
SELECT *
FROM pdl.companies
WHERE domain = $1;
```

Peoplebot owns:

- when such a query is made;
- how results are matched;
- confidence/reconciliation;
- fallbacks among sources;
- how facts enter the Peoplebot knowledgebase;
- caching in Peoplebot;
- provenance presented to Peoplebot users.

Datatown owns:

- faithfully representing PDL;
- faithfully representing Crunchbase;
- keeping source data accessible;
- source-specific indexes;
- acquisition/import provenance.

Do not create bidirectional coupling.

Datatown should never need to connect to the main Peoplebot database.

---

# 25. Schema evolution

Vendor schemas can change.

Treat this as expected.

Source importers should be explicit enough that a changed header/schema causes a visible failure or warning rather than silently dropping new columns.

When practical, record information about the observed upstream schema.

For example, an importer may validate the CSV column set against the set it expects.

If PDL adds or removes columns later, update the Datatown importer and migration deliberately.

---

# 26. Adding future data sources

Do not create a plugin framework.

A future source can simply get something like:

```text
src/datatown/new_vendor/
```

plus:

```text
new_vendor.*
```

in PostgreSQL.

Common functionality should be extracted only when at least two sources actually require substantially the same behavior.

Shared primitives that already obviously belong in common code include:

- configuration;
- DB connections;
- object storage;
- hashing;
- metadata records;
- logging/output helpers.

Vendor parsing should remain vendor-specific.

---

# 27. Operational safety

This repository manages valuable but reconstructible datasets.

Still, default toward safety.

Before destructive database operations:

- make the target database obvious;
- make the target schema/table obvious;
- verify staging data before replacing the current version.

Provide a command such as:

```bash
datatown doctor
```

that clearly identifies:

```text
Database host
Database name
Object-storage bucket
Connectivity
```

without printing credentials.

This is especially important because imports may be run manually from a laptop.

---

# 28. Style philosophy

Optimize for:

1. comprehensibility six months from now;
2. reproducibility;
3. boring code;
4. explicit source-specific behavior;
5. good failure messages.

Do not optimize for:

- maximal abstraction;
- clever class hierarchies;
- framework purity;
- distributed execution;
- supporting arbitrary vendor formats;
- eliminating every line of duplication.

This is infrastructure for occasional bulk data operations, not a product surface.

Comments should explain **why**, especially around odd vendor behavior.

---

# 29. Initial implementation milestone

Implement the repository incrementally.

## Phase 1 — Bootstrap

Create:

- `pyproject.toml`;
- package structure;
- CLI;
- configuration;
- DB connection helper;
- S3-compatible storage helper;
- streaming SHA-256 helper;
- `.gitignore`;
- `.env.example`;
- initial README;
- tests for core primitives.

Implement:

```bash
datatown doctor
```

Acceptance criterion:

> From the development laptop, Datatown can verify connectivity to the existing Supabase PostgreSQL database and configured object-storage bucket without modifying either.

---

## Phase 2 — Inspect existing Crunchbase data

Implement:

```bash
datatown crunchbase inspect
```

Use PostgreSQL metadata to understand the current database.

Report the Crunchbase layout without changing it.

Write the important discoveries into repository documentation.

Acceptance criterion:

> We understand exactly which existing tables make up the Crunchbase scrape, their rough sizes, important identifiers, and current schema placement.

---

## Phase 3 — Inspect PDL files

Implement:

```bash
datatown pdl inspect \
  --csv <path> \
  --json <path>
```

Inspect the actual local files.

Compare their structures.

Do not yet assume the final PostgreSQL schema.

Produce enough information to decide:

- expected columns;
- types;
- stable identifiers;
- nested structures;
- likely useful indexes;
- preferred import representation.

Acceptance criterion:

> The repository contains an explicit documented mapping from the actual PDL company dataset to the proposed `pdl.companies` PostgreSQL table.

---

## Phase 4 — Metadata and archive

Create the `meta` schema and minimal metadata tables.

Implement PDL archival.

Archive both original PDL files privately.

Compute SHA-256 before/during upload and record metadata.

Acceptance criterion:

> The local copies could be deleted and the exact downloaded originals could later be identified and recovered from object storage.

---

## Phase 5 — PDL import

Create:

```text
pdl.companies
```

based on actual file inspection.

Implement the import using a bulk-oriented strategy.

Validate at minimum:

- imported row count;
- expected identifier population;
- absence of catastrophic parse failure;
- successful indexes.

Use staging-and-swap or another strategy that prevents a failed import from masquerading as a complete current dataset.

Acceptance criterion:

> A PDL company can be efficiently retrieved from PostgreSQL using its important external identifiers/domain fields.

---

## Phase 6 — Status and ergonomics

Implement:

```bash
datatown status
datatown pdl status
```

Show:

- known datasets;
- archived snapshot;
- current imported snapshot;
- row count;
- file hashes;
- import date;
- relevant relation sizes.

At this point, evaluate whether a convenience command such as:

```bash
datatown pdl ingest ...
```

would simplify future updates.

Do not build it merely for symmetry.

---

# 30. First task for Codex

Start with **Phase 1 only**.

Do not implement PDL schema/import logic until the actual PDL files have been inspected.

Do not modify the existing Crunchbase tables.

Bootstrap the Python repository, implement configuration and connectivity primitives, and create the `datatown doctor` command.

When Phase 1 is complete:

1. run the tests;
2. run lint/type checks that have been configured;
3. summarize the resulting repository structure;
4. explain any architectural deviations from this document;
5. stop before making source-data mutations.

The next task will be inspection of the existing Crunchbase database and the local PDL files.