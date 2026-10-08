"""Warehouse abstraction shared by the BigQuery (sandbox/prod) and DuckDB (local/CI) backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import polars as pl


class WriteMode(StrEnum):
    REPLACE = "replace"  # atomic full replacement (BigQuery WRITE_TRUNCATE load job)
    APPEND = "append"  # load-job append; never DML, so it works in the BigQuery sandbox


@dataclass(frozen=True)
class TableSpec:
    layer: str
    name: str
    description: str = ""
    clustering: tuple[str, ...] = ()
    # Month-partition column, honoured only where partitions don't expire (billing-enabled BigQuery).
    partition_month_column: str | None = None
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class WriteResult:
    table: str
    rows: int
    logical_bytes: int


# BigQuery logical-storage sizes per value (https://cloud.google.com/bigquery/pricing#data_type_sizes)
_FIXED_WIDTH = {
    pl.Int64: 8,
    pl.Int32: 8,
    pl.Float64: 8,
    pl.Float32: 8,
    pl.Date: 8,
    pl.Datetime: 8,
    pl.Boolean: 1,
}


def estimate_logical_bytes(frame: pl.DataFrame) -> int:
    """Approximate BigQuery logical bytes for ``frame`` (what the sandbox storage quota counts)."""
    total = 0
    for name, dtype in frame.schema.items():
        column = frame.get_column(name)
        if dtype == pl.Utf8:
            total += int(column.str.len_bytes().fill_null(0).sum()) + 2 * column.len()
        else:
            total += _FIXED_WIDTH.get(type(dtype), 8) * column.len()
    return total


class Warehouse(ABC):
    """Minimal surface the loader needs: write a frame, run a query, inspect tables."""

    @abstractmethod
    def ensure_layer(self, layer: str) -> None: ...

    @abstractmethod
    def write(self, spec: TableSpec, frame: pl.DataFrame, mode: WriteMode) -> WriteResult: ...

    @abstractmethod
    def query(self, sql: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def table_exists(self, layer: str, name: str) -> bool: ...

    @abstractmethod
    def qualified(self, layer: str, name: str) -> str:
        """SQL identifier for ``layer.name`` in this warehouse's dialect."""

    def touch(self, layer: str, name: str) -> None:  # noqa: B027 - optional hook
        """Refresh retention metadata on an unchanged table (sandbox expiry). Default: no-op."""

    def close(self) -> None:  # noqa: B027 - optional hook
        """Release connections."""

    def __enter__(self) -> Warehouse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
