"""``proptech`` command-line interface."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from proptech import budget, dbt_sources, fixtures, logs
from proptech.catalog import load_catalog
from proptech.manifest import Status
from proptech.pipeline import RunSummary, run_ingest
from proptech.settings import REPO_ROOT, Settings, Target

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)

DBT_DIR = REPO_ROOT / "dbt"


@app.callback()
def _main(
    log_level: Annotated[str, typer.Option(envvar="PROPTECH_LOG_LEVEL")] = "INFO",
    json_logs: Annotated[bool, typer.Option(envvar="PROPTECH_JSON_LOGS")] = True,
) -> None:
    logs.configure(log_level, json_logs)


def _render(summary: RunSummary) -> str:
    lines = [
        f"### Ingestion run `{summary.run_id}`",
        "",
        f"target **{summary.settings_target}** / env **{summary.settings_env}** / "
        f"profile **{summary.profile}**" + (" (dry run)" if summary.dry_run else ""),
        "",
        "| dataset | status | regions | observations | last month | MiB written | notes |",
        "|---|---|---:|---:|---|---:|---|",
    ]
    for e in summary.entries:
        mib = f"{(e.logical_bytes_written or 0) / 2**20:.2f}" if e.logical_bytes_written else ""
        note = e.error or "; ".join(e.warnings)
        lines.append(
            f"| `{e.dataset_key}` | {e.status} | {e.regions or ''} | {e.observations or ''} | "
            f"{e.last_month or ''} | {mib} | {note[:160]} |"
        )
    counts = {s: sum(e.status is s for e in summary.entries) for s in Status}
    lines += ["", ", ".join(f"{s}: {n}" for s, n in counts.items() if n)]
    if summary.budget_status:
        lines.append(f"Storage ledger: {summary.budget_status.describe()}")
    return "\n".join(lines)


@app.command()
def ingest(
    only: Annotated[list[str] | None, typer.Option("--only", help="Dataset key(s) to ingest.")] = None,
    force: Annotated[bool, typer.Option(help="Reload even if the source is unchanged.")] = False,
    dry_run: Annotated[bool, typer.Option(help="Fetch and validate, but write nothing.")] = False,
    reference: Annotated[bool, typer.Option(help="Also ingest PMMS rates and ACS income.")] = True,
    summary_json: Annotated[Path | None, typer.Option(help="Write a machine-readable summary.")] = None,
) -> None:
    """Fetch, validate, archive and load source data into the raw layer."""
    settings = Settings()
    summary = run_ingest(settings, only=only, force=force, dry_run=dry_run, include_reference=reference)
    report = _render(summary)
    typer.echo(report)
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(report + "\n")
    if summary_json:
        summary_json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": summary.run_id,
            "failed": [e.dataset_key for e in summary.failed],
            "entries": [e.as_row() for e in summary.entries],
            "logical_bytes_written": summary.logical_bytes_written,
        }
        summary_json.write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")
    if summary.failed:
        typer.echo(f"{len(summary.failed)} dataset(s) failed", err=True)
        raise typer.Exit(code=1)


@app.command("catalog")
def list_catalog(profile: Annotated[str | None, typer.Option()] = None) -> None:
    """List the datasets in a profile."""
    settings = Settings()
    catalog = load_catalog(settings.catalog_path)
    for d in catalog.for_profile(profile or settings.profile):
        typer.echo(f"{d.key:<34} {d.geography!s:<8} {d.unit:<14} {d.path}")


@app.command("dbt-sources")
def dbt_sources_cmd(
    check: Annotated[bool, typer.Option(help="Fail if generated files are stale; write nothing.")] = False,
) -> None:
    """Regenerate dbt sources, the catalog macro and the dataset seed from the catalog."""
    catalog = load_catalog(Settings().catalog_path)
    stale = dbt_sources.sync(catalog, DBT_DIR, check=check)
    for path in stale:
        typer.echo(f"{'stale' if check else 'wrote'}: {path.relative_to(REPO_ROOT)}")
    if check and stale:
        typer.echo("run `proptech dbt-sources` and commit the result", err=True)
        raise typer.Exit(code=1)
    if not stale:
        typer.echo("dbt artifacts are up to date")


@app.command("budget")
def budget_cmd(
    record_dbt_since: Annotated[
        str | None,
        typer.Option(help="ISO timestamp; record dbt tables created since then (BigQuery only)."),
    ] = None,
    run_id: Annotated[str, typer.Option()] = "",
    fail_above: Annotated[
        float | None, typer.Option(help="Exit 1 if the used fraction exceeds this.")
    ] = None,
) -> None:
    """Show (and optionally update) the storage ledger used to protect the sandbox quota."""
    from proptech.warehouse import open_warehouse

    settings = Settings()
    ops = settings.dataset("ops")
    with open_warehouse(settings) as warehouse:
        if record_dbt_since:
            if settings.target is not Target.BIGQUERY:
                typer.echo("--record-dbt-since only applies to BigQuery; skipping")
            else:
                since = datetime.fromisoformat(record_dbt_since).astimezone(UTC)
                items = []
                for layer in ("staging", "intermediate", "marts", "seeds"):
                    for table in warehouse.table_sizes(settings.dataset(layer)):  # type: ignore[attr-defined]
                        if table["type"] == "TABLE" and table["created"] and table["created"] >= since:
                            items.append(("dbt", table["table"], int(table["logical_bytes"])))
                budget.record(warehouse, run_id or f"dbt-{since:%Y%m%dT%H%M%SZ}", items, ops)
                typer.echo(f"recorded {len(items)} dbt table(s), {sum(i[2] for i in items) / 2**20:.2f} MiB")
        state = budget.status(warehouse, settings.storage_budget_gib, ops)
    typer.echo(state.describe())
    if fail_above is not None and state.used_fraction > fail_above:
        raise typer.Exit(code=1)


@app.command("fixtures")
def fixtures_cmd(
    out: Annotated[Path, typer.Option()] = REPO_ROOT / "tests" / "fixtures" / "sources",
) -> None:
    """Regenerate the synthetic source fixtures used by tests and CI."""
    catalog = load_catalog(Settings().catalog_path)
    paths = fixtures.generate(catalog, out)
    typer.echo(f"wrote {len(paths)} fixture files under {out}")


def main() -> None:  # pragma: no cover
    sys.exit(app())
