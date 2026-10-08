# Sentinel ZA — Real-Time Bank Fraud Detection

[![CI](https://github.com/ST10471601/sentinel-za/actions/workflows/ci.yml/badge.svg)](https://github.com/ST10471601/sentinel-za/actions/workflows/ci.yml)

An end-to-end data platform that simulates a South African bank's transaction flow, injects
realistic fraud, and detects it in real time.

> **Status:** 🚧 Phase 0 — foundations. Built in public.

## The problem

South African banks face growing digital fraud: SIM-swap account takeovers, card-not-present
fraud, cloned cards, social-engineering scams, money-mule networks, and rapid drains over
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
uv sync
uv run sentinel version
uv run pytest
```

## Project layout

```
src/sentinel/
  simulator/   synthetic SA bank data + fraud injectors
  broker/      event broker interface (local log, Kafka)
  ingestion/   consumers that land raw events in bronze
  features/    shared feature logic (training + live scoring)
  rules/       YAML-configured rules engine
  ml/          training, evaluation, explainability
  scoring/     real-time scoring service
  dashboard/   analyst console
analytics/     dbt project (silver → gold)
docs/          architecture, data model, decisions
tests/
```

## Documentation

- [Data model](docs/data-model.md): entities, ERD, data dictionary, medallion layers

## Data & privacy

All data is synthetic. No real customer data is used (POPIA).

## Licence

MIT
