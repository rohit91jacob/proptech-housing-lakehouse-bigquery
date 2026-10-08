{{
    config(
        materialized=proptech_materialization('timeseries'),
        partition_by=proptech_month_partition('month_end'),
        cluster_by=proptech_cluster_by(['region_id']),
    )
}}

{#-
  Ownership and rental affordability for the typical home.
  monthly_principal_interest: fixed-rate payment on (1 - down_payment_share) x ZHVI at the
  month's average 30-year PMMS rate, over mortgage_term_months.
  Income: ACS median household income for the region, from the latest ACS year at or before
  the month's calendar year (else the earliest available; see income_acs_year).
  Zillow's own total payment (incl. taxes and insurance) and affordability ratio are carried
  alongside, where published, as a cross-check.
  Grain: region_id x month_end.
-#}
{%- set down = var('down_payment_share') -%}
{%- set term = var('mortgage_term_months') -%}

with home_values as (
    select region_id, geo_level, month_end, value as home_value
    from {{ ref('int_zillow_series') }}
    where dataset_key in ('zhvi_metro_mid_all', 'zhvi_state_mid_all', 'zhvi_county_mid_all')
      and month_end >= cast('{{ var("affordability_start_month") }}' as date)
),

rents as (
    select region_id, month_end, value as monthly_rent
    from {{ ref('int_zillow_series') }}
    where dataset_key in ('zori_metro_all', 'zori_county_all')
),

zillow_reference as (
    select
        region_id,
        month_end,
        max(case when dataset_key = 'total_monthly_payment_metro' then value end) as zillow_total_monthly_payment,
        max(case when dataset_key = 'homeowner_affordability_metro' then value end) as zillow_affordability_ratio
    from {{ ref('int_zillow_series') }}
    where dataset_key in ('total_monthly_payment_metro', 'homeowner_affordability_metro')
    group by region_id, month_end
),

calendar_years as (
    select distinct region_id, extract(year from month_end) as calendar_year
    from home_values
),

income_by_year as (
    select
        calendar_years.region_id,
        calendar_years.calendar_year,
        income.acs_year,
        income.median_household_income,
        row_number() over (
            partition by calendar_years.region_id, calendar_years.calendar_year
            order by
                case when income.acs_year <= calendar_years.calendar_year then 0 else 1 end,
                case
                    when income.acs_year <= calendar_years.calendar_year then -1 * income.acs_year
                    else income.acs_year
                end
        ) as preference
    from calendar_years
    inner join {{ ref('int_household_income_by_region') }} as income
        on income.region_id = calendar_years.region_id
),

priced as (
    select
        home_values.region_id,
        home_values.geo_level,
        home_values.month_end,
        home_values.home_value,
        rates.rate_30y_pct,
        home_values.home_value * (1 - {{ down }}) as loan_amount,
        {{ monthly_payment('home_values.home_value * (1 - ' ~ down ~ ')', 'rates.rate_30y_pct', term) }}
            as monthly_principal_interest
    from home_values
    left join {{ ref('int_mortgage_rates_monthly') }} as rates
        on rates.month_end = home_values.month_end
)

select
    priced.region_id,
    priced.geo_level,
    priced.month_end,
    priced.home_value,
    priced.rate_30y_pct,
    cast({{ down }} as {{ type_double() }}) as down_payment_share,
    priced.loan_amount,
    priced.monthly_principal_interest,
    income_by_year.acs_year as income_acs_year,
    income_by_year.median_household_income,
    100 * {{ safe_divide('priced.monthly_principal_interest * 12', 'income_by_year.median_household_income') }}
        as payment_to_income_pct,
    rents.monthly_rent,
    100 * {{ safe_divide('rents.monthly_rent * 12', 'income_by_year.median_household_income') }}
        as rent_to_income_pct,
    zillow_reference.zillow_total_monthly_payment,
    100 * zillow_reference.zillow_affordability_ratio as zillow_payment_to_income_pct,
    {{ safe_divide('zillow_reference.zillow_total_monthly_payment * 12', 'zillow_reference.zillow_affordability_ratio') }}
        as zillow_implied_household_income
from priced
left join income_by_year
    on income_by_year.region_id = priced.region_id
    and income_by_year.calendar_year = extract(year from priced.month_end)
    and income_by_year.preference = 1
left join rents
    on rents.region_id = priced.region_id
    and rents.month_end = priced.month_end
left join zillow_reference
    on zillow_reference.region_id = priced.region_id
    and zillow_reference.month_end = priced.month_end
