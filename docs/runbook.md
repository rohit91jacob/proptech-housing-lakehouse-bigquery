# Runbook

Operational procedures for the `pipeline` workflow (`.github/workflows/pipeline.yml`).
Commands run from the repo root. The BigQuery commands need the credentials described in
[gcp_setup.md](gcp_setup.md).

> **Verification status.** Every DuckDB command in this runbook has been run against real
> source data. The BigQuery paths are covered by unit tests against a mocked client and by an
> offline BigQuery compile in CI. They have **not** yet been run against a live GCP project.

## Normal operation

- **Schedule:** the 18th of each month at 09:00 UTC. Zillow published the September 2026
  vintage on the 16th, and Freddie Mac publishes PMMS weekly on Thursdays.
- **What a run does:**
  1. Storage-budget gate.
  2. `proptech ingest`: conditional GETs. Unchanged files are skipped and their sandbox
     expiry is refreshed.
  3. `dbt source freshness`.
  4. `dbt build`: models, data tests and unit tests.
  5. Record the storage dbt wrote in the ledger.
  6. Upload artifacts.
- **On failure** the workflow opens an issue titled `Pipeline failure: <env>` with the label
  `pipeline-failure`, or comments on the open one.
- **Run summary:** shown in the job summary, with one row per dataset (status, rows, bytes,
  warnings).
- **Inactive repos:** GitHub disables scheduled workflows after 60 days without repository
  activity in public repos. Re-enable it from the Actions tab, or push a commit.

## Triage by symptom

