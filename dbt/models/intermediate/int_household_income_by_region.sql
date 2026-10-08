-- ACS median household income attached to Zillow regions, one row per region and ACS year.
with regions as (
    select * from {{ ref('int_regions_conformed') }}
),

acs as (
    select * from {{ ref('stg_census__acs_household_income') }}
),

crosswalk as (
    select * from {{ ref('int_metro_cbsa_crosswalk') }}
)

select regions.region_id, acs.acs_year, acs.geo_id, acs.geo_name, acs.median_household_income
from regions
inner join acs on regions.geo_level = 'country' and acs.geo_level = 'country'

union all

select regions.region_id, acs.acs_year, acs.geo_id, acs.geo_name, acs.median_household_income
from regions
inner join acs
    on regions.geo_level = 'state'
    and acs.geo_level = 'state'
    and acs.state_fips = regions.state_fips

union all

select regions.region_id, acs.acs_year, acs.geo_id, acs.geo_name, acs.median_household_income
from regions
inner join acs
    on regions.geo_level = 'county'
    and acs.geo_level = 'county'
    and acs.county_geoid = regions.county_geoid

union all

select crosswalk.region_id, acs.acs_year, acs.geo_id, acs.geo_name, acs.median_household_income
from crosswalk
inner join acs
    on acs.geo_level = 'cbsa'
    and acs.acs_year = crosswalk.acs_year
    and acs.cbsa_code = crosswalk.cbsa_code
