{{ config(materialized=proptech_materialization('snapshot')) }}

{#-
  Latest reading of each headline metric for every metro and the US. Series end in different
  months (sale-price series lag listing series by a month), so each metric carries its own
  as-of month.
  Grain: region_id.
-#}
with home_values as (
    select region_id, month_end, home_value, yoy_pct, cagr_5y_pct, drawdown_from_peak_pct
    from {{ ref('fct_home_values_monthly') }}
    where dataset_key = 'zhvi_metro_mid_all'
    qualify row_number() over (partition by region_id order by month_end desc) = 1
),

rents as (
    select region_id, month_end, monthly_rent, yoy_pct
    from {{ ref('fct_rents_monthly') }}
    where dataset_key = 'zori_metro_all'
    qualify row_number() over (partition by region_id order by month_end desc) = 1
),

market as (
    select
        region_id,
        month_end,
        market_heat_index,
        market_condition,
        for_sale_inventory,
        for_sale_inventory_yoy_pct,
        median_days_to_pending,
        share_listings_price_cut
    from {{ ref('fct_market_heat_monthly') }}
    where market_heat_index is not null
    qualify row_number() over (partition by region_id order by month_end desc) = 1
),

sales as (
    select region_id, month_end, median_sale_price, median_sale_to_list, share_sold_above_list
    from {{ ref('fct_market_heat_monthly') }}
    where median_sale_price is not null
    qualify row_number() over (partition by region_id order by month_end desc) = 1
),

affordability as (
    select region_id, month_end, payment_to_income_pct, rent_to_income_pct, rate_30y_pct
    from {{ ref('fct_affordability_monthly') }}
    where geo_level in ('metro', 'country') and payment_to_income_pct is not null
    qualify row_number() over (partition by region_id order by month_end desc) = 1
),

regions as (
    select * from {{ ref('dim_region') }}
    where geo_level in ('metro', 'country')
)

select
    regions.region_id,
    regions.region_name,
    regions.geo_level,
    regions.size_rank,
    regions.state_code,
    regions.cbsa_code,
    home_values.month_end as home_value_as_of,
    home_values.home_value,
    home_values.yoy_pct as home_value_yoy_pct,
    home_values.cagr_5y_pct as home_value_cagr_5y_pct,
    home_values.drawdown_from_peak_pct,
    rents.month_end as rent_as_of,
    rents.monthly_rent,
    rents.yoy_pct as rent_yoy_pct,
    {{ safe_divide('home_values.home_value', 'rents.monthly_rent * 12') }} as price_to_rent_ratio,
    market.month_end as market_as_of,
    market.market_heat_index,
    market.market_condition,
    market.for_sale_inventory,
    market.for_sale_inventory_yoy_pct,
    market.median_days_to_pending,
    market.share_listings_price_cut,
    sales.month_end as sales_as_of,
    sales.median_sale_price,
    sales.median_sale_to_list,
    sales.share_sold_above_list,
    affordability.month_end as affordability_as_of,
    affordability.rate_30y_pct,
    affordability.payment_to_income_pct,
    affordability.rent_to_income_pct
from regions
inner join home_values
    on home_values.region_id = regions.region_id
left join rents
    on rents.region_id = regions.region_id
left join market
    on market.region_id = regions.region_id
left join sales
    on sales.region_id = regions.region_id
left join affordability
    on affordability.region_id = regions.region_id
