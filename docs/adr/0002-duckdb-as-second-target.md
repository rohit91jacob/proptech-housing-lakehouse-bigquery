# ADR 0002: DuckDB as a second target for development and CI

**Status:** accepted

## Context

Without GCP credentials, a dbt-bigquery project can only be parsed. Its SQL, tests and
business logic would go unverified until deployment. Every BigQuery run also consumes the
sandbox's lifetime storage quota (ADR 0001).

## Decision

The same loader and dbt project run on **DuckDB** (`PROPTECH_TARGET=duckdb`, dbt target
`local`):

- The Python `Warehouse` interface has two backends that share the same raw layout:
  `BigQueryWarehouse` (load jobs) and `DuckDBWarehouse` (transactional `CREATE OR REPLACE` /
  `INSERT BY NAME`).
- Dialect differences sit behind a few macros in `dbt/macros/platform.sql` and `trends.sql`:
  `safe_divide`, `month_end_from_index`, `type_double`, materialisation and partition helpers.
  Everything else is SQL both engines accept, including `QUALIFY` (always with a `WHERE`, as
  BigQuery requires) and `RANGE` window frames.
- Contract column types use Jinja, so one definition validates on both engines.
- CI runs the full fixture pipeline on DuckDB (ingest → `dbt build` → freshness → docs). It
  also **compiles the BigQuery SQL offline** (`--target offline`, which runs no queries) for
  both the `prod` and `sandbox` flavours, and parses the result with sqlfluff's BigQuery
  dialect.

## Consequences

- Business logic, data tests and unit tests run on every push with no cloud dependency.
  The real-data verification (exact match to Zillow's published CSV, CBSA coverage,
  idempotent reruns) also runs locally.
- BigQuery-specific behaviour (load-job semantics, expiry refresh, partitioning) is covered
  by unit tests against a mocked client. It is only proven live once credentials exist, when
  the `bigquery` CI job runs automatically.
- There is a small risk of semantic drift between engines, for example in NULL ordering and
  float formatting. Models avoid engine-specific functions outside the macros.
