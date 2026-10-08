{#-
  Datasets are named by layer (staging / intermediate / marts / seeds), optionally prefixed
  by PROPTECH_BQ_DATASET_PREFIX (e.g. "ci_") so CI never touches production objects.
-#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set prefix = env_var('PROPTECH_BQ_DATASET_PREFIX', '') -%}
    {%- if custom_schema_name is none -%}
        {{ prefix }}{{ target.schema | trim }}
    {%- else -%}
        {{ prefix }}{{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