| Symptom | Likely cause | Action |
|---|---|---|
| Dataset `failed` with `missing required columns` | Zillow renamed or removed a metadata column | Check the file header. Update `REQUIRED_COLUMNS` and `META_COLUMNS` in `src/proptech/catalog.py` and `zillow.py`, add a test, re-run. The previous vintage stays loaded meanwhile. |
| `ignored unknown columns [...]` warning | Zillow added a column | Not urgent. Decide whether to map it in `zillow.META_COLUMNS`. |
| `only N regions, expected >= M (truncated download?)` | Truncated transfer or a real coverage change | Re-run. If it persists, compare against the published file and adjust `min_regions` in the catalog. |
| `values outside [lo, hi]` error | Many out-of-range values: unit change or bad file | Inspect the sample in the error. Fix the catalog range only if the new values are genuinely valid. |
| `quarantined N values` warning | A few outliers (e.g. Enid, OK affordability ratio ≈ 17 in 2016) | Review `ops.rejected_values`. No action is needed unless the count grows. |
| `month columns are not contiguous` | Zillow skipped or duplicated a month | Hold. Report it upstream. The previous vintage remains in place. |
| `dbt source freshness` warn or error | Missed run, or no successful load in 35 or 45 days | Check the last runs and re-run the workflow manually. |
| `share_at_most_mart_metro_momentum_rankings...` fails | More than 5% of peer metros have no CBSA mapping, often after a new ACS delineation | Query `int_metro_cbsa_crosswalk` for the metro, find its CBSA in `raw_reference.acs_median_household_income`, add a row to `dbt/seeds/metro_cbsa_overrides.csv`. |
| `storage budget exceeded` | Sandbox ledger reached `PROPTECH_STORAGE_BUDGET_GIB` | See [Sandbox storage quota](#sandbox-storage-quota). |
| `HTTP 403/404` for an ACS year | That ACS year isn't published yet | Expected. It is skipped with a warning. |
| Freddie Mac or Zillow `HTTP 403` | The host is blocking the client | Retry later. The User-Agent identifies the project. |

## Backfill and reprocessing

Zillow republishes the full history every month, so there is no per-month backfill. A run
replaces each dataset with the newest vintage.

```bash
# Re-run the whole profile even if nothing changed (e.g. after fixing a parser bug)
uv run proptech ingest --force
# Reload selected datasets only
uv run proptech ingest --force --only zhvi_metro_mid_all --only zori_metro_all
# Rebuild everything downstream
uv run dbt build --project-dir dbt --profiles-dir dbt
```

From GitHub, use **Actions → pipeline → Run workflow** with `force` and `only`.

**Restoring an older vintage:** every vintage is archived, gzip-compressed, under
`PROPTECH_RAW_ARCHIVE_URI/<dataset_key>/<last-modified>_<sha256[:16]>.csv.gz`. To reload one,
decompress it into a directory that mirrors the source path, point the loader at it, then run
`dbt build`:

```bash
PROPTECH_ZILLOW_BASE_URL=file:///path/to/mirror uv run proptech ingest --force --only <key>
```

**Moving to the extended profile** (city and ZIP, about 400 MB of CSV and about 0.5 GiB of
logical storage per load):

```bash
PROPTECH_PROFILE=extended uv run proptech ingest
PROPTECH_PROFILE=extended uv run dbt build --project-dir dbt --profiles-dir dbt
```

Check the storage budget first. On the sandbox each extended load uses about 5% of the
lifetime quota.

## Sandbox storage quota

The BigQuery sandbox grants a **lifetime** 10 GiB of storage, and "this quota is not
refunded upon data deletion" (Google's sandbox docs). The pipeline keeps its own estimate in
`ops.storage_ledger`:

```bash
uv run proptech budget                     # used / budget
uv run proptech budget --fail-above 0.95   # exit 1 when nearly exhausted (the workflow gate)
```

A core-profile load writes about 100 MiB when every file has changed. A month where nothing
changed writes about 0 MiB. Options when the ledger approaches the budget:

1. Stay on `core`. On the sandbox the time-series marts are views, which cost no storage.
2. Run less often. Each run only writes files that changed.
3. Enable billing and switch to `PROPTECH_ENV=prod` and `DBT_TARGET=prod`. This removes the
   lifetime quota and the 60-day expiry, and turns on month partitioning. Afterwards, update
   the default expiration of every dataset, as Google's upgrade notes say.

## Sandbox 60-day expiry

Every table, view and partition in the sandbox expires 60 days after creation.

- dbt re-creates its relations on every run.
- The loader re-stamps the expiry (`PROPTECH_SANDBOX_TABLE_TTL_DAYS`, 59) on every raw and
  ops table on each run, including unchanged ones.
- If the pipeline doesn't run for 60 days, tables disappear. The next run then sees no
  manifest and reloads everything. This is safe, but it costs about 100 MiB of quota.

## Credentials

- **CI:** prefer Workload Identity Federation (`vars.GCP_WORKLOAD_IDENTITY_PROVIDER` and
  `vars.GCP_SERVICE_ACCOUNT`). The fallback is `secrets.GCP_SA_KEY`. Set one, not both.
- **Rotating a key:** create a new key, update the secret, delete the old key in IAM.
- **Locally:** `export GOOGLE_APPLICATION_CREDENTIALS=/path/key.json`. Never commit a key.
  `.gitignore` and the pre-commit `detect-private-key` hook guard against it.

## Dashboard (Looker Studio)

1. In Looker Studio, choose **Create → Data source → BigQuery**, then your project →
   `marts` → `mart_metro_momentum_rankings`.
2. Repeat for `mart_metro_latest_snapshot`, `fct_affordability_monthly` (filter
   `geo_level = 'metro'`) and `dim_region`.
3. The exposure `housing_market_dashboard` in `dbt/models/marts/_exposures.yml` documents
   which models the report depends on. Add the report URL there once it exists.

## Data-quality checks at a glance

- **Load time** (`src/proptech/validation.py`): required columns, contiguous month-end
  columns, region-count floor, unique RegionIDs, expected RegionTypes, finite values, value
  ranges (with quarantine), staleness and interior-gap warnings.
- **dbt:** more than 90 data tests (uniqueness, relationships, accepted values and ranges,
  month-end dates, recency, drawdown ≤ 0, CBSA coverage SLO). Singular tests check that mart
  values exactly equal the source, the payment formula, and US affordability plausibility.
  There are 5 unit tests and contracts on 3 marts.
