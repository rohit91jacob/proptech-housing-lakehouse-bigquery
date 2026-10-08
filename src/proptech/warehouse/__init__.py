"""Warehouse backends. ``open_warehouse`` picks one from settings."""

from __future__ import annotations

from proptech.settings import Settings, Target
from proptech.warehouse.base import (
    TableSpec,
    Warehouse,
    WriteMode,
    WriteResult,
    estimate_logical_bytes,
)

__all__ = [
    "TableSpec",
    "Warehouse",
    "WriteMode",
    "WriteResult",
    "estimate_logical_bytes",
    "open_warehouse",
]


def open_warehouse(settings: Settings) -> Warehouse:
    if settings.target is Target.DUCKDB:
        from proptech.warehouse.duckdb_backend import DuckDBWarehouse

        return DuckDBWarehouse(settings.duckdb_path)

    if not settings.bq_project:
        raise ValueError("PROPTECH_BQ_PROJECT must be set when PROPTECH_TARGET=bigquery")
    from proptech.warehouse.bigquery_backend import BigQueryWarehouse

    return BigQueryWarehouse(
        settings.bq_project,
        settings.bq_location,
        settings.env,
        ttl_days=settings.sandbox_table_ttl_days,
        max_bytes_billed=settings.max_bytes_billed,
    )
