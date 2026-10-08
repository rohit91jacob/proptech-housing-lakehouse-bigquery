# ADR 0003: GitHub Actions as the orchestrator

**Status:** accepted

## Context

The workload is one monthly batch. Zillow publishes mid-month, and PMMS is weekly but only
needed as a monthly average. A run takes minutes. The sandbox has no billing account, so
Cloud Composer, Cloud Run jobs or Cloud Scheduler aren't available without enabling billing.

## Decision

`.github/workflows/pipeline.yml` is the orchestrator:

- `schedule` (18th of the month) plus `workflow_dispatch` inputs for deployment, profile,
  force and dataset selection.
- `concurrency: pipeline` with `cancel-in-progress: false`, so two loads never overlap and a
  load is never killed halfway.
- Keyless auth through Workload Identity Federation, with a service-account key fallback.
- Failure alerting: a GitHub issue with the label `pipeline-failure` is opened or commented on.
- Run artifacts: the ingest summary and dbt `run_results`, `sources` and `manifest`.
- A storage-budget gate before loading. Storage written by dbt is recorded afterwards.

## Alternatives considered

- **Airflow or Dagster.** Their scheduling, retries and lineage add little for a single
  monthly DAG of about five steps. They would need an always-on host, which has no free tier.
- **Cloud Scheduler with Cloud Run jobs.** These need billing. They're the natural move once
  the project leaves the sandbox. The CLI-based steps port directly.

## Consequences

- Free, auditable, and versioned with the code.
- GitHub disables schedules in public repos after 60 days without activity. The runbook
  explains how to re-enable them.
- There is no task-level retry or partial re-run. Instead, runs are idempotent and cheap to
  repeat, because unchanged sources are skipped.
