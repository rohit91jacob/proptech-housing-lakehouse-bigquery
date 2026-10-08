"""Parse Zillow Research CSVs (one column per month) into tidy long tables."""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import polars as pl

from proptech.catalog import Dataset, Kind

log = logging.getLogger(__name__)

DATE_COLUMN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Zillow header -> warehouse column. Anything not listed (and not a date) is reported as drift.
META_COLUMNS: dict[str, str] = {
    "RegionID": "region_id",
    "SizeRank": "size_rank",
    "RegionName": "region_name",
    "RegionType": "region_type",
    "StateName": "state_name",
    "State": "state",
    "City": "city",
    "Metro": "metro",
    "CountyName": "county_name",
    "StateCodeFIPS": "state_fips",
    "MunicipalCodeFIPS": "county_fips",
    "BaseDate": "base_date",
}

REGION_SCHEMA: dict[str, pl.DataType] = {
    "region_id": pl.Int64(),
    "size_rank": pl.Int64(),
    "region_name": pl.Utf8(),
    "region_type": pl.Utf8(),
    "state_name": pl.Utf8(),
    "state": pl.Utf8(),
    "city": pl.Utf8(),
    "metro": pl.Utf8(),
    "county_name": pl.Utf8(),
    "state_fips": pl.Utf8(),
    "county_fips": pl.Utf8(),
}

TIMESERIES_SCHEMA: dict[str, pl.DataType] = {
    "region_id": pl.Int64(),
    "date": pl.Date(),
    "value": pl.Float64(),
}

FORECAST_SCHEMA: dict[str, pl.DataType] = {
    "region_id": pl.Int64(),
    "base_date": pl.Date(),
    "horizon_date": pl.Date(),
    "horizon_months": pl.Int64(),
    "value": pl.Float64(),
}


@dataclass
class ParsedFile:
    dataset: Dataset
    header: list[str]
    date_columns: list[str]
    regions: pl.DataFrame
    values: pl.DataFrame
    unknown_columns: list[str] = field(default_factory=list)
    raw_rows: int = 0

    @property
    def months(self) -> list[date]:
        return [date.fromisoformat(c) for c in self.date_columns]


def read_header(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return next(csv.reader(handle))


def parse_file(dataset: Dataset, path: Path) -> ParsedFile:
    header = read_header(path)
    date_columns = [c for c in header if DATE_COLUMN.match(c)]
    meta = [c for c in header if not DATE_COLUMN.match(c)]
    unknown = [c for c in meta if c not in META_COLUMNS]
    if unknown:
        log.warning(
            "schema drift: unknown columns ignored",
            extra={"dataset": dataset.key, "columns": unknown},
        )

    schema = pl.Schema([(c, pl.Utf8() if c in meta else pl.Float64()) for c in header])
    frame = pl.read_csv(path, schema=schema, encoding="utf8-lossy", null_values=[""])

    present = {c: META_COLUMNS[c] for c in meta if c in META_COLUMNS}
    regions = frame.select(
        [pl.col(src).alias(dst) for src, dst in present.items() if dst != "base_date"]
    ).with_columns(
        pl.col("region_id").str.strip_chars().cast(pl.Int64, strict=True),
        pl.col("size_rank").str.strip_chars().cast(pl.Int64, strict=False)
        if "size_rank" in present.values()
        else pl.lit(None, pl.Int64).alias("size_rank"),
    )
    for column, dtype in REGION_SCHEMA.items():
        if column not in regions.columns:
            regions = regions.with_columns(pl.lit(None, dtype).alias(column))
    text_columns = [c for c, dtype in REGION_SCHEMA.items() if dtype == pl.Utf8()]
    regions = regions.select(list(REGION_SCHEMA)).with_columns(
        [
            pl.when(pl.col(c).str.strip_chars() == "")
            .then(None)
            .otherwise(pl.col(c).str.strip_chars())
            .alias(c)
            for c in text_columns
        ]
    )

    if dataset.kind is Kind.FORECAST:
        values = _forecast_values(frame, date_columns)
    else:
        values = _timeseries_values(frame, date_columns)

    return ParsedFile(
        dataset=dataset,
        header=header,
        date_columns=date_columns,
        regions=regions,
        values=values,
        unknown_columns=unknown,
        raw_rows=frame.height,
    )


def _timeseries_values(frame: pl.DataFrame, date_columns: list[str]) -> pl.DataFrame:
    if not date_columns:
        return pl.DataFrame(schema=TIMESERIES_SCHEMA)
    return (
        frame.select(["RegionID", *date_columns])
        .unpivot(index="RegionID", on=date_columns, variable_name="date", value_name="value")
        .drop_nulls("value")
        .select(
            pl.col("RegionID").str.strip_chars().cast(pl.Int64).alias("region_id"),
            pl.col("date").str.to_date("%Y-%m-%d").alias("date"),
            pl.col("value").cast(pl.Float64),
        )
        .sort(["region_id", "date"])
    )


def _forecast_values(frame: pl.DataFrame, date_columns: list[str]) -> pl.DataFrame:
    if "BaseDate" not in frame.columns or not date_columns:
        return pl.DataFrame(schema=FORECAST_SCHEMA)
    long = (
        frame.select(["RegionID", "BaseDate", *date_columns])
        .unpivot(
            index=["RegionID", "BaseDate"],
            on=date_columns,
            variable_name="horizon_date",
            value_name="value",
        )
        .drop_nulls("value")
        .select(
            pl.col("RegionID").str.strip_chars().cast(pl.Int64).alias("region_id"),
            pl.col("BaseDate").str.to_date("%Y-%m-%d").alias("base_date"),
            pl.col("horizon_date").str.to_date("%Y-%m-%d").alias("horizon_date"),
            pl.col("value").cast(pl.Float64),
        )
    )
    return (
        long.with_columns(
            (
                (pl.col("horizon_date").dt.year() - pl.col("base_date").dt.year()) * 12
                + pl.col("horizon_date").dt.month()
                - pl.col("base_date").dt.month()
            )
            .cast(pl.Int64)
            .alias("horizon_months")
        )
        .select(list(FORECAST_SCHEMA))
        .sort(["region_id", "horizon_months"])
    )
