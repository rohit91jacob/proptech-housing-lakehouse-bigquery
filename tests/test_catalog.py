from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from proptech import dbt_sources, fixtures
from proptech.catalog import Catalog, Kind, load_catalog
from proptech.settings import REPO_ROOT

from .conftest import FIXTURES


def test_catalog_keys_are_unique_and_profiles_nest(catalog: Catalog) -> None:
    keys = [d.key for d in catalog.datasets]
    assert len(keys) == len(set(keys))
    core = {d.key for d in catalog.for_profile("core")}
    extended = {d.key for d in catalog.for_profile("extended")}
    assert core < extended, "every core dataset must also be in the extended profile"
    assert any(d.kind is Kind.FORECAST for d in catalog.datasets)


def test_unknown_dataset_selection_is_rejected(catalog: Catalog) -> None:
    with pytest.raises(KeyError, match="unknown dataset keys"):
        catalog.for_profile("core", only=["no_such_dataset"])


def test_invalid_catalog_entries_fail_fast(tmp_path: Path) -> None:
    bad = {
        "datasets": [
            {
                "key": "Bad-Key",
                "family": "zhvi",
                "metric": "home_value",
                "path": "x.csv",
                "geography": "metro",
                "unit": "usd",
                "min_regions": 1,
                "value_range": [0, 1],
                "description": "x",
            }
        ]
    }
    path = tmp_path / "datasets.yml"
    path.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="must match"):
        load_catalog(path)


def test_generated_dbt_artifacts_are_up_to_date(catalog: Catalog) -> None:
    stale = dbt_sources.sync(catalog, REPO_ROOT / "dbt", check=True)
    assert stale == [], "run `proptech dbt-sources` and commit the result"


def test_committed_fixtures_match_generator(catalog: Catalog, tmp_path: Path) -> None:
    generated = fixtures.generate(catalog, tmp_path)
    assert generated
    for path in generated:
        committed = FIXTURES / path.relative_to(tmp_path)
        assert committed.exists(), f"missing committed fixture {committed}"
        assert committed.read_bytes() == path.read_bytes(), (
            f"stale fixture {committed}; run `proptech fixtures`"
        )


def test_every_core_dataset_has_a_fixture(catalog: Catalog) -> None:
    for dataset in catalog.for_profile("core"):
        assert (FIXTURES / "zillow" / dataset.path).is_file(), dataset.key
