{{ config(materialized=proptech_materialization('snapshot')) }}

{#-
  Ranks the N largest metros (var momentum_peer_metros, by Zillow SizeRank) by market momentum.
  Each signal is z-scored across the peer group and signed so that higher = hotter:
    + home value YoY, + rent YoY, + market heat index,
    - days to pending, - share of listings with a price cut, - inventory YoY.
  momentum_score is the mean of the available z-scores; rank 1 is the hottest market.
  Grain: region_id (one row per peer metro).
-#}
with peers as (
    select *
    from {{ ref('mart_metro_latest_snapshot') }}
    where geo_level = 'metro'
    qualify row_number() over (order by size_rank, region_id) <= {{ var('momentum_peer_metros') }}
),

scored as (
    select
        *,
        {{ zscore('home_value_yoy_pct') }} as z_home_value_yoy,
        {{ zscore('rent_yoy_pct') }} as z_rent_yoy,
        {{ zscore('market_heat_index') }} as z_market_heat,
        -1 * {{ zscore('median_days_to_pending') }} as z_days_to_pending,
        -1 * {{ zscore('share_listings_price_cut') }} as z_price_cuts,
        -1 * {{ zscore('for_sale_inventory_yoy_pct') }} as z_inventory_yoy
    from peers
),

combined as (
    select
        *,
        (
            coalesce(z_home_value_yoy, 0) + coalesce(z_rent_yoy, 0) + coalesce(z_market_heat, 0)
            + coalesce(z_days_to_pending, 0) + coalesce(z_price_cuts, 0) + coalesce(z_inventory_yoy, 0)
        ) / nullif(
            case when z_home_value_yoy is null then 0 else 1 end
            + case when z_rent_yoy is null then 0 else 1 end
            + case when z_market_heat is null then 0 else 1 end
            + case when z_days_to_pending is null then 0 else 1 end
            + case when z_price_cuts is null then 0 else 1 end
            + case when z_inventory_yoy is null then 0 else 1 end,
            0
        ) as momentum_score
    from scored
)

select
    region_id,
    region_name,
    size_rank,
    state_code,
    cbsa_code,
    home_value_as_of,
    home_value,
    home_value_yoy_pct,
    rent_yoy_pct,
    market_heat_index,
    market_condition,
    median_days_to_pending,
    share_listings_price_cut,
    for_sale_inventory_yoy_pct,
    payment_to_income_pct,
    z_home_value_yoy,
    z_rent_yoy,
    z_market_heat,
    z_days_to_pending,
    z_price_cuts,
    z_inventory_yoy,
    momentum_score,
    row_number() over (order by momentum_score desc, size_rank) as momentum_rank
from combined
