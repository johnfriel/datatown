# PDL free company dataset

This document records bounded inspection of the actual People Data Labs free company files in
`data/` on 2026-08-17. No source file was modified.

## Source artifacts

| Artifact | Format | Size | SHA-256 |
| --- | --- | ---: | --- |
| `free_company_dataset.csv` | CSV with backslash-escaped quotes | 5.24 GiB | `e4b352bc950aca4b355d50deb1a71af7399579fb1ef8537b89e6016a87e9d403` |
| `free_company_dataset.json` | Newline-delimited JSON objects | 9.98 GiB | `e5805ceca8dd025ca36160fcd6f1d5479ecaf817cb48a71cb8393c3ff7b1cde9` |

Both originals are archived. The hashes above were computed with streaming full-file reads and
serve as their immutable identities.

## Phase 4 archive status

Archive completed on 2026-08-17 under snapshot
`ce01d408-a2be-416b-b333-f6aaed39dbdc`. The private bucket contains:

| Role | Object key | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Original CSV | `pdl/company/2026-08-17/free_company_dataset.csv` | 5,629,540,965 | `e4b352bc950aca4b355d50deb1a71af7399579fb1ef8537b89e6016a87e9d403` |
| Original JSONL | `pdl/company/2026-08-17/free_company_dataset.json` | 10,719,027,713 | `e5805ceca8dd025ca36160fcd6f1d5479ecaf817cb48a71cb8393c3ff7b1cde9` |
| Manifest | `pdl/company/2026-08-17/manifest.json` | 763 | `cfaea83117421ae6777e582b99101d09c1e77b4662b0b8373256def13c556719` |

The manifest body hash, object metadata, byte sizes, `meta.dataset_snapshots` row, and both
`meta.dataset_files` rows were independently verified after upload. No multipart uploads remain,
and no import run exists because Phase 4 does not load `pdl.companies`. The source URL was not
available and is recorded as null.

An identical second archive command uploaded nothing, verified all three objects, and returned the
same snapshot ID. To repeat that check:

```bash
uv run --env-file .env datatown pdl archive \
  --csv data/free_company_dataset.csv \
  --json data/free_company_dataset.json \
  --acquired-at 2026-08-17
```

The direct Supabase Storage endpoint produced occasional TLS `bad record mac` errors during the
multi-gigabyte transfer. Datatown therefore uses serialized multipart requests with fresh
connections and bounded per-part SSL retries. A failed part is rewound and retried without
discarding the rest of the multipart object.

## Inspection method

- compared the first 100,000 records from both files using streaming readers;
- retained only counters and a few sample records in memory;
- probed JSON records near 0%, 25%, 50%, 75%, and 99% byte offsets;
- did not count every row or perform a full schema scan.

All five offset probes had the same ten keys. No arrays or objects were observed as field values.

## Observed fields and PostgreSQL mapping

| PDL field | Observed JSON type | Nulls in first 100k | PostgreSQL | Notes |
| --- | --- | ---: | --- | --- |
| `id` | string | 0 | `text PRIMARY KEY` | PDL record/company identifier; 28 characters in offset samples |
| `website` | string/null | 33,923 | `text` | Raw vendor value; some values include paths rather than a bare domain |
| `name` | string | 0 | `text NOT NULL` | Company name |
| `founded` | integer/null | 63,361 | `integer` | Year-like scalar; sampled range 1098–2026 |
| `size` | string | 0 | `text` | Employee range such as `1-10`; do not coerce to one number |
| `locality` | string/null | 17,607 | `text` | Locality/city-like vendor value |
| `region` | string/null | 15,779 | `text` | Region/state-like vendor value |
| `country` | string/null | 11,201 | `text` | Country label as supplied |
| `industry` | string/null | 17,015 | `text` | Scalar vendor label, not an array |
| `linkedin_url` | string | 0 | `text` | Raw LinkedIn company path/URL |

The importer performs full-file validation and rejects an unexpected field set or value type
rather than silently dropping vendor fields. It uses conservative nullability except for the PDL
identifier and name.

## Stable identity and lookup indexes

`id` is the explicit PDL identifier and is the proposed primary key. No duplicate IDs occurred in
the first 100,000-record sample, but uniqueness must be validated across the complete import.

Initial indexes on `pdl.companies`:

- primary-key index on `id`;
- B-tree index on `website` for exact raw-value lookup;
- B-tree index on `linkedin_url` for external-identity lookup;
- an exact or case-normalized name index only after expected name-query behavior is decided.

The first 100,000 JSON records included 1,786 non-null `website` values containing `/`, so
`website` is not reliably a bare domain. Preserve it exactly. A separately specified mechanical
domain normalization or expression index can be added later; do not overwrite the vendor value.

