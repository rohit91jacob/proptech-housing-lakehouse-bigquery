{%- set datasets = [] -%}
{%- for d in zillow_catalog() if d.kind == 'forecast' and var('zillow_profile') in d.profiles -%}
    {%- do datasets.append(d) -%}
{%- endfor %}

{% for d in datasets %}
select
    '{{ d.key }}' as dataset_key,
    cast(region_id as {{ dbt.type_bigint() }}) as region_id,
    cast(base_date as date) as base_date,
    cast(horizon_date as date) as horizon_date,
    cast(horizon_months as {{ dbt.type_bigint() }}) as horizon_months,
    cast(value as {{ type_double() }}) as forecast_growth_pct
from {{ source('zillow', d.key) }}
{% if not loop.last %}union all{% endif %}
{% endfor %}
