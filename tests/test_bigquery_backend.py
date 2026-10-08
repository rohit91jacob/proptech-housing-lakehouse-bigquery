"""BigQuery backend behaviour verified against a mocked client (no credentials needed)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

import polars as pl
import pytest
from google.cloud import bigquery

from proptech.settings import Environment
from proptech.warehouse.base import TableSpec, WriteMode, estimate_logical_bytes
from proptech.warehouse.bigquery_backend import BigQueryWarehouse, bq_schema

FRAME = pl.DataFrame(
    {
        "region_id": [1, 2],
        "date": [date(2026, 7, 31), date(2026, 8, 31)],
        "value": [1.5, 2.5],
        "name": ["a", "bc"],
    }
)
SPEC = TableSpec(
    layer="raw_zillow",
    name="t",
    description="d",
    clustering=("region_id",),
    partition_month_column="date",
)


def _warehouse(env: Environment) -> tuple[BigQueryWarehouse, MagicMock]:
    client = MagicMock(spec=bigquery.Client)
    client.get_table.return_value = MagicMock(description=None, labels={})
    return BigQueryWarehouse("proj", "US", env, ttl_days=59, client=client), client


def _job_config(client: MagicMock) -> bigquery.LoadJobConfig:
    return client.load_table_from_file.call_args.kwargs["job_config"]


def test_schema_mapping() -> None:
    assert [(f.name, f.field_type) for f in bq_schema(FRAME)] == [
        ("region_id", "INT64"),
        ("date", "DATE"),
        ("value", "FLOAT64"),
        ("name", "STRING"),
    ]
    with pytest.raises(TypeError):
        bq_schema(pl.DataFrame({"x": [[1]]}))


def test_sandbox_load_is_truncate_without_partitions_and_refreshes_expiry() -> None:
    warehouse, client = _warehouse(Environment.SANDBOX)
    result = warehouse.write(SPEC, FRAME, WriteMode.REPLACE)
    config = _job_config(client)
    assert config.write_disposition == bigquery.WriteDisposition.WRITE_TRUNCATE
    assert config.source_format == bigquery.SourceFormat.PARQUET
    assert config.time_partitioning is None  # sandbox partitions would expire after 60 days
    assert config.clustering_fields == ["region_id"]
    assert result.rows == 2 and result.logical_bytes == estimate_logical_bytes(FRAME)

    updated = [c.args for c in client.update_table.call_args_list]
    expiry = [table for table, fields in updated if fields == ["expires"]]
    assert expiry, "sandbox tables must get their expiry pushed forward"
    assert expiry[0].expires > datetime.now(UTC) + timedelta(days=58)


def test_prod_load_partitions_by_month_and_never_touches_expiry() -> None:
    warehouse, client = _warehouse(Environment.PROD)
    warehouse.write(SPEC, FRAME, WriteMode.REPLACE)
    partitioning = _job_config(client).time_partitioning
    assert partitioning.type_ == bigquery.TimePartitioningType.MONTH
    assert partitioning.field == "date"
    assert all(c.args[1] != ["expires"] for c in client.update_table.call_args_list)


def test_append_allows_new_columns() -> None:
    warehouse, client = _warehouse(Environment.SANDBOX)
    warehouse.write(SPEC, FRAME, WriteMode.APPEND)
    config = _job_config(client)
    assert config.write_disposition == bigquery.WriteDisposition.WRITE_APPEND
    assert config.schema_update_options == [bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION]


def test_query_applies_bytes_billed_cap() -> None:
    warehouse, client = _warehouse(Environment.SANDBOX)
    warehouse.max_bytes_billed = 123
    client.query.return_value.result.return_value = []
    warehouse.query("select 1")
    assert client.query.call_args.kwargs["job_config"].maximum_bytes_billed == 123


def test_logical_byte_estimate_follows_bigquery_sizes() -> None:
    # 2 rows x (INT64 8 + DATE 8 + FLOAT64 8) + STRING (2 + len) per value
    assert estimate_logical_bytes(FRAME) == 2 * 24 + (2 + 1) + (2 + 2)
