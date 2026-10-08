# 0003. Run a lightweight local stack with no local servers

- **Status:** Accepted
- **Date:** 2026-10-08

## Context

The project must cost nothing and run on an 8 GB laptop with soldered RAM. WSL with Docker idle used
about 2.2 GB before any service started; Kafka, Postgres and Airflow together would not fit.

## Decision

Develop natively on Windows with plain Python processes. Locally: SQLite for operational data and the
event log, DuckDB with Parquet for analytics, in-process state for rolling features, MLflow with a file
store. Kafka and Postgres run only as service containers in GitHub Actions integration tests.

## Alternatives considered

- **Docker Compose with profiles:** still exceeded available memory on this hardware.
- **Cloud free tiers:** time-limited or requiring payment details; conflicts with the zero-cost rule.

## Consequences

The full demo runs locally in under 2 GB. Production-grade infrastructure is still exercised in CI.
Every storage and broker dependency must sit behind an interface (ADR 0004, ADR 0009) so local and CI
implementations stay interchangeable.
