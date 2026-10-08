# ADR 0005: Match Zillow metros to Census CBSAs by principal-city name, per ACS vintage

**Status:** accepted

## Context

Affordability needs ACS median household income per metro. Zillow metro files identify
metros by an internal `RegionID` and a short name ("New York, NY"). The ACS uses CBSA codes
and full titles ("New York-Newark-Jersey City, NY-NJ Metro Area"). No crosswalk file is
published alongside the CSVs. CBSA delineations also change between ACS vintages. In 2023,
for example, "The Villages, FL" became "Wildwood-The Villages, FL", and Madera, CA dropped
out of the 1-year tables.

## Decision

`proptech.reference.cbsa_name_candidates` turns each CBSA title into ranked `"City, ST"`
candidates:

- **Prefixes** of the principal-city list are ranked 1..n. This handles hyphenated city
  names such as Winston-Salem.
- **Later principal cities** on their own are ranked 101 and up. This handles renamed CBSAs.
- **`/` variants** are added, which handles "Louisville/Jefferson County".

`int_metro_cbsa_crosswalk` matches each Zillow metro **per ACS year**:

1. An override row in `seeds/metro_cbsa_overrides.csv` wins.
2. Otherwise the best-ranked candidate is used.
3. Two different CBSAs tied at the best rank are left unmapped rather than guessed.

Income is joined per ACS year, so a metro that exists only in older vintages still gets its
matching-year income.

## Consequences

- On the September 2026 data, all 394 metros within Zillow's top 400 size ranks are mapped,
  including all 100 momentum peers.
- 406 metros remain unmapped, all outside the 400 largest. These are mostly small areas the
  ACS 1-year estimates don't cover (they publish only for areas of 65,000+ people). Their
  income-based affordability metrics are NULL, and the Zillow-published affordability series
  are still available.
- The test `share_at_most(cbsa_code is null) <= 5%` on the peer group catches a future
  delineation change that breaks matching.
