{#- Every forecast vintage ever loaded; a re-delivered vintage keeps its latest load. -#}
{%- set datasets = [] -%}
{%- for d in zillow_catalog() if d.kind == 'forecast' and var('zillow_profile') in d.profiles -%}
    {%- do datasets.append(d) -%}
{%- endfor %}

with unioned as (
    {% for d in datasets %}
    select
        '{{ d.key }}' as dataset_key,
        cast(region_id as {{ dbt.type_bigint() }}) as region_id,
        cast(base_date as date) as base_date,
        cast(horizon_date as date) as horizon_date,
        cast(horizon_months as {{ dbt.type_bigint() }}) as horizon_months,
        cast(value as {{ type_double() }}) as forecast_growth_pct,
        loaded_at
    from {{ source('zillow', d.key ~ '__history') }}
    {% if not loop.last %}union all{% endif %}
    {% endfor %}
)

select
    dataset_key,
    region_id,
    base_date,
    horizon_date,
    horizon_months,
    forecast_growth_pct,
    loaded_at
from unioned
where true  -- BigQuery only accepts QUALIFY alongside WHERE/GROUP BY/HAVING
qualify row_number() over (
    partition by dataset_key, region_id, base_date, horizon_months
    order by loaded_at desc
) = 1
