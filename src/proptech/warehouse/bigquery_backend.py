"""BigQuery backend built only on load jobs and metadata calls, so it runs in the sandbox.

Sandbox constraints this module is designed around (docs.cloud.google.com/bigquery/docs/sandbox):
no DML, no streaming inserts, tables/views/partitions expire after 60 days, and a lifetime
10 GiB storage quota that is not refunded on deletion.
"""

from __future__ import annotations

import io
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import polars as pl
import pyarrow.parquet as pq
from google.api_core.exceptions import GoogleAPICallError, NotFound
from google.cloud import bigquery

from proptech.settings import Environment
from proptech.warehouse.base import (
    TableSpec,
    Warehouse,
    WriteMode,
    WriteResult,
    estimate_logical_bytes,
)

log = logging.getLogger(__name__)

_BQ_TYPES: dict[type[pl.DataType], str] = {
    pl.Int64: "INT64",
    pl.Int32: "INT64",
    pl.Float64: "FLOAT64",
    pl.Float32: "FLOAT64",
    pl.Utf8: "STRING",
    pl.Date: "DATE",
    pl.Datetime: "TIMESTAMP",
    pl.Boolean: "BOOL",
}


def bq_schema(frame: pl.DataFrame) -> list[bigquery.SchemaField]:
    fields = []
    for name, dtype in frame.schema.items():
        bq_type = _BQ_TYPES.get(type(dtype))
        if bq_type is None:
            raise TypeError(f"column {name!r} has unsupported type {dtype}")
        fields.append(bigquery.SchemaField(name, bq_type, mode="NULLABLE"))
    return fields


class BigQueryWarehouse(Warehouse):
    def __init__(
        self,
        project: str,
        location: str,
        env: Environment,
        *,
        ttl_days: int = 59,
        max_bytes_billed: int | None = None,
        client: bigquery.Client | None = None,
    ) -> None:
        self.project = project
        self.location = location
        self.env = env
        self.ttl_days = ttl_days
        self.max_bytes_billed = max_bytes_billed
        self.client = client or bigquery.Client(project=project, location=location)
        self._layers: set[str] = set()

    # ------------------------------------------------------------------ helpers
    def _table_id(self, layer: str, name: str) -> str:
        return f"{self.project}.{layer}.{name}"

    def qualified(self, layer: str, name: str) -> str:
        return f"`{self._table_id(layer, name)}`"

    def ensure_layer(self, layer: str) -> None:
        if layer in self._layers:
            return
        dataset = bigquery.Dataset(f"{self.project}.{layer}")
        dataset.location = self.location
        dataset.labels = {"managed_by": "proptech"}
        self.client.create_dataset(dataset, exists_ok=True)
        self._layers.add(layer)

    def table_exists(self, layer: str, name: str) -> bool:
        try:
            self.client.get_table(self._table_id(layer, name))
        except NotFound:
            return False
        return True

    # ------------------------------------------------------------------ writes
    def write(self, spec: TableSpec, frame: pl.DataFrame, mode: WriteMode) -> WriteResult:
        self.ensure_layer(spec.layer)
        table_id = self._table_id(spec.layer, spec.name)

        config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.PARQUET,
            schema=bq_schema(frame),
            write_disposition=(
                bigquery.WriteDisposition.WRITE_TRUNCATE
                if mode is WriteMode.REPLACE
                else bigquery.WriteDisposition.WRITE_APPEND
            ),
            create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
            labels={"pipeline": "proptech", **spec.labels},
        )
        if mode is WriteMode.APPEND:
            config.schema_update_options = [bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION]
        if spec.clustering:
            config.clustering_fields = list(spec.clustering)
        if spec.partition_month_column and self.env is Environment.PROD:
            config.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.MONTH, field=spec.partition_month_column
            )

        buffer = io.BytesIO()
        pq.write_table(frame.to_arrow(), buffer, compression="zstd")
        buffer.seek(0)
        job = self.client.load_table_from_file(buffer, table_id, job_config=config)
        job.result()

        table = self.client.get_table(table_id)
        fields = []
        if spec.description and table.description != spec.description:
            table.description = spec.description
            fields.append("description")
        if spec.labels:
            table.labels = {**(table.labels or {}), "pipeline": "proptech", **spec.labels}
            fields.append("labels")
        if fields:
            self.client.update_table(table, fields)
        self.touch(spec.layer, spec.name)
        return WriteResult(table_id, frame.height, estimate_logical_bytes(frame))

    def touch(self, layer: str, name: str) -> None:
        """Push the sandbox's 60-day expiry forward; a no-op on billing-enabled projects."""
        if self.env is not Environment.SANDBOX:
            return
        table_id = self._table_id(layer, name)
        try:
            table = self.client.get_table(table_id)
            table.expires = datetime.now(UTC) + timedelta(days=self.ttl_days)
            self.client.update_table(table, ["expires"])
        except GoogleAPICallError as exc:
            log.warning("could not refresh table expiry", extra={"table": table_id, "error": str(exc)})

    # ------------------------------------------------------------------ reads
    def query(self, sql: str) -> list[dict[str, Any]]:
        config = bigquery.QueryJobConfig(
            maximum_bytes_billed=self.max_bytes_billed, labels={"pipeline": "proptech"}
        )
        rows = self.client.query(sql, job_config=config).result()
        return [dict(row.items()) for row in rows]

    def table_sizes(self, layer: str) -> list[dict[str, Any]]:
        """Logical bytes and creation time per table (metadata API, no query cost)."""
        result = []
        try:
            tables = list(self.client.list_tables(f"{self.project}.{layer}"))
        except NotFound:
            return result
        for item in tables:
            table = self.client.get_table(item.reference)
            result.append(
                {
                    "table": table.full_table_id,
                    "type": table.table_type,
                    "logical_bytes": table.num_bytes or 0,
                    "created": table.created,
                    "expires": table.expires,
                }
            )
        return result

    def close(self) -> None:
        self.client.close()
