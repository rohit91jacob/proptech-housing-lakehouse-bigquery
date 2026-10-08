-- Known amortisation values: $300,000 at 6% over 30 years is $1,798.65/month,
-- $100,000 at 3.5% over 15 years is $714.88/month.
-- (BigQuery rejects WHERE without FROM, hence the derived table.)
with cases as (
    select 'p300k_6pct_30y' as case_name, {{ monthly_payment('300000.0', '6.0', 360) }} as payment, 1798.6515754582708 as expected

    union all

    select 'p100k_3_5pct_15y' as case_name, {{ monthly_payment('100000.0', '3.5', 180) }} as payment, 714.8825413431731 as expected
)

select *
from cases
where abs(payment - expected) > 0.000001
