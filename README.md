# Sentinel ZA: Real-Time Bank Fraud Detection

[![CI](https://github.com/ST10471601/sentinel-za/actions/workflows/ci.yml/badge.svg)](https://github.com/ST10471601/sentinel-za/actions/workflows/ci.yml)

An end-to-end data platform that simulates a South African bank's transaction flow, injects
realistic fraud, and detects it in real time.

> **Status:** Phase 0 (foundations) complete. Phase 1 (simulator) in progress.

## The problem

South African banks face growing digital fraud: SIM-swap account takeovers, card-not-present
fraud, stolen cards, social-engineering scams, money-mule networks, and rapid drains over
instant payments. Fraud must be stopped in milliseconds without blocking legitimate customers.

## What this project does

- **Simulates** a bank: customers, accounts, cards, devices, merchants and ZAR transactions,
  with six labelled fraud scenarios.
- **Streams** transactions through a pluggable event broker (local event log; Kafka in CI).
- **Lands** data in medallion layers (bronze Parquet → silver/gold in DuckDB via dbt).
- **Scores** every transaction with a rules engine + ML model, with plain-English reasons.
- **Serves** an analyst console to review alerts and feed decisions back into retraining.

## Tech stack

Python 3.12 · uv · DuckDB · Parquet · dbt · Prefect · SQLite/Postgres · Kafka (CI) ·
LightGBM · SHAP · FastAPI · Streamlit · GitHub Actions

Designed to run on an 8 GB laptop: no local servers, heavy infrastructure tested in CI.

## Quickstart

```bash
uv sync                             # install
uv run sentinel version             # run the CLI
uv run sentinel simulate reference  # generate reference data in data/reference/
uv run sentinel simulate history    # plus 3 months of activity in data/history/
uv run poe check                    # lint, strict types, architecture rules, tests
```

## Engineering standards

Every change must pass `uv run poe check`, locally and in CI:

- **Ruff** linting and formatting, with a complexity cap and docstrings on public code
- **mypy** in strict mode
- **import-linter** enforcing the [layered architecture](docs/architecture.md)
- **pytest** with an 85% coverage gate

## Project layout

```
src/sentinel/
  cli.py       command-line interface and composition root
  dashboard/   analyst console
  scoring/     real-time scoring service
  ml/          training, evaluation, explainability
  rules/       YAML-configured rules engine
  features/    shared feature logic (training + live scoring)
  simulator/   synthetic SA bank data + fraud injectors
  ingestion/   consumers that land raw events in bronze
  broker/      event broker interface (local log, Kafka)
  domain/      business entities and events
  core/        config, logging, errors, money, time
analytics/     dbt project (silver → gold)
docs/          architecture, data model, decisions
tests/         unit/ and integration/
```

## Documentation

- [Architecture](docs/architecture.md): layers, the rules that keep them clean, and key decisions
- [Data model](docs/data-model.md): entities, ERD, data dictionary, medallion layers
- [Contributing](CONTRIBUTING.md): workflow, code conventions, testing rules

## Data & privacy

All data is synthetic. No real customer data is used (POPIA).

## Licence

MIT
