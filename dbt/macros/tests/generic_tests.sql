{#- Fails for every row whose date is not the last day of its month. -#}
{% test is_month_end(model, column_name) %}
    select {{ column_name }}
    from {{ model }}
    where {{ column_name }} is not null
      and {{ column_name }} != last_day({{ column_name }})
{% endtest %}

{#-
  Fails when the share of rows matching `condition` is above `max_share`.
  Used for coverage SLOs (e.g. at least 95% of peer metros mapped to a CBSA).
-#}
{% test share_at_most(model, condition, max_share) %}
    with counts as (
        select
            count(*) as total_rows,
            sum(case when {{ condition }} then 1 else 0 end) as matching_rows
        from {{ model }}
    )
    select *
    from counts
    where total_rows > 0
      and matching_rows * 1.0 / total_rows > {{ max_share }}
{% endtest %}
