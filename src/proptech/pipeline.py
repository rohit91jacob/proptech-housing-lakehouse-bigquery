"""Ingestion run: fetch -> validate -> archive -> load -> manifest, one dataset at a time.

A dataset that fails validation is recorded as ``failed`` and skipped; the rest of the run
continues, and the CLI exits non-zero so the orchestrator alerts. Raw tables are only ever
replaced by a single load job, so a failure leaves the previous vintage in place.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import requests

from proptech import budget, manifest, reference
from proptech.archive import archive_file, archive_name
from proptech.catalog import Dataset, Kind, load_catalog
from proptech.http import Download, DownloadError, build_session, fetch
from proptech.manifest import ManifestEntry, Status
from proptech.settings import Environment, Settings, Target
from proptech.validation import validate
from proptech.warehouse import (
    TableSpec,
    Warehouse,
    WriteMode,
    estimate_logical_bytes,
    open_warehouse,
)
from proptech.zillow import parse_file

log = logging.getLogger(__name__)

RAW_ZILLOW = "raw_zillow"
RAW_REFERENCE = "raw_reference"
# ACS years that aren't published yet answer 401/403/404 rather than a file.
UNPUBLISHED_STATUSES = {401, 403, 404}


@dataclass
class RunSummary:
    run_id: str
    started_at: datetime
    settings_target: str
    settings_env: str
    profile: str
    dry_run: bool
    entries: list[ManifestEntry] = field(default_factory=list)
    budget_status: budget.BudgetStatus | None = None
    finished_at: datetime | None = None

    @property
    def failed(self) -> list[ManifestEntry]:
        return [e for e in self.entries if e.status is Status.FAILED]

    @property
    def logical_bytes_written(self) -> int:
        return sum(e.logical_bytes_written or 0 for e in self.entries)


REJECTS = TableSpec(
    layer="ops",
    name="rejected_values",
    description="Source values quarantined by the load-time range checks (append-only).",
    clustering=("dataset_key",),
)


def _rejects_frame(run_id: str, dataset: Dataset, rejected: pl.DataFrame) -> pl.DataFrame:
    observed = "date" if "date" in rejected.columns else "horizon_date"
    return rejected.select(
        pl.lit(run_id).alias("run_id"),
        pl.lit(dataset.key).alias("dataset_key"),
        pl.col("region_id"),
        pl.col(observed).alias("observed_date"),
        pl.col("value"),
        pl.col("reason"),
    )


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


class Ingestor:
    def __init__(self, settings: Settings, warehouse: Warehouse, session: requests.Session, workdir: Path):
        self.settings = settings
        self.warehouse = warehouse
        self.session = session
        self.workdir = workdir
        self.raw_zillow = settings.dataset(RAW_ZILLOW)
        self.raw_reference = settings.dataset(RAW_REFERENCE)
        self.ops = settings.dataset("ops")
        self.ledger: list[tuple[str, str, int]] = []
        self.rejects: list[pl.DataFrame] = []
        self.enforce_budget = settings.target is Target.BIGQUERY and settings.env is Environment.SANDBOX
        self._budget: budget.BudgetStatus | None = None

    # ------------------------------------------------------------------ budget
    def budget_status(self) -> budget.BudgetStatus:
        if self._budget is None:
            self._budget = budget.status(self.warehouse, self.settings.storage_budget_gib, self.ops)
        return self._budget

    def _reserve(self, planned: int) -> str | None:
        """Return an error message if writing ``planned`` bytes would exceed the budget."""
        if not self.enforce_budget:
            return None
        current = self.budget_status()
        pending = sum(b for _, _, b in self.ledger)
        projected = budget.BudgetStatus(current.used_bytes + pending, current.budget_bytes)
        if not projected.allows(planned):
            needs = f"{planned / budget.GIB:.3f} GiB"
            return f"storage budget exceeded: {projected.describe()}, load needs {needs}"
        return None

    def _write(self, spec: TableSpec, frame: pl.DataFrame, mode: WriteMode) -> int:
        result = self.warehouse.write(spec, frame, mode)
        self.ledger.append(("load", result.table, result.logical_bytes))
        return result.logical_bytes

    # ------------------------------------------------------------------ zillow
    def ingest_dataset(
        self,
        dataset: Dataset,
        run_id: str,
        previous: dict[str, dict],
        *,
        force: bool,
        dry_run: bool,
    ) -> ManifestEntry:
        url = f"{self.settings.zillow_base_url.rstrip('/')}/{dataset.path}"
        entry = ManifestEntry(run_id=run_id, dataset_key=dataset.key, source_url=url, status=Status.FAILED)
        prior = previous.get(dataset.key)
        present = self.warehouse.table_exists(
            self.raw_zillow, dataset.raw_table
        ) and self.warehouse.table_exists(self.raw_zillow, dataset.regions_table)
        can_skip = bool(prior) and present and not force

        try:
            download = fetch(
                self.session,
                url,
                self.workdir / dataset.key,
                etag=prior.get("etag") if can_skip and prior else None,
                timeout=self.settings.http_timeout_seconds,
            )
        except DownloadError as exc:
            entry.error = str(exc)
            log.error("download failed", extra={"dataset": dataset.key, "error": str(exc)})
            return entry
        self._stamp(entry, download)

        if can_skip and prior and (download.not_modified or download.sha256 == prior.get("sha256")):
            entry.status = Status.UNCHANGED
            entry.sha256 = entry.sha256 or prior.get("sha256")
            if not dry_run:
                self.warehouse.touch(self.raw_zillow, dataset.raw_table)
                self.warehouse.touch(self.raw_zillow, dataset.regions_table)
            log.info("unchanged, skipping load", extra={"dataset": dataset.key})
            return entry
        if download.path is None:  # 304 without a usable prior state; refetch unconditionally
            download = fetch(
                self.session,
                url,
                self.workdir / dataset.key,
                timeout=self.settings.http_timeout_seconds,
            )
            self._stamp(entry, download)
        if download.path is None:
            entry.error = "server answered 304 to an unconditional request"
            return entry

        try:
            parsed = parse_file(dataset, download.path)
        except Exception as exc:  # malformed CSV, unexpected types, ...
            entry.error = f"parse failed: {exc}"
            log.exception("parse failed", extra={"dataset": dataset.key})
            return entry

        report = validate(
            parsed,
            relaxed=self.settings.relaxed_validation,
            max_reject_ratio=self.settings.max_reject_ratio,
        )
        entry.regions = report.stats.get("regions")
        entry.observations = report.stats.get("observations")
        entry.first_month = report.stats.get("first_month")
        entry.last_month = report.stats.get("last_month")
        entry.warnings = report.warnings + [f"ignored unknown columns {parsed.unknown_columns}"] * bool(
            parsed.unknown_columns
        )
        if not report.ok:
            entry.error = "; ".join(report.errors)
            log.error("validation failed", extra={"dataset": dataset.key, "errors": report.errors})
            return entry

        if dry_run:
            entry.status = Status.DRY_RUN
            entry.logical_bytes_written = estimate_logical_bytes(parsed.values) + estimate_logical_bytes(
                parsed.regions
            )
            return entry

        values = parsed.values
        if report.rejected is not None:
            low, high = dataset.value_range
            values = values.filter(pl.col("value").is_between(low, high))
            self.rejects.append(_rejects_frame(run_id, dataset, report.rejected))
        regions = parsed.regions.with_columns(pl.lit(dataset.key).alias("dataset_key"))
        planned = estimate_logical_bytes(values) + estimate_logical_bytes(regions)
        if dataset.kind is Kind.FORECAST:
            planned *= 2  # the vintage is also appended to the history table
        if (problem := self._reserve(planned)) is not None:
            entry.error = problem
            log.error("budget guard", extra={"dataset": dataset.key, "error": problem})
            return entry

        try:
            entry.logical_bytes_written = self._load(dataset, values, regions, entry)
            entry.rows_written = values.height + regions.height
        except Exception as exc:
            entry.error = f"load failed: {exc}"
            log.exception("load failed", extra={"dataset": dataset.key})
            return entry

        if download.sha256:
            try:
                archive_file(
                    self.settings.raw_archive_uri,
                    download.path,
                    archive_name(dataset.key, download.sha256, download.last_modified, ".csv"),
                )
            except Exception as exc:  # archive problems must not hide a successful load
                entry.warnings.append(f"archive failed: {exc}")
                log.warning("archive failed", extra={"dataset": dataset.key, "error": str(exc)})

        entry.status = Status.LOADED
        entry.loaded_at = datetime.now(UTC)
        log.info(
            "loaded",
            extra={
                "dataset": dataset.key,
                "rows": entry.rows_written,
                "bytes": entry.logical_bytes_written,
            },
        )
        return entry

    def _load(
        self, dataset: Dataset, values: pl.DataFrame, regions: pl.DataFrame, entry: ManifestEntry
    ) -> int:
        labels = {"dataset_key": dataset.key, "family": dataset.family}
        written = self._write(
            TableSpec(
                layer=self.raw_zillow,
                name=dataset.regions_table,
                description=f"Region metadata for {dataset.key}. Source: Zillow Research.",
                labels=labels,
            ),
            regions,
            WriteMode.REPLACE,
        )
        if dataset.kind is Kind.TIMESERIES:
            spec = TableSpec(
                layer=self.raw_zillow,
                name=dataset.raw_table,
                description=f"{dataset.description} Unit: {dataset.unit}. Source: Zillow Research.",
                clustering=("region_id",),
                partition_month_column="date",
                labels=labels,
            )
            return written + self._write(spec, values, WriteMode.REPLACE)

        # Forecasts: latest vintage replaces the current table and is appended to history once.
        current = TableSpec(
            layer=self.raw_zillow,
            name=dataset.raw_table,
            description=f"{dataset.description} Latest vintage. Source: Zillow Research.",
            clustering=("region_id",),
            labels=labels,
        )
        written += self._write(current, values, WriteMode.REPLACE)
        history = TableSpec(
            layer=self.raw_zillow,
            name=f"{dataset.raw_table}__history",
            description=f"Every vintage of {dataset.key} ever loaded (append-only).",
            clustering=("base_date", "region_id"),
            labels=labels,
        )
        new_bases = self._new_base_dates(history, values)
        if new_bases:
            vintage = values.filter(pl.col("base_date").is_in(new_bases)).with_columns(
                pl.lit(entry.sha256).alias("source_sha256"),
                pl.lit(datetime.now(UTC)).cast(pl.Datetime("us", "UTC")).alias("loaded_at"),
            )
            written += self._write(history, vintage, WriteMode.APPEND)
        else:
            self.warehouse.touch(history.layer, history.name)
        return written

    def _new_base_dates(self, history: TableSpec, values: pl.DataFrame) -> list:
        bases = values.get_column("base_date").unique().to_list()
        if not self.warehouse.table_exists(history.layer, history.name):
            return bases
        table = self.warehouse.qualified(history.layer, history.name)
        rows = self.warehouse.query(f"select distinct base_date from {table}")  # noqa: S608
        existing = {row["base_date"] for row in rows}
        return [b for b in bases if b not in existing]

    @staticmethod
    def _stamp(entry: ManifestEntry, download: Download) -> None:
        entry.sha256 = download.sha256
        entry.etag = download.etag
        entry.source_last_modified = download.last_modified
        entry.size_bytes = download.size_bytes
        entry.fetched_at = download.fetched_at

    # ------------------------------------------------------------------ reference
    def ingest_pmms(
        self, run_id: str, previous: dict[str, dict], *, force: bool, dry_run: bool
    ) -> ManifestEntry:
        key = "pmms_weekly"
        entry = ManifestEntry(
            run_id=run_id, dataset_key=key, source_url=self.settings.pmms_url, status=Status.FAILED
        )
        try:
            download = fetch(
                self.session,
                self.settings.pmms_url,
                self.workdir / key,
                timeout=self.settings.http_timeout_seconds,
            )
        except DownloadError as exc:
            entry.error = str(exc)
            return entry
        self._stamp(entry, download)
        prior = previous.get(key)
        if (
            not force
            and prior
            and prior.get("sha256") == download.sha256
            and self.warehouse.table_exists(self.raw_reference, key)
        ):
            entry.status = Status.UNCHANGED
            if not dry_run:
                self.warehouse.touch(self.raw_reference, key)
            return entry
        if download.path is None:
            entry.error = "no body returned"
            return entry
        try:
            frame = reference.parse_pmms(download.path)
        except Exception as exc:
            entry.error = f"parse failed: {exc}"
            return entry
        report = reference.validate_pmms(frame, relaxed=self.settings.relaxed_validation)
        entry.observations = frame.height
        entry.warnings = report.warnings
        if not report.ok:
            entry.error = "; ".join(report.errors)
            return entry
        entry.first_month = frame.get_column("week_date").min()
        entry.last_month = frame.get_column("week_date").max()
        if dry_run:
            entry.status = Status.DRY_RUN
            return entry
        if (problem := self._reserve(estimate_logical_bytes(frame))) is not None:
            entry.error = problem
            return entry
        spec = TableSpec(
            layer=self.raw_reference,
            name=key,
            description="Freddie Mac Primary Mortgage Market Survey (PMMS), weekly averages.",
            labels={"dataset_key": key, "family": "reference"},
        )
        entry.logical_bytes_written = self._write(spec, frame, WriteMode.REPLACE)
        entry.rows_written = frame.height
        entry.status = Status.LOADED
        entry.loaded_at = datetime.now(UTC)
        if download.sha256:
            archive_file(
                self.settings.raw_archive_uri,
                download.path,
                archive_name(key, download.sha256, download.last_modified, ".csv"),
            )
        return entry

    def ingest_acs(
        self, run_id: str, previous: dict[str, dict], *, force: bool, dry_run: bool
    ) -> ManifestEntry:
        key = "acs_median_household_income"
        entry = ManifestEntry(
            run_id=run_id,
            dataset_key=key,
            source_url=self.settings.acs_base_url,
            status=Status.FAILED,
        )
        frames: list[pl.DataFrame] = []
        digest = hashlib.sha256()
        downloads: list[tuple[str, Download]] = []
        for year in sorted(self.settings.acs_years):
            urls = reference.acs_urls(self.settings.acs_base_url, year)
            try:
                data = fetch(
                    self.session,
                    urls.data,
                    self.workdir / f"acs{year}",
                    timeout=self.settings.http_timeout_seconds,
                )
                geos = fetch(
                    self.session,
                    urls.geos,
                    self.workdir / f"acs{year}",
                    timeout=self.settings.http_timeout_seconds,
                )
            except DownloadError as exc:
                if exc.status_code in UNPUBLISHED_STATUSES:
                    entry.warnings.append(f"ACS {year} not available ({exc.status_code}); skipped")
                    continue
                entry.error = str(exc)
                return entry
            if data.path is None or geos.path is None:
                entry.error = f"ACS {year}: no body returned"
                return entry
            digest.update(f"{year}:{data.sha256}:{geos.sha256};".encode())
            downloads += [(f"acs{year}_b19013", data), (f"acs{year}_geos", geos)]
            try:
                frame = reference.parse_acs(data.path, geos.path, year)
            except Exception as exc:
                entry.error = f"ACS {year} parse failed: {exc}"
                return entry
            report = reference.validate_acs(frame, year, relaxed=self.settings.relaxed_validation)
            if not report.ok:
                entry.error = f"ACS {year}: " + "; ".join(report.errors)
                return entry
            frames.append(frame)
        if not frames:
            entry.error = "no ACS year could be loaded"
            return entry

        entry.sha256 = digest.hexdigest()
        entry.fetched_at = datetime.now(UTC)
        acs = pl.concat(frames)
        candidates = reference.cbsa_name_candidates(acs)
        entry.observations = acs.height
        prior = previous.get(key)
        if (
            not force
            and prior
            and prior.get("sha256") == entry.sha256
            and self.warehouse.table_exists(self.raw_reference, key)
            and self.warehouse.table_exists(self.raw_reference, "cbsa_name_candidates")
        ):
            entry.status = Status.UNCHANGED
            if not dry_run:
                self.warehouse.touch(self.raw_reference, key)
                self.warehouse.touch(self.raw_reference, "cbsa_name_candidates")
            return entry
        if dry_run:
            entry.status = Status.DRY_RUN
            return entry
        planned = estimate_logical_bytes(acs) + estimate_logical_bytes(candidates)
        if (problem := self._reserve(planned)) is not None:
            entry.error = problem
            return entry
        labels = {"dataset_key": key, "family": "reference"}
        written = self._write(
            TableSpec(
                layer=self.raw_reference,
                name=key,
                description="Census ACS 1-year B19013 median household income (country/state/county/CBSA).",
                clustering=("geo_level",),
                labels=labels,
            ),
            acs,
            WriteMode.REPLACE,
        )
        written += self._write(
            TableSpec(
                layer=self.raw_reference,
                name="cbsa_name_candidates",
                description="Candidate 'City, ST' names per CBSA used to match Zillow metros to CBSA codes.",
                labels=labels,
            ),
            candidates,
            WriteMode.REPLACE,
        )
        for name, download in downloads:
            if download.path is not None and download.sha256:
                archive_file(
                    self.settings.raw_archive_uri,
                    download.path,
                    archive_name(name, download.sha256, download.last_modified, download.path.suffix),
                )
        entry.logical_bytes_written = written
        entry.rows_written = acs.height + candidates.height
        entry.status = Status.LOADED
        entry.loaded_at = datetime.now(UTC)
        return entry


def run_ingest(
    settings: Settings,
    *,
    only: list[str] | None = None,
    force: bool = False,
    dry_run: bool = False,
    include_reference: bool = True,
) -> RunSummary:
    catalog = load_catalog(settings.catalog_path)
    datasets = catalog.for_profile(settings.profile, only)
    summary = RunSummary(
        run_id=new_run_id(),
        started_at=datetime.now(UTC),
        settings_target=str(settings.target),
        settings_env=str(settings.env),
        profile=settings.profile,
        dry_run=dry_run,
    )
    session = build_session(settings.user_agent, settings.http_retries)
    log.info(
        "ingest started",
        extra={
            "run_id": summary.run_id,
            "datasets": len(datasets),
            "target": str(settings.target),
            "dry_run": dry_run,
        },
    )
    with (
        open_warehouse(settings) as warehouse,
        tempfile.TemporaryDirectory(prefix="proptech-") as tmp,
    ):
        ingestor = Ingestor(settings, warehouse, session, Path(tmp))
        ops = settings.dataset("ops")
        previous = manifest.last_loaded(warehouse, ops)
        try:
            for dataset in datasets:
                summary.entries.append(
                    ingestor.ingest_dataset(dataset, summary.run_id, previous, force=force, dry_run=dry_run)
                )
            if include_reference:
                summary.entries.append(
                    ingestor.ingest_pmms(summary.run_id, previous, force=force, dry_run=dry_run)
                )
                summary.entries.append(
                    ingestor.ingest_acs(summary.run_id, previous, force=force, dry_run=dry_run)
                )
        finally:
            if not dry_run:
                if ingestor.rejects:
                    rejects = pl.concat(ingestor.rejects)
                    result = warehouse.write(replace(REJECTS, layer=ops), rejects, WriteMode.APPEND)
                    ingestor.ledger.append(("load", result.table, result.logical_bytes))
                manifest.write_manifest(warehouse, summary.entries, ops)
                budget.record(warehouse, summary.run_id, ingestor.ledger, ops)
            summary.budget_status = budget.status(warehouse, settings.storage_budget_gib, ops)
    summary.finished_at = datetime.now(UTC)
    log.info(
        "ingest finished",
        extra={
            "run_id": summary.run_id,
            "failed": [e.dataset_key for e in summary.failed],
            "logical_bytes_written": summary.logical_bytes_written,
        },
    )
    return summary
