{#-
  One row per Zillow RegionID. The same region appears in many files; county/city/ZIP files
  carry richer metadata (FIPS codes, CBSA title), so values are coalesced across files.
-#}
{%- set datasets = [] -%}
{%- for d in zillow_catalog() if var('zillow_profile') in d.profiles -%}
    {%- do datasets.append(d) -%}
{%- endfor %}

with unioned as (
    {% for d in datasets %}
    select
        cast(region_id as {{ dbt.type_bigint() }}) as region_id,
        size_rank,
        region_name,
        region_type,
        -- metro/ZIP files put the state code in StateName; county files also carry State
        coalesce(state, state_name) as state_code,
        city,
        metro,
        county_name,
        state_fips,
        county_fips,
        '{{ d.key }}' as dataset_key
    from {{ source('zillow', d.key ~ '__regions') }}
    {% if not loop.last %}union all{% endif %}
    {% endfor %}
)

select
    region_id,
    case max(region_type) when 'msa' then 'metro' else max(region_type) end as geo_level,
    max(region_name) as region_name,
    min(size_rank) as size_rank,
    max(state_code) as state_code,
    max(city) as city,
    max(metro) as metro_title,
    max(county_name) as county_name,
    max(state_fips) as state_fips,
    max(county_fips) as county_fips,
    count(distinct dataset_key) as dataset_count
from unioned
group by region_id
