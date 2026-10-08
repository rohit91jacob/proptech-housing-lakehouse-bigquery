"""Reference data: Freddie Mac PMMS mortgage rates and Census ACS median household income."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

from proptech.validation import ValidationReport

ACS_SUMMARY_LEVELS = {"010": "country", "040": "state", "050": "county", "310": "cbsa"}
# ACS "jam values" (e.g. -666666666) mark suppressed or unavailable estimates.
ACS_JAM_THRESHOLD = -1


@dataclass(frozen=True)
class AcsUrls:
    data: str
    geos: str


def acs_urls(base_url: str, year: int) -> AcsUrls:
    root = f"{base_url.rstrip('/')}/{year}/table-based-SF"
    return AcsUrls(
        data=f"{root}/data/1YRData/acsdt1y{year}-b19013.dat",
        geos=f"{root}/documentation/Geos{year}1YR.txt",
    )


# --------------------------------------------------------------------------- PMMS


def parse_pmms(path: Path) -> pl.DataFrame:
    frame = pl.read_csv(path, infer_schema=False, null_values=["", " "])
    required = {"date", "pmms30"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"PMMS file is missing columns {sorted(missing)}")

    def _float(column: str) -> pl.Expr:
        if column not in frame.columns:
            return pl.lit(None, pl.Float64).alias(column)
        return pl.col(column).str.strip_chars().cast(pl.Float64, strict=False)

    return (
        frame.select(
            pl.col("date").str.strip_chars().str.to_date("%m/%d/%Y").alias("week_date"),
            _float("pmms30").alias("rate_30y"),
            _float("pmms30p").alias("points_30y"),
            _float("pmms15").alias("rate_15y"),
        )
        .drop_nulls(["week_date", "rate_30y"])
        .sort("week_date")
    )


def validate_pmms(
    frame: pl.DataFrame, *, today: date | None = None, relaxed: bool = False
) -> ValidationReport:
    report = ValidationReport("pmms_weekly")
    today = today or date.today()
    report.stats["rows"] = frame.height
    if frame.height < (10 if relaxed else 2000):
        report.errors.append(f"only {frame.height} weekly rates (truncated file?)")
        return report
    if frame.get_column("week_date").n_unique() != frame.height:
        report.errors.append("duplicate week dates")
    bad = frame.filter((pl.col("rate_30y") <= 0) | (pl.col("rate_30y") > 25)).height
    if bad:
        report.errors.append(f"{bad} 30-year rates outside (0, 25]")
    latest = frame.get_column("week_date").max()
    report.stats["latest_week"] = latest
    if isinstance(latest, date) and (today - latest).days > 21:
        report.warnings.append(f"latest PMMS week {latest} is more than 21 days old")
    return report


# --------------------------------------------------------------------------- ACS


def _read_pipe(path: Path) -> pl.DataFrame:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return pl.read_csv(text.encode("utf-8"), separator="|", infer_schema=False, quote_char=None)


def parse_acs(data_path: Path, geos_path: Path, year: int) -> pl.DataFrame:
    estimates = _read_pipe(data_path)
    geos = _read_pipe(geos_path)
    for column in ("GEO_ID", "B19013_E001", "B19013_M001"):
        if column not in estimates.columns:
            raise ValueError(f"ACS {year} estimates file is missing {column}")
    for column in ("GEO_ID", "NAME", "SUMLEVEL", "COMPONENT", "STATE", "COUNTY", "CBSA"):
        if column not in geos.columns:
            raise ValueError(f"ACS {year} geography file is missing {column}")

    geos = geos.filter(
        (pl.col("COMPONENT") == "00") & pl.col("SUMLEVEL").is_in(list(ACS_SUMMARY_LEVELS))
    ).select(
        "GEO_ID",
        pl.col("NAME").alias("geo_name"),
        pl.col("SUMLEVEL").alias("summary_level"),
        pl.when(pl.col("STATE") != "").then(pl.col("STATE")).alias("state_fips"),
        pl.when(pl.col("COUNTY") != "").then(pl.col("COUNTY")).alias("county_fips"),
        pl.when(pl.col("CBSA") != "").then(pl.col("CBSA")).alias("cbsa_code"),
    )

    def _number(column: str) -> pl.Expr:
        value = pl.col(column).str.strip_chars().cast(pl.Float64, strict=False)
        return pl.when(value > ACS_JAM_THRESHOLD).then(value)

    joined = estimates.join(geos, on="GEO_ID", how="inner").select(
        pl.lit(year, pl.Int64).alias("acs_year"),
        pl.col("GEO_ID").alias("geo_id"),
        "summary_level",
        pl.col("summary_level").replace_strict(ACS_SUMMARY_LEVELS).alias("geo_level"),
        "geo_name",
        "state_fips",
        "county_fips",
        "cbsa_code",
        _number("B19013_E001").alias("median_household_income"),
        _number("B19013_M001").alias("margin_of_error"),
    )
    return joined.sort(["summary_level", "geo_id"])


def validate_acs(frame: pl.DataFrame, year: int, *, relaxed: bool = False) -> ValidationReport:
    report = ValidationReport(f"acs_{year}")
    counts = dict(frame.group_by("geo_level").len().iter_rows())
    report.stats.update({f"rows_{level}": n for level, n in counts.items()})
    if counts.get("country", 0) != 1:
        report.errors.append(f"expected exactly one national row, found {counts.get('country', 0)}")
    min_states, min_cbsas = (1, 1) if relaxed else (50, 300)
    if counts.get("state", 0) < min_states:
        report.errors.append(f"only {counts.get('state', 0)} states")
    if counts.get("cbsa", 0) < min_cbsas:
        report.errors.append(f"only {counts.get('cbsa', 0)} CBSAs")
    bad = frame.filter(
        pl.col("median_household_income").is_not_null()
        & ((pl.col("median_household_income") < 5_000) | (pl.col("median_household_income") > 500_000))
    ).height
    if bad:
        report.errors.append(f"{bad} implausible median household incomes")
    return report


# --------------------------------------------------------------------------- CBSA names

_SUFFIX = re.compile(r"\s+(Metro|Micro) Area$")


def cbsa_name_candidates(acs: pl.DataFrame) -> pl.DataFrame:
    """Generate the "<Principal city>, <ST>" strings Zillow uses to name metros.

    "Nashville-Davidson--Murfreesboro--Franklin, TN Metro Area" yields "Nashville, TN",
    "Nashville-Davidson, TN", ...; hyphenated cities such as Winston-Salem are covered by
    the multi-token candidates. dbt joins on these and falls back to a manual seed.
    """
    rows: list[dict[str, object]] = []
    cbsas = acs.filter(pl.col("geo_level") == "cbsa").select("acs_year", "cbsa_code", "geo_name").unique()
    for acs_year, code, name in cbsas.iter_rows():
        kind = "micro" if str(name).endswith("Micro Area") else "metro"
        title = _SUFFIX.sub("", str(name))
        if ", " not in title:
            continue
        cities, states = title.rsplit(", ", 1)
        state = states.split("-")[0].strip()
        tokens = [t for t in re.split(r"-+", cities) if t]
        # Prefixes of the principal-city list rank first (1..n); each later principal city on
        # its own ranks after them (101..), which catches renamed CBSAs such as
        # "The Villages, FL" -> "Wildwood-The Villages, FL".
        ranked = [("-".join(tokens[:n]), n) for n in range(1, len(tokens) + 1)]
        ranked += [(token, 100 + i) for i, token in enumerate(tokens[1:], start=1)]
        seen: set[str] = set()
        for text, rank in ranked:
            for variant in (text, text.split("/")[0]):
                candidate = f"{variant.strip()}, {state}"
                if candidate not in seen:
                    seen.add(candidate)
                    rows.append(
                        {
                            "acs_year": acs_year,
                            "cbsa_code": code,
                            "cbsa_title": title,
                            "metro_micro": kind,
                            "candidate_name": candidate,
                            "candidate_rank": rank,
                        }
                    )
    schema = {
        "acs_year": pl.Int64,
        "cbsa_code": pl.Utf8,
        "cbsa_title": pl.Utf8,
        "metro_micro": pl.Utf8,
        "candidate_name": pl.Utf8,
        "candidate_rank": pl.Int64,
    }
    return pl.DataFrame(rows, schema=schema).sort(["acs_year", "cbsa_code", "candidate_rank"])
