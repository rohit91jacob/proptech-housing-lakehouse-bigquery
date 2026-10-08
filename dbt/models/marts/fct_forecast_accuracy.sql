{{ config(materialized=proptech_materialization('snapshot'), cluster_by=proptech_cluster_by(['dataset_key', 'region_id'])) }}

{#-
  Scores every archived Zillow Home Value Forecast vintage against the realised ZHVI change.
  Realised values come from the CURRENT ZHVI vintage (Zillow restates history monthly), so
  this measures the forecast against today's best estimate of what happened.
  Rows whose horizon month isn't published yet have is_realized = false.
  Grain: dataset_key x region_id x base_date x horizon_months.
-#}
with forecasts as (
    select
        dataset_key,
        case dataset_key
            when 'zhvf_metro_mid_all' then 'zhvi_metro_mid_all'
            when 'zhvf_zip_mid_all' then 'zhvi_zip_mid_all'
        end as actual_dataset_key,
        region_id,
        base_date,
        horizon_date,
        horizon_months,
        forecast_growth_pct
    from {{ ref('stg_zillow__forecast_history') }}
),

actuals as (
    select dataset_key, region_id, month_end, value
    from {{ ref('stg_zillow__observations') }}
    where dataset_key in ('zhvi_metro_mid_all', 'zhvi_zip_mid_all')
),

scored as (
    select
        forecasts.*,
        base.value as base_home_value,
        realised.value as realised_home_value,
        {{ pct_change('realised.value', 'base.value') }} as realised_growth_pct
    from forecasts
    left join actuals as base
        on base.dataset_key = forecasts.actual_dataset_key
        and base.region_id = forecasts.region_id
        and base.month_end = forecasts.base_date
    left join actuals as realised
        on realised.dataset_key = forecasts.actual_dataset_key
        and realised.region_id = forecasts.region_id
        and realised.month_end = forecasts.horizon_date
)

select
    dataset_key,
    region_id,
    base_date,
    horizon_months,
    horizon_date,
    forecast_growth_pct,
    base_home_value,
    realised_home_value,
    realised_growth_pct,
    forecast_growth_pct - realised_growth_pct as error_pp,
    abs(forecast_growth_pct - realised_growth_pct) as abs_error_pp,
    realised_growth_pct is not null as is_realized
from scored
