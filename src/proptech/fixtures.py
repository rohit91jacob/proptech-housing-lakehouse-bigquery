"""Deterministic synthetic fixtures that mirror the real source layouts byte-for-byte in schema.

The repository never redistributes Zillow, Freddie Mac or Census data. CI and the test-suite
run the full pipeline against these files instead (``file://`` source URLs). Regions, names
and values are fictional, but each file reproduces the real headers, RegionType values,
FIPS formatting and month-end date columns.
"""

from __future__ import annotations

import csv
import itertools
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from proptech.catalog import Catalog, Dataset, Geography, Kind

LAST_MONTH = date(2026, 8, 31)
FORECAST_HORIZONS = (date(2026, 9, 30), date(2026, 11, 30), date(2027, 8, 31))
ACS_YEARS = (2021, 2022, 2023, 2024)

START_BY_FAMILY = {
    "zhvi": date(2010, 1, 31),
    "zori": date(2015, 1, 31),
    "market": date(2018, 3, 31),
    "affordability": date(2012, 1, 31),
    "new_construction": date(2018, 1, 31),
}


@dataclass(frozen=True)
class Metro:
    region_id: int
    size_rank: int
    name: str
    state: str
    base_value: float
    growth: float  # annualised home-value growth
    rent_ratio: float  # monthly rent as a share of value


COUNTRY = Metro(102001, 0, "United States", "", 250_000, 0.045, 0.0058)
METROS = (
    Metro(900101, 1, "Lakeview, CA", "CA", 520_000, 0.055, 0.0042),
    Metro(900102, 2, "Pine Ridge, TX", "TX", 210_000, 0.040, 0.0071),
    Metro(900103, 3, "Twin-Oaks, FL", "FL", 260_000, 0.060, 0.0066),
    Metro(900104, 4, "Granite Falls, CO", "CO", 310_000, 0.050, 0.0057),
    Metro(900105, 5, "Maple Grove, CO", "CO", 280_000, 0.035, 0.0060),
)
# (region_id, size_rank, state name, state code, state fips, value multiplier)
STATES = (
    (800006, 0, "California", "CA", "06", 1.9),
    (800048, 1, "Texas", "TX", "48", 0.85),
    (800012, 2, "Florida", "FL", "12", 1.05),
    (800008, 3, "Colorado", "CO", "08", 1.25),
)
# (region_id, size_rank, county, state code, state fips, county fips, CBSA title, multiplier)
COUNTIES = (
    (700001, 0, "Lakeview County", "CA", "06", "901", "Lakeview-Riverside-Brookfield, CA", 2.0),
    (700002, 1, "Pine County", "TX", "48", "901", "Pine Ridge-Cedar Park, TX", 0.8),
    (700003, 2, "Bay County", "FL", "12", "901", "Twin-Oaks-Bayside, FL", 1.0),
    (700004, 3, "Maple County", "CO", "08", "903", "Maple Grove, CO", 1.1),
)
# (CBSA code, title as published by the Census Bureau, kind)
CBSAS = (
    ("90010", "Lakeview-Riverside-Brookfield, CA Metro Area"),
    ("90020", "Pine Ridge-Cedar Park, TX Metro Area"),
    ("90030", "Twin-Oaks-Bayside, FL Metro Area"),
    ("90040", "Granite City, CO Micro Area"),
    ("90050", "Maple Grove, CO Metro Area"),
)


def month_ends(start: date, end: date) -> list[date]:
    out, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        nxt = date(year + (month == 12), month % 12 + 1, 1)
        out.append(nxt - timedelta(days=1))
        year, month = nxt.year, nxt.month
    return out


def _years_since(day: date, origin: date = date(2010, 1, 31)) -> float:
    return (day - origin).days / 365.25


def home_value(metro: Metro, day: date, multiplier: float = 1.0) -> float:
    t = _years_since(day)
    seasonal = 1 + 0.004 * math.sin(2 * math.pi * (day.month - 3) / 12)
    # a 2022-23 correction makes drawdown metrics non-trivial
    correction = 1 - 0.06 * max(0.0, min(1.0, (t - 12.5) / 1.0)) + 0.03 * max(0.0, min(1.0, (t - 14.0) / 2.0))
    return round(metro.base_value * multiplier * (1 + metro.growth) ** t * seasonal * correction, 6)


