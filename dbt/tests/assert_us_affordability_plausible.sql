-- Guards the payment formula and the rate/income joins: for the latest month, the US
-- principal-and-interest payment on the typical home must cost 10-60% of median income,
-- and must be below Zillow's total payment (which adds taxes and insurance).
with latest as (
    select *
    from {{ ref('fct_affordability_monthly') }}
    where geo_level = 'country'
    qualify row_number() over (order by month_end desc) = 1
)

select *
from latest
where payment_to_income_pct is null
   or payment_to_income_pct not between 10 and 60
   or (zillow_total_monthly_payment is not null and monthly_principal_interest >= zillow_total_monthly_payment)
