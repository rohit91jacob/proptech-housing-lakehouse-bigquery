{{
    config(
        materialized=proptech_materialization('timeseries'),
        partition_by=proptech_month_partition('month_end'),
        cluster_by=proptech_cluster_by(['dataset_key', 'region_id']),
    )
}}

-- Zillow Home Value Index (every tier / property type / bedroom count / geography) with growth
-- and drawdown metrics. Grain: dataset_key x region_id x month_end.
with series as (
    select dataset_key, tier, property_type, bedrooms, region_id, geo_level, month_end, month_idx, value
    from {{ ref('int_zillow_series') }}
    where family = 'zhvi'
),

windowed as (
    select
        *,
        {{ value_months_ago('value', 1, 'dataset_key, region_id') }} as value_1m_ago,
        {{ value_months_ago('value', 12, 'dataset_key, region_id') }} as value_12m_ago,
        {{ value_months_ago('value', 60, 'dataset_key, region_id') }} as value_60m_ago,
        {{ value_months_ago('value', 120, 'dataset_key, region_id') }} as value_120m_ago,
        {{ running_max('value', 'dataset_key, region_id') }} as running_peak_value
    from series
)

select
    dataset_key,
    region_id,
    geo_level,
    tier,
    property_type,
    bedrooms,
    month_end,
    value as home_value,
    {{ pct_change('value', 'value_1m_ago') }} as mom_pct,
    {{ pct_change('value', 'value_12m_ago') }} as yoy_pct,
    {{ cagr_pct('value', 'value_60m_ago', 5) }} as cagr_5y_pct,
    {{ cagr_pct('value', 'value_120m_ago', 10) }} as cagr_10y_pct,
    running_peak_value,
    {{ pct_change('value', 'running_peak_value') }} as drawdown_from_peak_pct,
    value >= running_peak_value as is_all_time_high
from windowed
