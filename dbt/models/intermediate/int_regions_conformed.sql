-- Zillow regions with a consistent state code / FIPS and a five-digit county GEOID.
with regions as (
    select * from {{ ref('stg_zillow__regions') }}
),

states as (
    select * from {{ ref('state_codes') }}
)

select
    regions.region_id,
    regions.geo_level,
    regions.region_name,
    regions.size_rank,
    case
        when regions.geo_level = 'state' then by_name.state_code
        else regions.state_code
    end as state_code,
    coalesce(
        case when regions.geo_level = 'state' then by_name.state_fips end,
        regions.state_fips,
        by_code.state_fips
    ) as state_fips,
    case
        when regions.geo_level = 'county' then regions.state_fips || regions.county_fips
    end as county_geoid,
    regions.metro_title,
    regions.city,
    regions.county_name,
    regions.dataset_count
from regions
left join states as by_name
    on regions.geo_level = 'state' and by_name.state_name = regions.region_name
left join states as by_code
    on regions.geo_level != 'state' and by_code.state_code = regions.state_code
