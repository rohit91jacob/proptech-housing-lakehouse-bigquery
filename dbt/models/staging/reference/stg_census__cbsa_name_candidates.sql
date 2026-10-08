select
    cast(acs_year as {{ dbt.type_bigint() }}) as acs_year,
    cbsa_code,
    cbsa_title,
    metro_micro,
    candidate_name,
    cast(candidate_rank as {{ dbt.type_bigint() }}) as candidate_rank
from {{ source('reference', 'cbsa_name_candidates') }}
