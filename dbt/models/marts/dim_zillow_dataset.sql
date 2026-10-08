{{ config(materialized=proptech_materialization('snapshot')) }}

-- One row per Zillow source file in the catalog (config/datasets.yml).
select
    dataset_key,
    family,
    metric,
    kind,
    geography,
    unit,
    tier,
    property_type,
    bedrooms,
    is_smoothed,
    is_seasonally_adjusted,
    profiles,
    source_path,
    description
from {{ ref('zillow_datasets') }}
