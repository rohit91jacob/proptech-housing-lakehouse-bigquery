{#- Helpers that keep models portable across BigQuery and DuckDB and sandbox-aware. -#}

{#-
  Long monthly time series become views on the sandbox/ci targets (views cost no storage
  against the sandbox's lifetime 10 GiB quota) and tables everywhere else.
  kind: 'timeseries' | 'snapshot'
-#}
{#- Deployment flavour: the target name, overridable with --vars "{deployment: ...}". -#}
{% macro proptech_deployment() -%}
    {{ return(var('deployment', target.name)) }}
{%- endmacro %}

{% macro proptech_materialization(kind='timeseries') -%}
    {%- set deployment = proptech_deployment() -%}
    {%- if deployment == 'ci' -%}
        {{ return('view') }}
    {%- elif deployment == 'sandbox' and kind == 'timeseries' -%}
        {{ return('view') }}
    {%- else -%}
        {{ return('table') }}
    {%- endif -%}
{%- endmacro %}

{#- Month partitioning only where partitions don't expire (billing-enabled BigQuery). -#}
{% macro proptech_month_partition(column) -%}
    {%- if target.type == 'bigquery' and proptech_deployment() == 'prod' -%}
        {{ return({'field': column, 'data_type': 'date', 'granularity': 'month'}) }}
    {%- else -%}
        {{ return(none) }}
    {%- endif -%}
{%- endmacro %}

{% macro proptech_cluster_by(columns) -%}
    {%- if target.type == 'bigquery' -%}
        {{ return(columns) }}
    {%- else -%}
        {{ return(none) }}
    {%- endif -%}
{%- endmacro %}

{#- Integer month index (year * 12 + month - 1): lets windows use RANGE frames in both engines. -#}
{% macro month_index(date_expr) -%}
    (extract(year from {{ date_expr }}) * 12 + extract(month from {{ date_expr }}) - 1)
{%- endmacro %}

{#- Month-end date for an integer month index produced by month_index(). -#}
{% macro month_end_from_index(index_expr) -%}
    {%- if target.type == 'bigquery' -%}
        last_day(date(div({{ index_expr }}, 12), mod({{ index_expr }}, 12) + 1, 1))
    {%- else -%}
        last_day(make_date(cast(({{ index_expr }}) // 12 as integer), cast(({{ index_expr }}) % 12 + 1 as integer), 1))
    {%- endif -%}
{%- endmacro %}

{% macro safe_divide(numerator, denominator) -%}
    {%- if target.type == 'bigquery' -%}
        safe_divide({{ numerator }}, {{ denominator }})
    {%- else -%}
        ({{ numerator }}) / nullif({{ denominator }}, 0)
    {%- endif -%}
{%- endmacro %}

{#- Percentage change, NULL when either side is missing or the base is zero. -#}
{% macro pct_change(current, previous) -%}
    (100 * ({{ safe_divide(current, previous) }} - 1))
{%- endmacro %}

{#- Fixed-rate amortising payment: P * r / (1 - (1 + r)^-n), r = annual % / 1200. -#}
{% macro monthly_payment(principal, annual_rate_pct, months) -%}
    case
        when {{ annual_rate_pct }} is null or {{ principal }} is null then null
        when {{ annual_rate_pct }} = 0 then {{ principal }} / {{ months }}
        else {{ principal }} * ({{ annual_rate_pct }} / 1200.0)
            / (1 - power(1 + {{ annual_rate_pct }} / 1200.0, -1 * {{ months }}))
    end
{%- endmacro %}

{#- Population z-score of an expression across the whole result set (or a partition). -#}
{% macro zscore(expr, partition_by=none) -%}
    {%- set window = '(partition by ' ~ partition_by ~ ')' if partition_by else '()' -%}
    {{ safe_divide(
        expr ~ ' - avg(' ~ expr ~ ') over ' ~ window,
        'stddev_pop(' ~ expr ~ ') over ' ~ window
    ) }}
{%- endmacro %}

{#- 8-byte float. dbt.type_float() is FLOAT64 on BigQuery but 4-byte FLOAT on DuckDB. -#}
{% macro type_double() -%}
    {%- if target.type == 'bigquery' -%} float64 {%- else -%} double {%- endif -%}
{%- endmacro %}