def mortgage_rate(day: date) -> float:
    """Piecewise-linear 30-year rate path: ~4% (2012-19), ~3% (2021), ~7% (2023-26)."""
    knots = [
        (date(2011, 1, 1), 4.6),
        (date(2016, 1, 1), 3.9),
        (date(2019, 1, 1), 4.5),
        (date(2021, 1, 1), 2.8),
        (date(2022, 1, 1), 3.2),
        (date(2023, 10, 1), 7.6),
        (date(2025, 1, 1), 6.8),
        (date(2026, 12, 31), 6.6),
    ]
    for (d0, r0), (d1, r1) in itertools.pairwise(knots):
        if d0 <= day <= d1:
            share = (day - d0).days / (d1 - d0).days
            return round(r0 + (r1 - r0) * share, 2)
    return knots[-1][1]


def _metric_value(dataset: Dataset, metro: Metro, day: date, multiplier: float) -> float | None:
    hv = home_value(metro, day, multiplier)
    t = _years_since(day)
    wave = math.sin(2 * math.pi * day.month / 12)
    metric = dataset.metric
    if metric == "home_value":
        tier = {"bottom": 0.62, "mid": 1.0, "top": 1.75}.get(dataset.tier or "mid", 1.0)
        ptype = {"sfr": 1.06, "condo": 0.78}.get(dataset.property_type or "", 1.0)
        beds = {1: 0.55, 2: 0.75, 3: 0.95, 4: 1.25, 5: 1.6}.get(dataset.bedrooms or 0, 1.0)
        return round(hv * tier * ptype * beds, 6)
    if metric == "rent":
        ptype = {"sfr": 1.12, "mfr": 0.9}.get(dataset.property_type or "", 1.0)
        return round(hv * metro.rent_ratio * ptype, 6)
    counts = 40_000.0 if metro.size_rank == 0 else 2_500.0 / metro.size_rank
    values = {
        "for_sale_inventory": counts * (1.1 - 0.15 * wave),
        "new_listings": counts * 0.35 * (1 + 0.2 * wave),
        "newly_pending": counts * 0.3 * (1 + 0.2 * wave),
        "median_list_price": hv * 1.04,
        "median_sale_price": hv * 0.99,
        "median_sale_to_list": 0.985 + 0.01 * wave,
        "share_sold_above_list": 0.22 + 0.08 * wave,
        "share_sold_below_list": 0.55 - 0.08 * wave,
        "median_days_to_pending": 21 - 6 * wave,
        "share_listings_price_cut": 0.18 + 0.05 * math.cos(2 * math.pi * day.month / 12),
        "sales_count": counts * 0.28 * (1 + 0.25 * wave),
        "market_heat_index": 50 + 12 * wave - metro.size_rank,
        "new_construction_sales_count": counts * 0.05,
        "new_construction_median_sale_price": hv * 1.15,
    }
    if metric in values:
        return round(values[metric], 6)
    payment = _payment(hv * 0.8, mortgage_rate(day)) * 1.3  # P&I plus taxes/insurance
    income = 60_000 * (1.035 ** (t - 2)) * (1.4 if metro.state == "CA" else 1.0)
    affordability = {
        "total_monthly_payment": payment,
        "homeowner_income_needed": payment * 12 / 0.30,
        "homeowner_affordability": payment * 12 / income,
        "renter_income_needed": hv * metro.rent_ratio * 12 / 0.30,
        "renter_affordability": hv * metro.rent_ratio * 12 / (income * 0.65),
    }
    return round(affordability[metric], 6) if metric in affordability else None


def _payment(principal: float, annual_rate_pct: float, months: int = 360) -> float:
    r = annual_rate_pct / 100 / 12
    return principal * r / (1 - (1 + r) ** -months)


