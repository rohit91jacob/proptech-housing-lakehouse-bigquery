# ADR 0001: Design for the BigQuery sandbox's limits

**Status:** accepted

## Context

The project runs on the free BigQuery sandbox (no billing account). The current Google docs
state:

> You are granted a lifetime limit of 10 GiB of storage. This quota is not refunded upon
> data deletion. … All BigQuery datasets have a default table expiration time, and all
> tables, views, and partitions automatically expire after 60 days. The BigQuery sandbox
> does not support … Streaming data, Data manipulation language (DML) statements, BigQuery
> Data Transfer Service.

## Decision

1. **No DML anywhere.** Raw loads are Parquet **load jobs**: `WRITE_TRUNCATE` to replace a
   table, `WRITE_APPEND` for manifest, ledger, rejects and forecast history. dbt uses only
   `table` and `view` materialisations, so there is no incremental `MERGE`.
2. **No time partitioning on the sandbox.** Partitions expire 60 days after their partition
   date, so month-partitioned history would be deleted. Tables are clustered by `region_id`
   instead. Month partitioning turns on automatically for `prod` (billing-enabled).
3. **Expiry is refreshed, not fought.** Each run re-stamps raw and ops table expiry to
   `now + 59 days` with a metadata update. Unchanged files are refreshed this way too. dbt
   re-creates its own relations on every run.
4. **Storage is a lifetime budget.**
   - Long monthly time-series marts are **views** on the `sandbox` target and tables
     elsewhere.
   - The CI target builds **only views** in `ci_` datasets.
   - Unchanged sources are skipped with ETags and sha256, so they cost no storage.
   - Raw tables are normalised: values `(region_id, date, value)` are stored apart from
     region metadata.
   - `ops.storage_ledger` estimates the logical bytes written, and loads stop at
     `PROPTECH_STORAGE_BUDGET_GIB` (default 8 of 10).
   - Tests never `store_failures`.
5. **Raw vintages are archived outside BigQuery.** BigQuery only holds the latest vintage,
   because Zillow restates history monthly. Every downloaded file is archived, gzip-compressed
   and immutable, to `PROPTECH_RAW_ARCHIVE_URI` (local disk, or GCS on billing-enabled
   projects).

## Observed on a live sandbox (October 2026)

- Datasets get a forced 60-day default table and partition expiration.
- Patching a table's `expires` is accepted, so the 59-day re-stamp works.
- Parquet load jobs (`WRITE_TRUNCATE` and `WRITE_APPEND`), `CREATE OR REPLACE TABLE/VIEW`,
  and metadata reads all work.
- A full core load plus `dbt build` stores 103.25 MiB. The ledger's estimate is 103.54 MiB.
- One `dbt build` bills 23.6 GiB of query allowance for 1.07 GiB processed, because of the
  10 MiB minimum per table per query. This is why BigQuery CI runs only on `main` and on demand.

## Consequences

- A core-profile run that changes every file writes about 100 MiB of logical storage, so
  monthly runs last several years inside the quota. Time-series queries recompute from views,
  which is cheap against the 1 TiB/month query allowance at this data size.
- The ledger is an estimate. Google doesn't expose the sandbox counter. Writes not made by
  the pipeline (e.g. ad-hoc `CREATE TABLE`) aren't counted.
- Upgrading to billing is a configuration change: `PROPTECH_ENV=prod` and `DBT_TARGET=prod`.
  Code doesn't change.
