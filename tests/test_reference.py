from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from proptech import reference

from .conftest import FIXTURES


def test_parse_pmms_fixture() -> None:
    frame = reference.parse_pmms(FIXTURES / "pmms" / "PMMS_history.csv")
    assert frame.columns == ["week_date", "rate_30y", "points_30y", "rate_15y"]
    assert frame.get_column("week_date").is_sorted()
    assert frame.get_column("week_date").max() == date(2026, 10, 1)
    report = reference.validate_pmms(frame, relaxed=True, today=date(2026, 10, 5))
    assert report.ok and not report.warnings


def test_pmms_tolerates_blank_columns(tmp_path: Path) -> None:
    path = tmp_path / "pmms.csv"
    path.write_text("date,pmms30,pmms30p,pmms15\n4/2/1971,7.33, ,\n4/9/1971,7.31,,\n", encoding="utf-8")
    frame = reference.parse_pmms(path)
    assert frame.get_column("rate_30y").to_list() == [7.33, 7.31]
    assert frame.get_column("rate_15y").null_count() == 2


def test_parse_acs_keeps_total_components_and_nulls_jam_values(tmp_path: Path) -> None:
    year_dir = FIXTURES / "acs" / "2024" / "table-based-SF"
    frame = reference.parse_acs(
        year_dir / "data" / "1YRData" / "acsdt1y2024-b19013.dat",
        year_dir / "documentation" / "Geos20241YR.txt",
        2024,
    )
    assert "0100089US" not in frame.get_column("geo_id").to_list()  # urban component dropped
    levels = dict(frame.group_by("geo_level").len().iter_rows())
    assert levels == {"country": 1, "state": 4, "county": 4, "cbsa": 5}
    county = frame.filter(pl.col("geo_level") == "county").row(0, named=True)
    assert (county["state_fips"], county["county_fips"]) == ("06", "901")
    assert reference.validate_acs(frame, 2024, relaxed=True).ok

    data = tmp_path / "d.dat"
    data.write_text("GEO_ID|B19013_E001|B19013_M001\n0100000US|-666666666|-222222222\n", encoding="utf-8")
    jammed = reference.parse_acs(data, year_dir / "documentation" / "Geos20241YR.txt", 2024)
    assert jammed.get_column("median_household_income").to_list() == [None]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Nashville-Davidson--Murfreesboro--Franklin, TN Metro Area", "Nashville, TN"),
        ("Winston-Salem, NC Metro Area", "Winston-Salem, NC"),
        ("Louisville/Jefferson County, KY-IN Metro Area", "Louisville, KY"),
        ("Wildwood-The Villages, FL Metro Area", "The Villages, FL"),
        ("New York-Newark-Jersey City, NY-NJ Metro Area", "New York, NY"),
    ],
)
def test_cbsa_candidates_cover_zillow_naming(title: str, expected: str) -> None:
    acs = pl.DataFrame(
        {"acs_year": [2024], "cbsa_code": ["99999"], "geo_name": [title], "geo_level": ["cbsa"]}
    )
    candidates = reference.cbsa_name_candidates(acs)
    assert expected in candidates.get_column("candidate_name").to_list()
    first = candidates.sort("candidate_rank").row(0, named=True)
    assert first["candidate_rank"] == 1
    assert first["metro_micro"] == "metro"


def test_secondary_cities_rank_after_prefixes() -> None:
    acs = pl.DataFrame(
        {
            "acs_year": [2024],
            "cbsa_code": ["48680"],
            "geo_name": ["Wildwood-The Villages, FL Metro Area"],
            "geo_level": ["cbsa"],
        }
    )
    ranks = dict(reference.cbsa_name_candidates(acs).select("candidate_name", "candidate_rank").iter_rows())
    assert ranks["Wildwood, FL"] == 1
    assert ranks["Wildwood-The Villages, FL"] == 2
    assert ranks["The Villages, FL"] == 101