def _write_csv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _fmt(value: float | None) -> str:
    return "" if value is None else repr(float(value))


def write_zillow(dataset: Dataset, root: Path) -> Path:
    path = root / dataset.path
    if dataset.kind is Kind.FORECAST:
        header = ["RegionID", "SizeRank", "RegionName", "RegionType", "StateName", "BaseDate"]
        header += [h.isoformat() for h in FORECAST_HORIZONS]
        rows = []
        for metro in (COUNTRY, *METROS):
            region_type = "country" if metro is COUNTRY else "msa"
            growth = [round(metro.growth * 100 * m / 12, 1) for m in (1, 3, 12)]
            rows.append(
                [
                    metro.region_id,
                    metro.size_rank,
                    metro.name,
                    region_type,
                    metro.state,
                    LAST_MONTH.isoformat(),
                    *growth,
                ]
            )
        _write_csv(path, header, rows)
        return path

    months = month_ends(START_BY_FAMILY[dataset.family], LAST_MONTH)
    month_cols = [m.isoformat() for m in months]
    if dataset.geography is Geography.METRO:
        header = ["RegionID", "SizeRank", "RegionName", "RegionType", "StateName", *month_cols]
        rows = []
        for metro in (COUNTRY, *METROS):
            region_type = "country" if metro is COUNTRY else "msa"
            values = []
            for m in months:
                # Granite Falls has no rent history before 2020: exercises late-starting series.
                if dataset.family == "zori" and metro.region_id == 900104 and m.year < 2020:
                    values.append("")
                else:
                    values.append(_fmt(_metric_value(dataset, metro, m, 1.0)))
            rows.append([metro.region_id, metro.size_rank, metro.name, region_type, metro.state, *values])
    elif dataset.geography is Geography.STATE:
        header = ["RegionID", "SizeRank", "RegionName", "RegionType", "StateName", *month_cols]
        rows = [
            [
                rid,
                rank,
                name,
                "state",
                "",
                *[_fmt(_metric_value(dataset, COUNTRY, m, mult)) for m in months],
            ]
            for rid, rank, name, _code, _fips, mult in STATES
        ]
    elif dataset.geography is Geography.COUNTY:
        header = [
            "RegionID", "SizeRank", "RegionName", "RegionType", "StateName", "State",
            "Metro", "StateCodeFIPS", "MunicipalCodeFIPS", *month_cols,
        ]  # fmt: skip
        rows = [
            [rid, rank, name, "county", code, code, metro, sfips, cfips,
             *[_fmt(_metric_value(dataset, COUNTRY, m, mult)) for m in months]]
            for rid, rank, name, code, sfips, cfips, metro, mult in COUNTIES
        ]  # fmt: skip
    else:
        raise ValueError(f"no fixture generator for geography {dataset.geography}")
    _write_csv(path, header, rows)
    return path


def write_pmms(path: Path) -> Path:
    first = date(2011, 12, 29)  # a Thursday, like every PMMS release
    weeks = []
    day = first
    while day <= date(2026, 10, 1):
        weeks.append(day)
        day += timedelta(days=7)
    header = [
        "date",
        "pmms30",
        "pmms30p",
        "pmms15",
        "pmms15p",
        "pmms51",
        "pmms51p",
        "pmms51m",
        "pmms51spread",
    ]
    rows = [
        [
            f"{d.month}/{d.day}/{d.year}",
            f"{mortgage_rate(d):.2f}",
            "0.7",
            f"{mortgage_rate(d) - 0.7:.2f}",
            "",
            "",
            "",
            "",
            "",
        ]
        for d in weeks
    ]
    _write_csv(path, header, rows)
    return path


