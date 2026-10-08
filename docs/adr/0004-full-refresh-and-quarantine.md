# ADR 0004: Full refresh per vintage, gate at load time, quarantine isolated outliers

**Status:** accepted

## Context

Zillow restates every series' history each month (smoothing and seasonal adjustment are
re-estimated). So an "append the new month" incremental design would silently mix vintages.
Upstream files occasionally contain isolated implausible values. For example, the
homeowner-affordability ratio for Enid, OK is about 17 in 2016, which would mean a payment of
17× income.

## Decision

- **Full refresh.** Each dataset is replaced by its latest vintage in one atomic load job. If
  the job fails, the previous vintage stays in place. dbt marts are rebuilt in full. Forecast
  vintages are the one exception: they are appended to a history table so forecast accuracy
  can be measured.
- **Fail closed per dataset.** Schema, month-column, region-count and range checks run
  before loading. A failing dataset is recorded as `failed` and skipped, the rest of the run
  continues, and the workflow exits non-zero to raise an alert.
- **Quarantine, don't drop silently.** Out-of-range values are excluded from the load and
  written to `ops.rejected_values`, but only when they make up at most
  `PROPTECH_MAX_REJECT_RATIO` (0.1%) of the file. Above that, the dataset fails, because a
  systematic violation means the unit or schema changed.

## Consequences

- Marts always reflect one consistent vintage per source.
- Every exclusion is auditable, with the run, value and reason recorded.
- Ranges in `config/datasets.yml` must be set from observed data. The Market Heat Index, for
  example, isn't bounded to 0–100: observed values run from about −120 to about 280.
