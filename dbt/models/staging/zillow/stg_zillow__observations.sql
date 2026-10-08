{#-
  Every loaded Zillow time series in one long table. The list of source tables comes from
  the generated zillow_catalog() macro, filtered to the catalog profile that was ingested,
  so dbt never references a table the loader didn't create.
-#}
{%- set datasets = [] -%}
{%- for d in zillow_catalog() if d.kind == 'timeseries' and var('zillow_profile') in d.profiles -%}
    {%- do datasets.append(d) -%}
{%- endfor %}

{% for d in datasets %}
select
    '{{ d.key }}' as dataset_key,
    cast(region_id as {{ dbt.type_bigint() }}) as region_id,
    cast(date as date) as month_end,
    {{ month_index('date') }} as month_idx,
    cast(value as {{ type_double() }}) as value
from {{ source('zillow', d.key) }}
{% if not loop.last %}union all{% endif %}
{% endfor %}
