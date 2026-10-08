"""Runtime configuration, read from environment variables (prefix ``PROPTECH_``) or ``.env``."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Target(StrEnum):
    """Which warehouse the loader writes to."""

    DUCKDB = "duckdb"
    BIGQUERY = "bigquery"


class Environment(StrEnum):
    """BigQuery environment flavour. ``sandbox`` = no billing account (no DML, 60-day expiry)."""

    SANDBOX = "sandbox"
    PROD = "prod"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PROPTECH_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    target: Target = Target.DUCKDB
    env: Environment = Environment.SANDBOX
    profile: str = "core"
    catalog_path: Path = REPO_ROOT / "config" / "datasets.yml"

    # Sources
    zillow_base_url: str = "https://files.zillowstatic.com/research/public_csvs"
    pmms_url: str = "https://www.freddiemac.com/pmms/docs/PMMS_history.csv"
    acs_base_url: str = "https://www2.census.gov/programs-surveys/acs/summary_file"
    acs_years: Annotated[list[int], NoDecode] = Field(default_factory=lambda: [2021, 2022, 2023, 2024])

    # DuckDB (local development and CI)
    duckdb_path: Path = REPO_ROOT / "data" / "warehouse" / "proptech.duckdb"

    # BigQuery
    bq_project: str | None = None
    bq_location: str = "US"
    bq_dataset_prefix: str = ""
    sandbox_table_ttl_days: int = Field(default=59, ge=1, le=60)

    # Raw archive of every downloaded vintage: a local directory or gs://bucket/prefix
    raw_archive_uri: str = str(REPO_ROOT / "data" / "archive")

    # Guardrails
    # Relax row-count floors (min_regions, ACS geography counts) for tiny synthetic fixtures.
    relaxed_validation: bool = False
    # Out-of-range values are quarantined (not loaded) when they are at most this share of a
    # file; above it the whole dataset fails. Zillow occasionally publishes isolated outliers.
    max_reject_ratio: float = Field(default=0.001, ge=0, le=1)
    storage_budget_gib: float = Field(default=8.0, gt=0)
    max_bytes_billed: int = 50 * 1024**3

    # HTTP
    http_timeout_seconds: float = 120.0
    http_retries: int = 5
    user_agent: str = (
        "proptech-housing/0.1 (+https://github.com/rohit91jacob/proptech-housing-lakehouse-bigquery)"
    )

    @field_validator("acs_years", mode="before")
    @classmethod
    def _split_years(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in value.replace(" ", "").split(",") if part]
        if isinstance(value, int):
            return [value]
        return value

    def dataset(self, layer: str) -> str:
        """Physical dataset (BigQuery) / schema (DuckDB) name for a logical layer."""
        return f"{self.bq_dataset_prefix}{layer}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
