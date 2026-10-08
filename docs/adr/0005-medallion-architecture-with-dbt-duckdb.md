# 0005. Use a medallion architecture with dbt and DuckDB

- **Status:** Accepted
- **Date:** 2026-10-08

## Context

Raw events need to be kept for replay and audit, cleaned for analysis, and shaped for dashboards and
model training. These are different jobs with different guarantees.

## Decision

Store data in three layers: bronze (raw events as Parquet, append-only, partitioned by date), silver
(typed, deduplicated, validated, PII-masked) and gold (star schema and marts). Transform silver to gold
with dbt-core using the dbt-duckdb adapter, with dbt tests on every model.

## Alternatives considered

- **Hand-written SQL scripts:** no lineage, no tests, no dependency ordering.
- **dbt on Postgres:** needs a running server (ADR 0003).

## Consequences

Any layer can be rebuilt from the one below it. dbt provides lineage documentation and data tests.
DuckDB is single-writer, so batch transformations and live writes must not target the same file.
