{#-
  Zillow metro -> Census CBSA, per ACS vintage (CBSA delineations change between vintages).
  1. A manual override (seed metro_cbsa_overrides) wins.
  2. Otherwise the best-ranked "City, ST" name candidate. Prefixes of the principal-city list
     rank 1..n and later principal cities 101.. (see proptech.reference.cbsa_name_candidates).
  3. Two different CBSAs tied on the best rank are ambiguous and left unmapped.
-#}
with metros as (
    select region_id, region_name
    from {{ ref('int_regions_conformed') }}
    where geo_level = 'metro'
),

candidates as (
    select * from {{ ref('stg_census__cbsa_name_candidates') }}
),

matches as (
    select
        metros.region_id,
        candidates.acs_year,
        candidates.cbsa_code,
        candidates.candidate_rank,
        min(candidates.candidate_rank) over (
            partition by metros.region_id, candidates.acs_year
        ) as best_rank
    from metros
    inner join candidates
        on candidates.candidate_name = metros.region_name
),

best as (
    select
        region_id,
        acs_year,
        min(cbsa_code) as cbsa_code,
        count(distinct cbsa_code) as tied_cbsas
    from matches
    where candidate_rank = best_rank
    group by region_id, acs_year
),

overrides as (
    select region_id, acs_year, cbsa_code
    from {{ ref('metro_cbsa_overrides') }}
),

combined as (
    select region_id, acs_year, cbsa_code, 'override' as match_method
    from overrides

    union all

    select best.region_id, best.acs_year, best.cbsa_code, 'name_match' as match_method
    from best
    left join overrides
        on overrides.region_id = best.region_id and overrides.acs_year = best.acs_year
    where best.tied_cbsas = 1
      and overrides.region_id is null
),

titles as (
    select distinct acs_year, cbsa_code, cbsa_title, metro_micro
    from candidates
)

select
    combined.region_id,
    combined.acs_year,
    combined.cbsa_code,
    titles.cbsa_title,
    titles.metro_micro,
    combined.match_method
from combined
left join titles
    on titles.acs_year = combined.acs_year and titles.cbsa_code = combined.cbsa_code
