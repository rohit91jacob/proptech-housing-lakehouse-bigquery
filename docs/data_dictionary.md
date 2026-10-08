# Data dictionary

Datasets (BigQuery) / schemas (DuckDB) are named by layer, with an optional
`PROPTECH_BQ_DATASET_PREFIX` (CI uses `ci_`). Column-level descriptions also live in the dbt
YAML and are published with `dbt docs`.

| Layer | Dataset | Written by | Materialisation |
|---|---|---|---|
| Raw | `raw_zillow` | `proptech ingest` (load jobs) | tables, replaced per vintage |
| Raw | `raw_reference` | `proptech ingest` | tables |
| Ops | `ops` | `proptech ingest` / `proptech budget` | append-only tables |
| Seeds | `seeds` | `dbt seed` | tables |
| Staging | `staging` | dbt | views |
| Intermediate | `intermediate` | dbt | views |
| Marts | `marts` | dbt | tables. Long time series are views on `sandbox`, and everything is a view on `ci` |

## Raw layer

### `raw_zillow.<dataset_key>`: one table per Zillow time-series file

The catalog in `config/datasets.yml` lists the `dataset_key` values: 36 in the `core` profile
and 41 in `extended`. The wide Zillow file (one column per month) is unpivoted, and
empty cells are dropped.

| Column | Type | Notes |
|---|---|---|
| `region_id` | INT64 | Zillow RegionID |
| `date` | DATE | month-end |
| `value` | FLOAT64 | unit given by the catalog (`usd`, `usd_per_month`, `count`, `ratio`, `percent`, `index`, `days`) |

Grain: `region_id × date`. Clustered by `region_id`. On `prod` it is also partitioned by
month on `date`. On the sandbox it isn't, because sandbox partitions expire 60 days after
their partition date.

### `raw_zillow.<dataset_key>__regions`: region metadata from the same file

`region_id, size_rank, region_name, region_type, state_name, state, city, metro, county_name,
state_fips, county_fips, dataset_key`. Which columns are populated depends on the
geography. County files carry FIPS codes. County, city and ZIP files carry the CBSA title
in `metro`.

### `raw_zillow.<forecast_key>` and `<forecast_key>__history`: Zillow Home Value Forecasts

| Column | Type | Notes |
|---|---|---|
| `region_id` | INT64 | |
| `base_date` | DATE | month the forecast was made from |
| `horizon_date` | DATE | month-end being forecast |
| `horizon_months` | INT64 | 1, 3 or 12 |
| `value` | FLOAT64 | forecast % change from `base_date` |
| `source_sha256`, `loaded_at` | STRING, TIMESTAMP | history table only |

The current table holds the latest vintage. A vintage is appended to `__history` the first
time it is seen, and each vintage is appended only once.

### `raw_reference`

| Table | Grain | Columns |
|---|---|---|
| `pmms_weekly` | week | `week_date, rate_30y, points_30y, rate_15y` (Freddie Mac PMMS, percent) |
| `acs_median_household_income` | ACS year × geography | `acs_year, geo_id, summary_level, geo_level (country/state/county/cbsa), geo_name, state_fips, county_fips, cbsa_code, median_household_income, margin_of_error` |
| `cbsa_name_candidates` | ACS year × CBSA × candidate | `acs_year, cbsa_code, cbsa_title, metro_micro, candidate_name ("City, ST"), candidate_rank` |

### `ops`

| Table | Grain | Purpose |
|---|---|---|
| `ingestion_manifest` | run × source object | status (`loaded`, `unchanged`, `failed` or `dry_run`), sha256, ETag, source Last-Modified, regions, observations, first and last month, bytes written, warnings, error, `loaded_at`. Drives incremental skips and dbt source freshness. |
| `storage_ledger` | run × object | Estimated logical bytes written by load jobs and dbt tables. This is the sandbox quota guardrail. |
| `rejected_values` | run × quarantined value | `dataset_key, region_id, observed_date, value, reason` |

## Staging (views)

