from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from proptech.catalog import Catalog
from proptech.validation import ValidationError, is_month_end, validate
from proptech.zillow import parse_file

HEADER = "RegionID,SizeRank,RegionName,RegionType,StateName,2026-06-30,2026-07-31,2026-08-31"


def _parsed(catalog: Catalog, tmp_path: Path, rows: list[str], key: str = "zhvi_metro_mid_all"):
    path = tmp_path / "f.csv"
    path.write_text("\n".join([HEADER, *rows]) + "\n", encoding="utf-8")
    return parse_file(catalog.get(key), path)


def test_clean_file_passes(catalog: Catalog, tmp_path: Path) -> None:
    parsed = _parsed(catalog, tmp_path, ['1,0,"A, CA",msa,CA,100000,101000,102000'])
    report = validate(parsed, relaxed=True, today=date(2026, 9, 20))
    assert report.ok, report.errors
    assert report.stats["last_month"] == date(2026, 8, 31)
    assert report.warnings == []


def test_truncated_file_is_rejected_by_region_floor(catalog: Catalog, tmp_path: Path) -> None:
    parsed = _parsed(catalog, tmp_path, ['1,0,"A, CA",msa,CA,100000,101000,102000'])
    report = validate(parsed, relaxed=False)
    assert any("truncated download" in e for e in report.errors)
    with pytest.raises(ValidationError):
        report.raise_for_errors()


def test_duplicate_ids_and_unexpected_region_types(catalog: Catalog, tmp_path: Path) -> None:
    parsed = _parsed(
        catalog,
        tmp_path,
        ['1,0,"A, CA",msa,CA,1000,1000,1000', '1,1,"B, CA",county,CA,1000,1000,1000'],
    )
    errors = " ".join(validate(parsed, relaxed=True).errors)
    assert "duplicate RegionID" in errors
    assert "unexpected RegionType values ['county']" in errors


def test_isolated_outliers_are_quarantined(catalog: Catalog, tmp_path: Path) -> None:
    rows = [f'{i},{i},"M{i}, CA",msa,CA,100000,100000,100000' for i in range(1, 400)]
    rows.append('999,999,"Odd, CA",msa,CA,100000,100000,999999999999')  # 1 of 1200 values
    report = validate(_parsed(catalog, tmp_path, rows), relaxed=True, max_reject_ratio=0.001)
    assert report.ok
    assert report.rejected is not None and report.rejected.height == 1
    assert report.rejected.row(0, named=True)["region_id"] == 999
    assert any(w.startswith("quarantined") for w in report.warnings)


def test_systematic_range_violations_fail(catalog: Catalog, tmp_path: Path) -> None:
    rows = ['1,0,"A, CA",msa,CA,-5,-5,-5', '2,1,"B, CA",msa,CA,100000,100000,100000']
    report = validate(_parsed(catalog, tmp_path, rows), relaxed=True, max_reject_ratio=0.001)
    assert not report.ok
    assert report.rejected is None


def test_stale_latest_month_warns(catalog: Catalog, tmp_path: Path) -> None:
    parsed = _parsed(catalog, tmp_path, ['1,0,"A, CA",msa,CA,100000,101000,102000'])
    report = validate(parsed, relaxed=True, today=date(2027, 3, 1))
    assert report.ok
    assert any("days old" in w for w in report.warnings)


def test_interior_gaps_warn(catalog: Catalog, tmp_path: Path) -> None:
    parsed = _parsed(catalog, tmp_path, ['1,0,"A, CA",msa,CA,100000,,102000'])
    report = validate(parsed, relaxed=True, today=date(2026, 9, 20))
    assert report.stats["interior_gap_ratio"] == pytest.approx(1 / 3, rel=1e-6)
    assert any("interior region-months" in w for w in report.warnings)


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2024, 2, 29), True),
        (date(2023, 2, 28), True),
        (date(2024, 2, 28), False),
        (date(2026, 12, 31), True),
    ],
)
def test_is_month_end(day: date, expected: bool) -> None:
    assert is_month_end(day) is expected
