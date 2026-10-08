from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from proptech.catalog import Catalog, Dataset, Geography
from proptech.validation import validate
from proptech.zillow import parse_file

from .conftest import FIXTURES


def _write(path: Path, text: str) -> Path:
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def _metro(catalog: Catalog) -> Dataset:
    return catalog.get("zhvi_metro_mid_all")


def test_parse_metro_fixture_to_long_format(catalog: Catalog) -> None:
    dataset = _metro(catalog)
    parsed = parse_file(dataset, FIXTURES / "zillow" / dataset.path)
    assert parsed.regions.height == 6
    assert parsed.values.columns == ["region_id", "date", "value"]
    assert parsed.values.schema["date"] == pl.Date
    us = parsed.regions.filter(pl.col("region_id") == 102001).row(0, named=True)
    assert us["region_type"] == "country"
    assert us["state_name"] is None  # empty strings become NULL
    assert parsed.values.height == 6 * len(parsed.date_columns)


def test_nulls_are_dropped_and_zip_codes_keep_leading_zeros(tmp_path: Path) -> None:
    dataset = Dataset(
        key="zori_zip_test",
        family="zori",
        metric="rent",
        path="x.csv",
        geography=Geography.ZIP,
        unit="usd_per_month",
        min_regions=1,
        value_range=(100, 100000),
        description="test",
    )
    path = _write(
        tmp_path / "zip.csv",
        """
RegionID,SizeRank,RegionName,RegionType,StateName,State,City,Metro,CountyName,2026-06-30,2026-07-31
61148,1,08701,zip,NJ,NJ,Lakewood,"New York-Newark-Jersey City, NY-NJ-PA",Ocean County,,2185.5
""",
    )
    parsed = parse_file(dataset, path)
    region = parsed.regions.row(0, named=True)
    assert region["region_name"] == "08701"
    assert region["metro"] == "New York-Newark-Jersey City, NY-NJ-PA"
    assert parsed.values.to_dicts() == [{"region_id": 61148, "date": date(2026, 7, 31), "value": 2185.5}]


def test_forecast_horizons_are_computed(catalog: Catalog) -> None:
    dataset = catalog.get("zhvf_metro_mid_all")
    parsed = parse_file(dataset, FIXTURES / "zillow" / dataset.path)
    assert sorted(parsed.values.get_column("horizon_months").unique().to_list()) == [1, 3, 12]
    assert parsed.values.get_column("base_date").unique().to_list() == [date(2026, 8, 31)]


def test_unknown_columns_are_reported_not_loaded(catalog: Catalog, tmp_path: Path) -> None:
    path = _write(
        tmp_path / "m.csv",
        """
RegionID,SizeRank,RegionName,RegionType,StateName,NewColumn,2026-07-31
1,0,"Lakeview, CA",msa,CA,surprise,123456.0
""",
    )
    parsed = parse_file(_metro(catalog), path)
    assert parsed.unknown_columns == ["NewColumn"]
    assert "NewColumn" not in parsed.regions.columns


@pytest.mark.parametrize(
    ("header_dates", "message"),
    [
        ("2026-06-30,2026-08-31", "not contiguous"),
        ("2026-06-15,2026-07-31", "not month-end"),
    ],
)
def test_month_columns_must_be_contiguous_month_ends(
    catalog: Catalog, tmp_path: Path, header_dates: str, message: str
) -> None:
    path = _write(
        tmp_path / "m.csv",
        f"""
RegionID,SizeRank,RegionName,RegionType,StateName,{header_dates}
1,0,"Lakeview, CA",msa,CA,100000.0,101000.0
""",
    )
    report = validate(parse_file(_metro(catalog), path), relaxed=True)
    assert not report.ok
    assert any(message in e for e in report.errors)
