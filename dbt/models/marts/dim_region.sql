{{ config(materialized=proptech_materialization('snapshot'), cluster_by=proptech_cluster_by(['geo_level'])) }}

-- Conformed geography dimension: every Zillow region plus its latest CBSA mapping (metros).
with regions as (
    select * from {{ ref('int_regions_conformed') }}
),

latest_cbsa as (
    select
        region_id,
        acs_year,
        cbsa_code,
        cbsa_title,
        metro_micro,
        match_method
    from {{ ref('int_metro_cbsa_crosswalk') }}
    where true
    qualify row_number() over (partition by region_id order by acs_year desc) = 1
)

select
    regions.region_id,
    regions.geo_level,
    regions.region_name,
    regions.size_rank,
    regions.state_code,
    regions.state_fips,
    regions.county_geoid,
    regions.metro_title,
    regions.city,
    regions.county_name,
    latest_cbsa.cbsa_code,
    latest_cbsa.cbsa_title,
    latest_cbsa.metro_micro,
    latest_cbsa.acs_year as cbsa_acs_year,
    latest_cbsa.match_method as cbsa_match_method,
    regions.dataset_count
from regions
left join latest_cbsa
    on latest_cbsa.region_id = regions.region_id
