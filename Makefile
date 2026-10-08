# Developer entry points. Every target runs from the repo root with uv-managed tooling.
SHELL := /bin/bash
DBT := uv run dbt --no-use-colors
DBT_DIRS := --project-dir dbt --profiles-dir dbt
FIXTURES := $(CURDIR)/tests/fixtures/sources
FIXTURE_ENV := PROPTECH_ZILLOW_BASE_URL=file://$(FIXTURES)/zillow \
	PROPTECH_PMMS_URL=file://$(FIXTURES)/pmms/PMMS_history.csv \
	PROPTECH_ACS_BASE_URL=file://$(FIXTURES)/acs \
	PROPTECH_RELAXED_VALIDATION=true \
	PROPTECH_DUCKDB_PATH=data/warehouse/fixtures.duckdb \
	PROPTECH_RAW_ARCHIVE_URI=data/fixture-archive

.PHONY: help setup lint format test generate fixtures ingest ingest-fixtures dbt-deps dbt-build \
	dbt-build-fixtures compile-bigquery docs budget clean

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

setup: ## Install Python deps, dbt packages and git hooks
	uv sync --frozen
	$(DBT) deps $(DBT_DIRS)
	uv run pre-commit install

lint: ## Ruff, generated-file drift check, BigQuery compile + sqlfluff parse
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run proptech dbt-sources --check
	$(MAKE) compile-bigquery

format: ## Auto-format Python
	uv run ruff format src tests
	uv run ruff check --fix src tests

test: ## Unit + integration tests (DuckDB, fixtures, mocked BigQuery)
	uv run pytest --cov --cov-report=term-missing

generate: ## Regenerate dbt sources/macro/seed from config/datasets.yml
	uv run proptech dbt-sources

fixtures: ## Regenerate the synthetic source fixtures
	uv run proptech fixtures

ingest: ## Load real sources into the configured warehouse (default: local DuckDB)
	uv run proptech ingest

ingest-fixtures: ## Load the synthetic fixtures into data/warehouse/fixtures.duckdb
	$(FIXTURE_ENV) uv run proptech ingest

dbt-deps:
	$(DBT) deps $(DBT_DIRS)

dbt-build: ## dbt build against DBT_TARGET (default local DuckDB)
	$(DBT) build $(DBT_DIRS)

dbt-build-fixtures: ingest-fixtures ## Full fixture pipeline: ingest + dbt build on DuckDB
	PROPTECH_DUCKDB_PATH=data/warehouse/fixtures.duckdb $(DBT) build $(DBT_DIRS) --target local \
		--vars '{max_unmapped_peer_share: 0.25}'

compile-bigquery: ## Compile BigQuery SQL offline (prod + sandbox flavours) and parse it with sqlfluff
	@for flavour in prod sandbox; do \
		$(DBT) compile $(DBT_DIRS) --target offline --no-populate-cache \
			--target-path target-bq-$$flavour --vars "{deployment: $$flavour}" || exit 1; \
		uv run sqlfluff parse --dialect bigquery --templater raw --parse-statistics \
			dbt/target-bq-$$flavour/compiled/proptech/models > /dev/null || exit 1; \
	done

docs: ## Generate dbt docs (static single-page site) from the local DuckDB build
	$(DBT) docs generate $(DBT_DIRS) --static

budget: ## Show the storage ledger used to protect the sandbox quota
	uv run proptech budget

clean:
	rm -rf dbt/target dbt/target-bq-* dbt/logs .pytest_cache .ruff_cache .coverage
