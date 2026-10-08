from __future__ import annotations

import shutil
from pathlib import Path

import duckdb
import pytest

from proptech import budget
from proptech.manifest import Status
from proptech.pipeline import run_ingest
from proptech.settings import Settings

from .conftest import FIXTURES


def _count(settings: Settings, sql: str) -> int:
    with duckdb.connect(str(settings.duckdb_path), read_only=True) as con:
        return con.execute(sql).fetchone()[0]


def test_full_ingest_then_incremental_rerun(fixture_settings: Settings, catalog) -> None:
    first = run_ingest(fixture_settings)
    assert not first.failed, [(e.dataset_key, e.error) for e in first.failed]
    assert {e.status for e in first.entries} == {Status.LOADED}
    assert len(first.entries) == len(catalog.for_profile("core")) + 2  # + PMMS + ACS
    assert first.logical_bytes_written > 0

    loaded = _count(fixture_settings, "select count(*) from raw_zillow.zhvi_metro_mid_all")
    assert loaded == 6 * 200  # 6 regions x 200 months (2010-01..2026-08)
    assert _count(fixture_settings, "select count(*) from raw_zillow.zhvf_metro_mid_all__history") == 18

    second = run_ingest(fixture_settings)
    assert {e.status for e in second.entries} == {Status.UNCHANGED}
    assert second.logical_bytes_written == 0
    # Nothing was reloaded: forecast history still holds the single vintage.
    assert _count(fixture_settings, "select count(*) from raw_zillow.zhvf_metro_mid_all__history") == 18
    assert _count(fixture_settings, "select count(*) from ops.ingestion_manifest") == 2 * len(first.entries)

    forced = run_ingest(fixture_settings, only=["zhvi_state_mid_all"], force=True, include_reference=False)
    assert [e.status for e in forced.entries] == [Status.LOADED]


def test_dry_run_writes_nothing(fixture_settings: Settings) -> None:
    summary = run_ingest(fixture_settings, only=["zhvi_metro_mid_all"], dry_run=True, include_reference=False)
    assert [e.status for e in summary.entries] == [Status.DRY_RUN]
    with duckdb.connect(str(fixture_settings.duckdb_path), read_only=True) as con:
        tables = con.execute(
            "select count(*) from information_schema.tables where table_schema = 'raw_zillow'"
        ).fetchone()
    assert tables[0] == 0


def test_bad_file_fails_alone_and_keeps_previous_vintage(fixture_settings: Settings, tmp_path: Path) -> None:
    assert not run_ingest(fixture_settings, include_reference=False).failed
    before = _count(fixture_settings, "select count(*) from raw_zillow.inventory_metro")

    broken_root = tmp_path / "zillow"
    shutil.copytree(FIXTURES / "zillow", broken_root)
    target = broken_root / "invt_fs" / "Metro_invt_fs_uc_sfrcondo_sm_month.csv"
    lines = target.read_text(encoding="utf-8").splitlines()
    target.write_text(
        "\n".join([lines[0].replace("RegionType", "Kind"), *lines[1:]]) + "\n", encoding="utf-8"
    )

    settings = fixture_settings.model_copy(update={"zillow_base_url": broken_root.as_uri()})
    summary = run_ingest(settings, include_reference=False)
    failed = {e.dataset_key: e.error for e in summary.failed}
    assert list(failed) == ["inventory_metro"]
    assert "missing required columns" in failed["inventory_metro"]
    assert _count(fixture_settings, "select count(*) from raw_zillow.inventory_metro") == before


def test_quarantined_values_are_recorded(fixture_settings: Settings, tmp_path: Path) -> None:
    root = tmp_path / "zillow"
    shutil.copytree(FIXTURES / "zillow", root)
    target = root / "market_temp_index" / "Metro_market_temp_index_uc_sfrcondo_month.csv"
    lines = target.read_text(encoding="utf-8").splitlines()
    cells = lines[1].split(",")
    cells[-1] = "5000"  # one impossible heat-index value
    lines[1] = ",".join(cells)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    settings = fixture_settings.model_copy(
        update={"zillow_base_url": root.as_uri(), "max_reject_ratio": 0.01}
    )

    summary = run_ingest(settings, only=["market_heat_index_metro"], include_reference=False)
    assert [e.status for e in summary.entries] == [Status.LOADED]
    assert _count(settings, "select count(*) from ops.rejected_values") == 1
    assert _count(settings, "select count(*) from raw_zillow.market_heat_index_metro where value > 1000") == 0


def test_budget_guard_blocks_loads_in_sandbox(
    fixture_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from proptech import pipeline

    original_init = pipeline.Ingestor.__init__

    def init(self, *args, **kwargs):  # pretend we're on the BigQuery sandbox
        original_init(self, *args, **kwargs)
        self.enforce_budget = True

    monkeypatch.setattr(pipeline.Ingestor, "__init__", init)
    tiny = fixture_settings.model_copy(update={"storage_budget_gib": 1e-9})
    summary = run_ingest(tiny, only=["zhvi_metro_mid_all"], include_reference=False)
    assert summary.failed and "storage budget exceeded" in (summary.failed[0].error or "")


def test_budget_status_math() -> None:
    state = budget.BudgetStatus(used_bytes=3 * budget.GIB, budget_bytes=4 * budget.GIB)
    assert state.allows(budget.GIB)
    assert not state.allows(budget.GIB + 1)
    assert state.used_fraction == pytest.approx(0.75)
    assert "75.0%" in state.describe()
