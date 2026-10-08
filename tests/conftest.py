from __future__ import annotations

from pathlib import Path

import pytest

from proptech.catalog import Catalog, load_catalog
from proptech.settings import REPO_ROOT, Settings, Target

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "sources"


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    return load_catalog(REPO_ROOT / "config" / "datasets.yml")


@pytest.fixture
def fixture_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings that point every source at the committed synthetic fixtures."""
    for key in list(__import__("os").environ):
        if key.startswith("PROPTECH_"):
            monkeypatch.delenv(key, raising=False)
    return Settings(
        target=Target.DUCKDB,
        duckdb_path=tmp_path / "warehouse.duckdb",
        raw_archive_uri=str(tmp_path / "archive"),
        zillow_base_url=(FIXTURES / "zillow").as_uri(),
        pmms_url=(FIXTURES / "pmms" / "PMMS_history.csv").as_uri(),
        acs_base_url=(FIXTURES / "acs").as_uri(),
        relaxed_validation=True,
        _env_file=None,
    )
