# PDL free company dataset

This document records bounded inspection of the actual People Data Labs free company files in
`data/` on 2026-08-17. No source file was modified.

## Source artifacts

| Artifact | Format | Size | SHA-256 |
| --- | --- | ---: | --- |
| `free_company_dataset.csv` | CSV with backslash-escaped quotes | 5.24 GiB | `e4b352bc950aca4b355d50deb1a71af7399579fb1ef8537b89e6016a87e9d403` |
| `free_company_dataset.json` | Newline-delimited JSON objects | 9.98 GiB | `e5805ceca8dd025ca36160fcd6f1d5479ecaf817cb48a71cb8393c3ff7b1cde9` |

Both originals should be archived. The hashes above were computed with streaming full-file reads
and can be used as their immutable identities during the archive phase.

## Inspection method

- compared the first 100,000 records from both files using streaming readers;
- retained only counters and a few sample records in memory;
- probed JSON records near 0%, 25%, 50%, 75%, and 99% byte offsets;
- did not count every row or perform a full schema scan.

All five offset probes had the same ten keys. No arrays or objects were observed as field values.

## Observed fields and proposed PostgreSQL mapping

| PDL field | Observed JSON type | Nulls in first 100k | Proposed PostgreSQL | Notes |
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

The importer must perform a full-file validation before relying on sampled nullability or adding
`NOT NULL` constraints beyond `id` and `name`. It should reject an unexpected field set rather
than silently dropping new vendor fields.

## Stable identity and lookup indexes

`id` is the explicit PDL identifier and is the proposed primary key. No duplicate IDs occurred in
the first 100,000-record sample, but uniqueness must be validated across the complete import.

Initial indexes proposed for the eventual `pdl.companies` table:

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

## Proposed table shape

This is a Phase 3 proposal, not an applied migration:

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

## Reproduce the inspection

```bash
uv run datatown pdl inspect \
  --csv data/free_company_dataset.csv \
  --json data/free_company_dataset.json
```
