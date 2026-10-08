"""DuckDB backend: the same raw layout as BigQuery, used for local development and CI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from proptech.warehouse.base import (
    TableSpec,
    Warehouse,
    WriteMode,
    WriteResult,
    estimate_logical_bytes,
)


def _ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


class DuckDBWarehouse(Warehouse):
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.con = duckdb.connect(str(path))

    def ensure_layer(self, layer: str) -> None:
        self.con.execute(f"CREATE SCHEMA IF NOT EXISTS {_ident(layer)}")

    def qualified(self, layer: str, name: str) -> str:
        return f"{_ident(layer)}.{_ident(name)}"

    def table_exists(self, layer: str, name: str) -> bool:
        row = self.con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
            [layer, name],
        ).fetchone()
        return bool(row and row[0])

    def write(self, spec: TableSpec, frame: pl.DataFrame, mode: WriteMode) -> WriteResult:
        self.ensure_layer(spec.layer)
        target = self.qualified(spec.layer, spec.name)
        arrow = frame.to_arrow()  # noqa: F841 - DuckDB resolves `arrow` by name below
        self.con.execute("BEGIN TRANSACTION")
        try:
            if mode is WriteMode.REPLACE or not self.table_exists(spec.layer, spec.name):
                self.con.execute(f"CREATE OR REPLACE TABLE {target} AS SELECT * FROM arrow")  # noqa: S608
            else:
                self.con.execute(f"INSERT INTO {target} BY NAME SELECT * FROM arrow")  # noqa: S608
            if spec.description:
                comment = spec.description.replace("'", "''")
                self.con.execute(f"COMMENT ON TABLE {target} IS '{comment}'")
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            raise
        return WriteResult(target, frame.height, estimate_logical_bytes(frame))

    def query(self, sql: str) -> list[dict[str, Any]]:
        cursor = self.con.execute(sql)
        columns = [d[0] for d in cursor.description or []]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def close(self) -> None:
        self.con.close()