## CSV and JSON comparison

The CSV header and JSON objects contain the same ten fields in different orders. CSV empty cells
correspond to JSON `null`, and CSV-founded strings correspond to JSON integers.

The CSV is not conventional RFC-style CSV: embedded quotes use a backslash escape. With Python's
default CSV dialect, the first malformed-width record occurs at 32,131 and the first ID divergence
follows at record 32,132; malformed quoted fields can absorb following physical lines. Using
`escapechar='\\'` keeps all first 100,000 IDs aligned and produces no malformed-width rows.

Even with that rule, three sampled names differ because literal backslashes are indistinguishable
from escape characters in the CSV representation. JSON preserves those values.

Therefore:

- prefer newline-delimited JSON as the eventual import input;
- retain CSV as a canonical vendor-provided original and development cross-check;
- do not load both representations into PostgreSQL;
- keep the eventual load bulk-oriented despite choosing JSONL.

## Queryable table shape

Migration `002_pdl.sql` creates:

```sql
CREATE TABLE pdl.companies (
    id text PRIMARY KEY,
    website text,
    name text NOT NULL,
    founded integer,
    size text,
    locality text,
    region text,
    country text,
    industry text,
    linkedin_url text
);
```

Snapshot and file provenance belongs in `meta.*`, not as Peoplebot-specific interpretation fields
inside `pdl.companies`.

## Phase 5 import method

The JSONL original is the import input because it preserves values that the vendor CSV's
backslash-quote convention makes ambiguous. The CSV remains an archived canonical original.

`datatown pdl import-companies` first hashes the local JSONL and requires its SHA-256 and byte size
to match an archived `original_json` record. It then:

1. streams every record through `jq`, requiring the exact ten-field schema, scalar/null types,
   non-empty `id` and `name`, and PostgreSQL-range integer values for `founded`;
2. emits standards-compliant CSV directly into PostgreSQL `COPY` for `pdl.companies_next`;
3. builds the primary key and exact-value indexes for `website` and `linkedin_url`;
4. checks the exact PostgreSQL row count and required identifier population; and
5. atomically swaps the validated staging table into `pdl.companies` while marking its import run
   successful.

The primary key also performs the complete duplicate-ID check. JSON null and empty string remain
distinct through `jq`'s CSV encoding and PostgreSQL `COPY`. The importer never constructs the
35-million-record dataset in Python memory and does not create a second 10-GiB raw JSON staging
table in PostgreSQL.

Run the initial archived snapshot with:

```bash
uv run --env-file .env datatown migrate
uv run --env-file .env datatown pdl import-companies \
  --json data/free_company_dataset.json \
  --snapshot-id ce01d408-a2be-416b-b333-f6aaed39dbdc
```

### Initial import result and capacity finding

The first full attempt, import run `d92a9539-ddb1-4bfd-9c22-61532fae598a`, validated and copied all
35,828,989 records. The completed heap measured 6,696 MB, but Supabase exhausted the initially
provisioned database disk while building the final LinkedIn index. The staging table was removed,
the database returned from 8,856 MB to 2,160 MB, the import run is recorded as failed, and the
empty current table was never replaced. This is an infrastructure-capacity failure, not a vendor
schema or data-quality failure.

After the database disk was increased to 32 GB, retry run
`57bb3d69-44fe-44c7-bc7c-2917095d5136` completed in 28 minutes 30 seconds and atomically installed
the table. Final verification on 2026-08-17 found:

| Check | Result |
| --- | ---: |
| Exact PostgreSQL row count | 35,828,989 |
| Table heap | 6,696 MB |
| Primary-key index | 1,699 MB |
| Website index | 865 MB |
| LinkedIn index | 2,244 MB |
| Total `pdl.companies` relation | 11.24 GiB |
| Whole database after import | 13 GiB |

All three constraints are validated, no staging relation remains, and the successful import run
joins through snapshot `ce01d408-a2be-416b-b333-f6aaed39dbdc` to the 10,719,027,713-byte archived
JSONL with SHA-256
`e5805ceca8dd025ca36160fcd6f1d5479ecaf817cb48a71cb8393c3ff7b1cde9`. Read-only
`EXPLAIN ANALYZE` probes selected the intended primary-key, website, and LinkedIn indexes, each
completing in approximately 1.7–1.9 ms during the verification session.

Object-storage file-size settings are separate from PostgreSQL database disk capacity. Before a
future snapshot replacement, check disk headroom again: staging-and-swap temporarily retains the
current 11.24-GiB relation while constructing its replacement.

## Reproduce the inspection

```bash
uv run datatown pdl inspect \
  --csv data/free_company_dataset.csv \
  --json data/free_company_dataset.json
```
