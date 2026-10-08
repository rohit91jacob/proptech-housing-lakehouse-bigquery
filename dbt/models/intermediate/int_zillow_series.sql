-- Observations decorated with dataset metadata and the region's geography level.
select
    observations.dataset_key,
    datasets.family,
    datasets.metric,
    datasets.tier,
    datasets.property_type,
    datasets.bedrooms,
    observations.region_id,
    regions.geo_level,
    observations.month_end,
    observations.month_idx,
    observations.value
from {{ ref('stg_zillow__observations') }} as observations
inner join {{ ref('zillow_datasets') }} as datasets
    on datasets.dataset_key = observations.dataset_key
inner join {{ ref('stg_zillow__regions') }} as regions
    on regions.region_id = observations.region_id