GEOS_HEADER = [
    "FILEID",
    "STUSAB",
    "SUMLEVEL",
    "COMPONENT",
    "US",
    "REGION",
    "DIVISION",
    "STATE",
    "COUNTY",
    "COUSUB",
    "PLACE",
    "TRACT",
    "BLKGRP",
    "CONCIT",
    "AIANHH",
    "AIANHHFP",
    "AIHHTLI",
    "AITS",
    "AITSFP",
    "ANRC",
    "CBSA",
    "CSA",
    "METDIV",
    "MACC",
    "MEMI",
    "NECTA",
    "CNECTA",
    "NECTADIV",
    "UA",
    "CDCURR",
    "SLDU",
    "SLDL",
    "ZCTA5",
    "SUBMCD",
    "SDELM",
    "SDSEC",
    "SDUNI",
    "UR",
    "PCI",
    "PUMA5",
    "GEO_ID",
    "NAME",
    "BTTR",
    "BTBG",
    "TL_GEO_ID",
]


def _geo_row(
    *,
    sumlevel: str,
    geo_id: str,
    name: str,
    stusab: str = "US",
    state: str = "",
    county: str = "",
    cbsa: str = "",
) -> str:
    values = dict.fromkeys(GEOS_HEADER, "")
    values.update(
        FILEID="ACSSF",
        STUSAB=stusab,
        SUMLEVEL=sumlevel,
        COMPONENT="00",
        STATE=state,
        COUNTY=county,
        CBSA=cbsa,
        GEO_ID=geo_id,
        NAME=name,
    )
    return "|".join(values[c] for c in GEOS_HEADER)


def write_acs(root: Path) -> list[Path]:
    paths = []
    state_names = {code: name for _rid, _rank, name, code, _fips, _mult in STATES}
    for year in ACS_YEARS:
        growth = 1.04 ** (year - 2021)
        estimates = [("0100000US", 70_000 * growth)]
        geos = [_geo_row(sumlevel="010", geo_id="0100000US", name="United States")]
        for _rid, _rank, name, code, fips, mult in STATES:
            geo_id = f"0400000US{fips}"
            estimates.append((geo_id, 62_000 * growth * (0.9 + mult / 5)))
            geos.append(_geo_row(sumlevel="040", geo_id=geo_id, name=name, stusab=code.lower(), state=fips))
        for _rid, _rank, name, code, sfips, cfips, _metro, mult in COUNTIES:
            geo_id = f"0500000US{sfips}{cfips}"
            estimates.append((geo_id, 58_000 * growth * (0.9 + mult / 5)))
            geos.append(
                _geo_row(
                    sumlevel="050",
                    geo_id=geo_id,
                    name=f"{name}, {state_names[code]}",
                    stusab=code.lower(),
                    state=sfips,
                    county=cfips,
                )
            )
        for i, (code, title) in enumerate(CBSAS):
            geo_id = ("310M600US" if title.endswith("Micro Area") else "310M700US") + code
            estimates.append((geo_id, 64_000 * growth * (1 + 0.1 * i)))
            geos.append(_geo_row(sumlevel="310", geo_id=geo_id, name=title, cbsa=code))
        # A geographic-component row (urban portion) that the parser must ignore.
        urban = _geo_row(sumlevel="010", geo_id="0100089US", name="United States -- Urban")
        geos.append(urban.replace("|010|00|", "|010|89|"))
        estimates.append(("0100089US", 71_111))

        base = root / str(year) / "table-based-SF"
        data_path = base / "data" / "1YRData" / f"acsdt1y{year}-b19013.dat"
        geos_path = base / "documentation" / f"Geos{year}1YR.txt"
        data_path.parent.mkdir(parents=True, exist_ok=True)
        geos_path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["GEO_ID|B19013_E001|B19013_M001"] + [
            f"{g}|{round(v)}|{round(v * 0.02)}" for g, v in estimates
        ]
        data_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        geos_path.write_text("\n".join(["|".join(GEOS_HEADER), *geos]) + "\n", encoding="utf-8", newline="\n")
        paths += [data_path, geos_path]
    return paths


def generate(catalog: Catalog, out: Path, profile: str = "core") -> list[Path]:
    written = [write_zillow(d, out / "zillow") for d in catalog.for_profile(profile)]
    written.append(write_pmms(out / "pmms" / "PMMS_history.csv"))
    written += write_acs(out / "acs")
    return written