| Model | Grain | Notes |
|---|---|---|
| `stg_zillow__observations` | dataset × region × month | Union of every loaded time series. Adds `month_idx = year*12 + month - 1`. |
| `stg_zillow__regions` | region | Metadata coalesced across files. `geo_level` is country, state, metro, county, city or zip. |
| `stg_zillow__forecasts` | dataset × region × base × horizon | Latest forecast vintage. |
| `stg_zillow__forecast_history` | dataset × region × base × horizon | Every vintage. A re-delivered vintage keeps its latest load. |
| `stg_freddie_mac__pmms_weekly` | week | Rates in percent. |
| `stg_census__acs_household_income` | ACS year × geo_id | Adds a 5-digit `county_geoid`. |
| `stg_census__cbsa_name_candidates` | ACS year × CBSA × candidate | |

## Intermediate (views)

| Model | Grain | Notes |
|---|---|---|
| `int_mortgage_rates_monthly` | month | Mean of the weekly PMMS rates whose survey week falls in the month. |
| `int_regions_conformed` | region | Conformed state code and FIPS, county GEOID. |
| `int_metro_cbsa_crosswalk` | metro × ACS year | Override seed first, then the best-ranked name candidate. Ties are unmapped. |
| `int_household_income_by_region` | region × ACS year | Country, state (FIPS), county (GEOID) and metro (crosswalk) incomes. |
| `int_zillow_series` | dataset × region × month | Observations plus dataset metadata and geography level. |

## Marts

| Model | Grain | Key metrics |
|---|---|---|
| `dim_region` (contract) | region | names, state, FIPS, latest CBSA code, title and match method |
| `dim_zillow_dataset` | dataset | family, metric, unit, tier, property type, bedrooms, smoothing and seasonal adjustment |
| `fct_home_values_monthly` | ZHVI dataset × region × month | `home_value, mom_pct, yoy_pct, cagr_5y_pct, cagr_10y_pct, running_peak_value, drawdown_from_peak_pct, is_all_time_high` |
| `fct_rents_monthly` | ZORI dataset × region × month | `monthly_rent, mom_pct, yoy_pct, cagr_5y_pct` |
| `fct_price_to_rent_monthly` | region × month | `price_to_rent_ratio = ZHVI / (12 × ZORI)`, `gross_rental_yield_pct` |
| `fct_affordability_monthly` (contract) | region × month | `monthly_principal_interest` (20% down, 30-year PMMS), `payment_to_income_pct`, `rent_to_income_pct`, Zillow's `zillow_total_monthly_payment`, `zillow_payment_to_income_pct`, `zillow_implied_household_income` |
| `fct_market_heat_monthly` | metro × month | heat index and `market_condition`, inventory and YoY, new listings, newly pending, list and sale prices, sale-to-list, share above or below list, days to pending, price-cut share, sales, new construction, `months_of_supply` |
| `mart_metro_latest_snapshot` | metro (+ US) | latest value of each headline metric, each with its own `*_as_of` month |
| `mart_metro_momentum_rankings` (contract) | peer metro | signed z-scores, `momentum_score`, `momentum_rank` (1 = hottest) |
| `fct_forecast_accuracy` | forecast vintage × region × horizon | `forecast_growth_pct`, `realised_growth_pct`, `error_pp`, `abs_error_pp`, `is_realized` |

### Metric definitions

- **YoY / MoM**: `100 × (value / value_n_months_ago − 1)`. The look-back uses a RANGE window on
  `month_idx`, so a missing month gives NULL instead of comparing the wrong two months.
- **CAGR**: `100 × ((value / value_N_years_ago)^(1/N) − 1)`.
- **Drawdown**: `100 × (value / running_peak − 1)`. It is ≤ 0, and a test enforces that.
- **Monthly P&I**: `L·r / (1 − (1+r)^−n)` with `L = (1 − 0.20) × ZHVI`, `r = rate/1200` and
  `n = 360`. The formula is checked against known amortisation values
  (`tests/assert_monthly_payment_formula.sql`).
- **Income vintage**: the latest ACS 1-year at or before the month's calendar year. When none
  exists, the earliest later year is used. See `income_acs_year`.
- **Momentum score**: the mean of the available peer z-scores of home-value YoY, rent YoY and
  heat index, plus the negated z-scores of days to pending, price-cut share and inventory YoY.
- **Market condition**: Zillow's heat-index bands. ≥70 strong sellers, 55–69 sellers, 45–54
  neutral, 29–44 buyers, ≤28 strong buyers.
