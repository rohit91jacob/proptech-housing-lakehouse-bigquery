-- Monthly average of the weekly PMMS survey, keyed by month-end to join Zillow series.
select
    last_day(week_date) as month_end,
    avg(rate_30y_pct) as rate_30y_pct,
    avg(rate_15y_pct) as rate_15y_pct,
    count(*) as survey_weeks
from {{ ref('stg_freddie_mac__pmms_weekly') }}
group by last_day(week_date)
