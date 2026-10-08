"""Typed view over ``config/datasets.yml``."""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,62}$")


class Kind(StrEnum):
    TIMESERIES = "timeseries"
    FORECAST = "forecast"


class Geography(StrEnum):
    COUNTRY = "country"
    STATE = "state"
    METRO = "metro"
    COUNTY = "county"
    CITY = "city"
    ZIP = "zip"
    NEIGHBORHOOD = "neighborhood"


# RegionType values Zillow uses inside each file, keyed by the file's geography.
REGION_TYPES: dict[Geography, frozenset[str]] = {
    Geography.COUNTRY: frozenset({"country"}),
    Geography.STATE: frozenset({"state"}),
    Geography.METRO: frozenset({"msa", "country"}),
    Geography.COUNTY: frozenset({"county"}),
    Geography.CITY: frozenset({"city"}),
    Geography.ZIP: frozenset({"zip"}),
    Geography.NEIGHBORHOOD: frozenset({"neighborhood"}),
}

# Metadata columns that must be present in every Zillow file of a geography.
REQUIRED_COLUMNS: dict[Geography, tuple[str, ...]] = {
    Geography.COUNTRY: ("RegionID", "SizeRank", "RegionName", "RegionType"),
    Geography.STATE: ("RegionID", "SizeRank", "RegionName", "RegionType"),
    Geography.METRO: ("RegionID", "SizeRank", "RegionName", "RegionType", "StateName"),
    Geography.COUNTY: (
        "RegionID",
        "SizeRank",
        "RegionName",
        "RegionType",
        "StateName",
        "State",
        "Metro",
        "StateCodeFIPS",
        "MunicipalCodeFIPS",
    ),
    Geography.CITY: ("RegionID", "SizeRank", "RegionName", "RegionType", "StateName", "State"),
    Geography.ZIP: ("RegionID", "SizeRank", "RegionName", "RegionType", "StateName", "State"),
    Geography.NEIGHBORHOOD: ("RegionID", "SizeRank", "RegionName", "RegionType", "StateName"),
}


class Dataset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    family: str
    metric: str
    path: str
    geography: Geography
    unit: str
    kind: Kind = Kind.TIMESERIES
    tier: str | None = None
    property_type: str | None = None
    bedrooms: int | None = None
    smoothed: bool = True
    seasonally_adjusted: bool = False
    profiles: tuple[str, ...] = ("core", "extended")
    min_regions: int = Field(ge=1)
    value_range: tuple[float, float]
    description: str

    @field_validator("key")
    @classmethod
    def _key_is_identifier(cls, value: str) -> str:
        if not KEY_PATTERN.match(value):
            raise ValueError(f"dataset key {value!r} must match {KEY_PATTERN.pattern}")
        return value

    @model_validator(mode="after")
    def _range_is_ordered(self) -> Dataset:
        low, high = self.value_range
        if low >= high:
            raise ValueError(f"{self.key}: value_range lower bound must be < upper bound")
        return self

    @property
    def raw_table(self) -> str:
        return self.key

    @property
    def regions_table(self) -> str:
        return f"{self.key}__regions"


class Catalog(BaseModel):
    model_config = ConfigDict(frozen=True)

    datasets: tuple[Dataset, ...]

    @model_validator(mode="after")
    def _keys_unique(self) -> Catalog:
        keys = [d.key for d in self.datasets]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            raise ValueError(f"duplicate dataset keys: {dupes}")
        return self

    def for_profile(self, profile: str, only: list[str] | None = None) -> list[Dataset]:
        selected = [d for d in self.datasets if profile in d.profiles]
        if only:
            unknown = sorted(set(only) - {d.key for d in self.datasets})
            if unknown:
                raise KeyError(f"unknown dataset keys: {unknown}")
            selected = [d for d in self.datasets if d.key in only]
        return selected

    def get(self, key: str) -> Dataset:
        for dataset in self.datasets:
            if dataset.key == key:
                return dataset
        raise KeyError(key)


def load_catalog(path: Path) -> Catalog:
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    defaults: dict[str, Any] = raw.get("defaults", {})
    merged = [{**defaults, **entry} for entry in raw["datasets"]]
    return Catalog(datasets=tuple(Dataset(**entry) for entry in merged))
