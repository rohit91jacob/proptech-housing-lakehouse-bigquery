"""Ingestion manifest: one row per dataset per run, appended to ``ops.ingestion_manifest``.

It drives incremental loads (skip a dataset whose checksum/ETag hasn't changed) and is the
``loaded_at`` column behind dbt source freshness.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Any

import polars as pl

from proptech.warehouse import TableSpec, Warehouse, WriteMode

log = logging.getLogger(__name__)

MANIFEST = TableSpec(
    layer="ops",
    name="ingestion_manifest",
    description="One row per source object per ingestion run (append-only).",
    clustering=("dataset_key",),
)


class Status(StrEnum):
    LOADED = "loaded"
    UNCHANGED = "unchanged"
    FAILED = "failed"
    DRY_RUN = "dry_run"


@dataclass
class ManifestEntry:
    run_id: str
    dataset_key: str
    source_url: str
    status: Status
    sha256: str | None = None
    etag: str | None = None
    source_last_modified: datetime | None = None
    size_bytes: int | None = None
    regions: int | None = None
    observations: int | None = None
    first_month: date | None = None
    last_month: date | None = None
    rows_written: int | None = None
    logical_bytes_written: int | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    fetched_at: datetime | None = None
    loaded_at: datetime | None = None

    def as_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["status"] = str(self.status)
        row["warnings"] = "; ".join(self.warnings) or None
        return row


MANIFEST_SCHEMA: dict[str, pl.DataType] = {
    "run_id": pl.Utf8(),
    "dataset_key": pl.Utf8(),
    "source_url": pl.Utf8(),
    "status": pl.Utf8(),
    "sha256": pl.Utf8(),
    "etag": pl.Utf8(),
    "source_last_modified": pl.Datetime("us", "UTC"),
    "size_bytes": pl.Int64(),
    "regions": pl.Int64(),
    "observations": pl.Int64(),
    "first_month": pl.Date(),
    "last_month": pl.Date(),
    "rows_written": pl.Int64(),
    "logical_bytes_written": pl.Int64(),
    "warnings": pl.Utf8(),
    "error": pl.Utf8(),
    "fetched_at": pl.Datetime("us", "UTC"),
    "loaded_at": pl.Datetime("us", "UTC"),
}


def write_manifest(warehouse: Warehouse, entries: list[ManifestEntry], layer: str = "ops") -> None:
    if not entries:
        return
    frame = pl.DataFrame([e.as_row() for e in entries], schema=MANIFEST_SCHEMA)
    warehouse.write(replace(MANIFEST, layer=layer), frame, WriteMode.APPEND)


def last_loaded(warehouse: Warehouse, layer: str = "ops") -> dict[str, dict[str, Any]]:
    """Latest successful load per dataset: ``{dataset_key: {sha256, etag, loaded_at}}``."""
    if not warehouse.table_exists(layer, MANIFEST.name):
        return {}
    table = warehouse.qualified(layer, MANIFEST.name)
    latest = "qualify row_number() over (partition by dataset_key order by loaded_at desc) = 1"
    sql = f"select dataset_key, sha256, etag, loaded_at from {table} where status = 'loaded' {latest}"  # noqa: S608 - internal identifier
    rows = warehouse.query(sql)
    return {row["dataset_key"]: row for row in rows}
