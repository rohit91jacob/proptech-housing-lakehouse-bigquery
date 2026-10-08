{#-
  Exact-offset look-backs over a monthly series. RANGE frames on the integer month_idx return
  the value exactly N months earlier (NULL when that month is missing), unlike LAG, which
  silently compares the wrong months when a series has gaps.
-#}
{% macro value_months_ago(value, months, partition_by, order_by='month_idx') -%}
    max({{ value }}) over (
        partition by {{ partition_by }}
        order by {{ order_by }}
        range between {{ months }} preceding and {{ months }} preceding
    )
{%- endmacro %}

{% macro running_max(value, partition_by, order_by='month_idx') -%}
    max({{ value }}) over (
        partition by {{ partition_by }}
        order by {{ order_by }}
        rows between unbounded preceding and current row
    )
{%- endmacro %}

{#- Compound annual growth rate in percent over `years`, NULL when the base is missing. -#}
{% macro cagr_pct(current, base, years) -%}
    (100 * (power({{ safe_divide(current, base) }}, 1.0 / {{ years }}) - 1))
{%- endmacro %}
