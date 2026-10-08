"""Storage guardrail for the BigQuery sandbox's lifetime 10 GiB quota.

Google documents the quota as "not refunded upon data deletion", and its internal counter
isn't exposed. So the pipeline keeps its own append-only ledger of logical bytes written
(load jobs and dbt-built tables) and refuses to load once the configured budget is reached.
The ledger is a conservative, best-effort estimate, not Google's number.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

import polars as pl

from proptech.warehouse import TableSpec, Warehouse, WriteMode

GIB = 1024**3

LEDGER = TableSpec(
    layer="ops",
    name="storage_ledger",
    description="Append-only estimate of logical bytes written, per run and object.",
    clustering=("component",),
)

LEDGER_SCHEMA: dict[str, pl.DataType] = {
    "run_id": pl.Utf8(),
    "recorded_at": pl.Datetime("us", "UTC"),
    "component": pl.Utf8(),
    "object": pl.Utf8(),
    "logical_bytes": pl.Int64(),
}


@dataclass(frozen=True)
class BudgetStatus:
    used_bytes: int
    budget_bytes: int

    @property
    def remaining_bytes(self) -> int:
        return self.budget_bytes - self.used_bytes

    @property
    def used_fraction(self) -> float:
        return self.used_bytes / self.budget_bytes if self.budget_bytes else 1.0

    def allows(self, planned_bytes: int) -> bool:
        return self.used_bytes + planned_bytes <= self.budget_bytes

    def describe(self) -> str:
        return (
            f"{self.used_bytes / GIB:.3f} GiB of {self.budget_bytes / GIB:.2f} GiB budget used "
            f"({self.used_fraction:.1%})"
        )


def used_bytes(warehouse: Warehouse, layer: str = "ops") -> int:
    if not warehouse.table_exists(layer, LEDGER.name):
        return 0
    table = warehouse.qualified(layer, LEDGER.name)
    sql = f"select coalesce(sum(logical_bytes), 0) as used from {table}"  # noqa: S608 - internal identifier
    rows = warehouse.query(sql)
    return int(rows[0]["used"]) if rows else 0


def status(warehouse: Warehouse, budget_gib: float, layer: str = "ops") -> BudgetStatus:
    return BudgetStatus(used_bytes=used_bytes(warehouse, layer), budget_bytes=int(budget_gib * GIB))


def record(warehouse: Warehouse, run_id: str, items: list[tuple[str, str, int]], layer: str = "ops") -> None:
    """Append ``(component, object, logical_bytes)`` rows to the ledger."""
    if not items:
        return
    now = datetime.now(UTC)
    frame = pl.DataFrame(
        [
            {"run_id": run_id, "recorded_at": now, "component": c, "object": o, "logical_bytes": b}
            for c, o, b in items
        ],
        schema=LEDGER_SCHEMA,
    )
    warehouse.write(replace(LEDGER, layer=layer), frame, WriteMode.APPEND)
