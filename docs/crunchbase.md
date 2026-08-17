# Existing Crunchbase database

This document records the read-only inspection of the Crunchbase scrape in the Datatown Supabase
PostgreSQL database on 2026-08-17. It describes existing state; it is not a proposed schema.

## Placement

All six Crunchbase-related tables currently live in `public`. There is no `crunchbase` schema.
The Phase 2 inspection did not move, alter, vacuum, analyze, constrain, or index these tables.

The six relations occupy about 2.10 GiB in total. None has a declared primary key, foreign key,
unique constraint, or index. Identifier-looking columns below are therefore logical identifiers
from the loaded data, not database-enforced identities.

## Relations

| Relation | Exact rows | Total size | Important identifier/join columns |
| --- | ---: | ---: | --- |
| `public.consulting_tiers` | 219,210 | 18.19 MiB | `id`, `org_id`, `legacy_id` |
| `public.executives` | 1,790,999 | 481.16 MiB | `uuid`, `org_id`, `linkedin` |
| `public.funding_round_investors` | 1,148,059 | 90.05 MiB | `id`, `funding_round_uuid`, `investor_org_id` |
| `public.funding_rounds` | 688,674 | 161.58 MiB | `uuid`, `org_id` |
| `public.organizations` | 0 | 1.36 GiB | `id`, `legacy_id`, `domain`, `website`, `linkedin` |
| `public.unmatched_references` | 0 | 8.00 KiB | `id`, `source_table`, `source_uuid` |

Exact counts were collected with read-only `COUNT(*)` queries and a 60-second per-table timeout.

## Relationships suggested by the columns

The schema declares no foreign keys, but its column names suggest these joins:

- `executives.org_id` to `organizations.id`
- `funding_rounds.org_id` to `organizations.id`
- `funding_round_investors.funding_round_uuid` to `funding_rounds.uuid`
- `funding_round_investors.investor_org_id` to `organizations.id`
- `consulting_tiers.org_id` to `organizations.id`

These are observations, not validated referential-integrity claims.

## Organizations anomaly

`public.organizations` returns an exact count of zero but still occupies 1,465,319,424 bytes,
almost entirely in its heap. PostgreSQL maintenance statistics also report zero live and zero
dead tuples after an autovacuum on 2026-02-13. This is consistent with an emptied relation whose
heap space has not been returned to the operating system, but the inspection does not establish
how or why the rows disappeared.

Consequences:

- organization/domain lookups currently return no Crunchbase evidence;
- the other tables retain nullable organization UUID references that cannot presently resolve;
- no cleanup or space-reclamation operation should be attempted until the source history and
  original SQL artifact have been investigated.

## Reproduce the inspection

```bash
uv run --env-file .env datatown crunchbase inspect
uv run --env-file .env datatown crunchbase inspect --exact-counts
```

The command reports all non-system schemas and then details every ordinary or partitioned table
currently in `public`, including columns, primary keys, indexes, row counts, and relation sizes.
