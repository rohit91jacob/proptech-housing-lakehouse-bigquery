"""Load-time data-quality gate. Anything listed in ``errors`` blocks the load of that dataset."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import polars as pl

from proptech.catalog import REGION_TYPES, REQUIRED_COLUMNS, Kind
from proptech.zillow import ParsedFile

# Latest month older than this many days (relative to "today") produces a warning.
STALE_AFTER_DAYS = 75


class ValidationError(ValueError):
    def __init__(self, dataset_key: str, errors: list[str]) -> None:
        super().__init__(f"{dataset_key}: " + "; ".join(errors))
        self.dataset_key = dataset_key
        self.errors = errors


@dataclass
class ValidationReport:
    dataset_key: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    # Rows excluded from the load (quarantined) with a ``reason`` column.
    rejected: pl.DataFrame | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> None:
        if self.errors:
            raise ValidationError(self.dataset_key, self.errors)


def is_month_end(day: date) -> bool:
    return (day + timedelta(days=1)).day == 1


def month_index(day: date) -> int:
    return day.year * 12 + day.month - 1


def validate(
    parsed: ParsedFile,
    *,
    today: date | None = None,
    relaxed: bool = False,
    max_reject_ratio: float = 0.001,
) -> ValidationReport:
    spec = parsed.dataset
    report = ValidationReport(spec.key)
    today = today or date.today()

    missing = [c for c in REQUIRED_COLUMNS[spec.geography] if c not in parsed.header]
    if spec.kind is Kind.FORECAST and "BaseDate" not in parsed.header:
        missing.append("BaseDate")
    if missing:
        report.errors.append(f"missing required columns {missing}")
        return report

    months = parsed.months
    if not months:
        report.errors.append("no month columns found")
        return report
    not_month_end = [m.isoformat() for m in months if not is_month_end(m)]
    if not_month_end:
        report.errors.append(f"month columns are not month-end dates: {not_month_end[:5]}")
    if spec.kind is Kind.TIMESERIES:
        indexes = [month_index(m) for m in months]
        gaps = [months[i].isoformat() for i in range(1, len(indexes)) if indexes[i] - indexes[i - 1] != 1]
        if gaps:
            report.errors.append(f"month columns are not contiguous/increasing near {gaps[:5]}")

    regions = parsed.regions
    report.stats["regions"] = regions.height
    min_regions = 1 if relaxed else spec.min_regions
    if regions.height < min_regions:
        report.errors.append(
            f"only {regions.height} regions, expected >= {min_regions} (truncated download?)"
        )
    null_ids = regions.filter(pl.col("region_id").is_null()).height
    if null_ids:
        report.errors.append(f"{null_ids} rows with null RegionID")
    dupes = regions.height - regions.select("region_id").unique().height
    if dupes:
        report.errors.append(f"{dupes} duplicate RegionID values")
    seen_types = set(regions.get_column("region_type").drop_nulls().unique().to_list())
    unexpected = sorted(seen_types - REGION_TYPES[spec.geography])
    if unexpected:
        report.errors.append(f"unexpected RegionType values {unexpected}")

    values = parsed.values
    report.stats["observations"] = values.height
    if values.height == 0:
        report.errors.append("file contains no non-null values")
        return report

    non_finite = values.filter(~pl.col("value").is_finite()).height
    if non_finite:
        report.errors.append(f"{non_finite} non-finite values")
    low, high = spec.value_range
    out_of_range = values.filter((pl.col("value") < low) | (pl.col("value") > high))
    if out_of_range.height:
        ratio = out_of_range.height / values.height
        sample = out_of_range.head(3).to_dicts()
        message = f"{out_of_range.height} values ({ratio:.3%}) outside [{low}, {high}], e.g. {sample}"
        if ratio <= max_reject_ratio:
            report.warnings.append("quarantined " + message)
            report.rejected = out_of_range.with_columns(pl.lit(f"outside [{low}, {high}]").alias("reason"))
        else:
            report.errors.append(message)

    if spec.kind is Kind.TIMESERIES:
        first, last = values.get_column("date").min(), values.get_column("date").max()
        report.stats.update(first_month=first, last_month=last)
        if isinstance(last, date) and (today - last).days > STALE_AFTER_DAYS:
            report.warnings.append(f"latest month {last} is more than {STALE_AFTER_DAYS} days old")
        gap_ratio = _interior_gap_ratio(values)
        report.stats["interior_gap_ratio"] = round(gap_ratio, 6)
        if gap_ratio > 0.01:
            report.warnings.append(f"{gap_ratio:.2%} of interior region-months are missing")
    else:
        base_dates = values.get_column("base_date").unique().to_list()
        report.stats.update(base_dates=sorted(base_dates))
        if len(base_dates) != 1:
            report.warnings.append(f"expected one BaseDate per vintage, found {len(base_dates)}")
        bad_horizon = values.filter(pl.col("horizon_months") <= 0).height
        if bad_horizon:
            report.errors.append(f"{bad_horizon} forecasts with non-positive horizons")
    return report


def _interior_gap_ratio(values: pl.DataFrame) -> float:
    """Share of months missing between each region's first and last observation."""
    spans = values.group_by("region_id").agg(
        pl.len().alias("observed"),
        pl.col("date").min().alias("first"),
        pl.col("date").max().alias("last"),
    )
    spans = spans.with_columns(
        (
            (pl.col("last").dt.year() - pl.col("first").dt.year()) * 12
            + pl.col("last").dt.month()
            - pl.col("first").dt.month()
            + 1
        ).alias("expected")
    )
    expected = spans.get_column("expected").sum()
    observed = spans.get_column("observed").sum()
    return 0.0 if not expected else float(expected - observed) / float(expected)
