{{
    config(
        materialized=proptech_materialization('timeseries'),
        partition_by=proptech_month_partition('month_end'),
        cluster_by=proptech_cluster_by(['dataset_key', 'region_id']),
    )
}}

-- Zillow Observed Rent Index with growth metrics. Grain: dataset_key x region_id x month_end.
with series as (
    select dataset_key, property_type, region_id, geo_level, month_end, month_idx, value
    from {{ ref('int_zillow_series') }}
    where family = 'zori'
),

windowed as (
    select
        *,
        {{ value_months_ago('value', 1, 'dataset_key, region_id') }} as value_1m_ago,
        {{ value_months_ago('value', 12, 'dataset_key, region_id') }} as value_12m_ago,
        {{ value_months_ago('value', 60, 'dataset_key, region_id') }} as value_60m_ago
    from series
)

select
    dataset_key,
    region_id,
    geo_level,
    property_type,
    month_end,
    value as monthly_rent,
    {{ pct_change('value', 'value_1m_ago') }} as mom_pct,
    {{ pct_change('value', 'value_12m_ago') }} as yoy_pct,
    {{ cagr_pct('value', 'value_60m_ago', 5) }} as cagr_5y_pct
from windowed
