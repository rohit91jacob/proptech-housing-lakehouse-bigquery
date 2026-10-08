select
    cast(week_date as date) as week_date,
    cast(rate_30y as {{ type_double() }}) as rate_30y_pct,
    cast(points_30y as {{ type_double() }}) as points_30y,
    cast(rate_15y as {{ type_double() }}) as rate_15y_pct
from {{ source('reference', 'pmms_weekly') }}
