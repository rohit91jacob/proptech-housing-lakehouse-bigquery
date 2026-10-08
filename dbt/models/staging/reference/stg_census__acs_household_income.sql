select
    cast(acs_year as {{ dbt.type_bigint() }}) as acs_year,
    geo_id,
    geo_level,
    geo_name,
    state_fips,
    case when geo_level = 'county' then state_fips || county_fips end as county_geoid,
    cbsa_code,
    cast(median_household_income as {{ type_double() }}) as median_household_income,
    cast(margin_of_error as {{ type_double() }}) as margin_of_error
from {{ source('reference', 'acs_median_household_income') }}
where median_household_income is not null
