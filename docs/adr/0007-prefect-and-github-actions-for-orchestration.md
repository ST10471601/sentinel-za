# 0007. Orchestrate batch jobs with Prefect and GitHub Actions

- **Status:** Accepted
- **Date:** 2026-10-08

## Context

Batch jobs (dbt runs, data-quality reports, retraining) need scheduling, retries and logging. Apache
Airflow is the most common orchestrator in job descriptions but needs a scheduler, web server and
metadata database running continuously.

## Decision

Write batch workflows as Prefect flows, which run locally without a server. Schedule production-style
runs with GitHub Actions cron jobs.

## Alternatives considered

- **Apache Airflow:** too heavy for the hardware (ADR 0003).
- **Dagster:** capable, but its local web server and daemon still use more memory than needed.
- **Plain cron scripts:** no retries, no run history.

## Consequences

The concepts (tasks, dependencies, retries, schedules) map directly to Airflow DAGs, which the README
explains. Scheduled cloud runs depend on GitHub Actions availability.
