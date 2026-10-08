{{
    config(
        materialized=proptech_materialization('timeseries'),
        partition_by=proptech_month_partition('month_end'),
        cluster_by=proptech_cluster_by(['region_id']),
    )
}}

-- For-sale market conditions per metro (and the US), one wide row per month.
-- Grain: region_id x month_end.
{%- set metrics = {
    'market_heat_index_metro': 'market_heat_index',
    'inventory_metro': 'for_sale_inventory',
    'new_listings_metro': 'new_listings',
    'new_pending_metro': 'newly_pending',
    'median_list_price_metro': 'median_list_price',
    'median_sale_price_metro': 'median_sale_price',
    'median_sale_to_list_metro': 'median_sale_to_list',
    'pct_sold_above_list_metro': 'share_sold_above_list',
    'pct_sold_below_list_metro': 'share_sold_below_list',
    'median_days_to_pending_metro': 'median_days_to_pending',
    'share_price_cut_metro': 'share_listings_price_cut',
    'sales_count_metro': 'sales_count',
    'new_construction_sales_metro': 'new_construction_sales',
    'new_construction_price_metro': 'new_construction_median_price',
} %}

with wide as (
    select
        region_id,
        geo_level,
        month_end,
        month_idx,
        {%- for key, column in metrics.items() %}
        max(case when dataset_key = '{{ key }}' then value end) as {{ column }}{{ "," if not loop.last }}
        {%- endfor %}
    from {{ ref('int_zillow_series') }}
    where dataset_key in ({% for key in metrics %}'{{ key }}'{{ ", " if not loop.last }}{% endfor %})
    group by region_id, geo_level, month_end, month_idx
),

windowed as (
    select
        *,
        {{ value_months_ago('for_sale_inventory', 12, 'region_id') }} as for_sale_inventory_12m_ago,
        {{ value_months_ago('median_list_price', 12, 'region_id') }} as median_list_price_12m_ago
    from wide
)

select
    region_id,
    geo_level,
    month_end,
    {%- for column in metrics.values() %}
    {{ column }},
    {%- endfor %}
    {{ pct_change('for_sale_inventory', 'for_sale_inventory_12m_ago') }} as for_sale_inventory_yoy_pct,
    {{ pct_change('median_list_price', 'median_list_price_12m_ago') }} as median_list_price_yoy_pct,
    -- months of supply: active inventory over monthly sales pace
    {{ safe_divide('for_sale_inventory', 'sales_count') }} as months_of_supply,
    {{ safe_divide('newly_pending', 'new_listings') }} as pending_to_new_listing_ratio,
    case
        when market_heat_index >= 70 then 'strong_sellers'
        when market_heat_index >= 55 then 'sellers'
        when market_heat_index > 44 then 'neutral'
        when market_heat_index > 28 then 'buyers'
        when market_heat_index is not null then 'strong_buyers'
    end as market_condition
from windowed
