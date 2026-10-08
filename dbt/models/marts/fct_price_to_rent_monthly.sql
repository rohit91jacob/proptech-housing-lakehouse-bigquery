{{
    config(
        materialized=proptech_materialization('timeseries'),
        partition_by=proptech_month_partition('month_end'),
        cluster_by=proptech_cluster_by(['region_id']),
    )
}}

-- Price-to-rent ratio and gross rental yield where Zillow publishes both a typical home value
-- and an observed rent for the same region (country, metros, counties).
-- Grain: region_id x month_end.
with home_values as (
    select region_id, geo_level, month_end, value as home_value
    from {{ ref('int_zillow_series') }}
    where dataset_key in ('zhvi_metro_mid_all', 'zhvi_county_mid_all')
),

rents as (
    select region_id, month_end, value as monthly_rent
    from {{ ref('int_zillow_series') }}
    where dataset_key in ('zori_metro_all', 'zori_county_all')
)

select
    home_values.region_id,
    home_values.geo_level,
    home_values.month_end,
    home_values.home_value,
    rents.monthly_rent,
    {{ safe_divide('home_values.home_value', 'rents.monthly_rent * 12') }} as price_to_rent_ratio,
    100 * {{ safe_divide('rents.monthly_rent * 12', 'home_values.home_value') }} as gross_rental_yield_pct
from home_values
inner join rents
    on rents.region_id = home_values.region_id
    and rents.month_end = home_values.month_end
