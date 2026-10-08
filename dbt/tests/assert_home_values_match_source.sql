-- The mart must reproduce Zillow's published values exactly (no unit, rounding or type drift).
-- Returns every headline-ZHVI row whose value differs from the raw source table.
select
    raw.region_id,
    raw.date,
    raw.value as source_value,
    mart.home_value as mart_value
from {{ source('zillow', 'zhvi_metro_mid_all') }} as raw
left join {{ ref('fct_home_values_monthly') }} as mart
    on mart.dataset_key = 'zhvi_metro_mid_all'
    and mart.region_id = raw.region_id
    and mart.month_end = raw.date
where mart.home_value is null
   or mart.home_value != raw.value
