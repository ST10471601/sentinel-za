# Architecture

Sentinel ZA is one Python package (`sentinel`) organised **by feature**, with a strict
**layered** dependency rule. The rules below are enforced automatically in CI by
[import-linter](https://import-linter.readthedocs.io/) (`uv run poe arch`), so they cannot
silently erode.

## Layers

Arrows point from a package to the packages it may import. Imports only flow **downwards**.

```mermaid
flowchart TB
    cli["cli<br>composition root"]
    dashboard["dashboard<br>analyst console"]
    scoring["scoring<br>decisions"]
    ml["ml<br>models"]
    rules["rules<br>rules engine"]
    features["features<br>shared feature logic"]
    simulator["simulator<br>synthetic bank"]
    ingestion["ingestion<br>bronze landing"]
    broker["broker<br>event streams"]
    domain["domain<br>entities and events"]
    core["core<br>config, logging, errors, money, time, checksums"]

    cli --> dashboard --> scoring
    scoring --> ml
    scoring --> rules
    ml --> features
    rules --> features
    features --> domain
    simulator --> broker
    ingestion --> broker
    broker --> domain --> core
```

| Layer | Responsibility | May import |
|---|---|---|
| `cli` | Parses commands and wires concrete implementations together | Everything |
| `dashboard` | Analyst console | `scoring` and below |
| `scoring` | Combines rules and models into approve / review / block | `ml`, `rules` and below |
| `ml`, `rules` | Models; YAML rules. Independent of each other | `features` and below |
| `features` | Feature logic shared by training and live scoring | `domain`, `core` |
| `simulator`, `ingestion` | Produce events; land events in bronze. Independent of each other | `broker` and below |
| `broker` | Event broker interface and implementations | `domain`, `core` |
| `domain` | Entities and events; pure data, no I/O | `core` |
| `core` | Config, logging, errors, money, time, checksums | Nothing in `sentinel` |

Two contracts are enforced:

1. **Layered architecture.** A lower layer never imports a higher one, and the pairs marked
   independent (`ml` / `rules`, `simulator` / `ingestion`) never import each other.
2. **Pure foundations.** `domain` and `core` never import I/O frameworks (Typer, SQLite,
   DuckDB, SQLAlchemy, Kafka, Streamlit), so business logic is testable without them.

## Design rules

**Depend on interfaces, not implementations.** Where a component talks to the outside
world (broker, databases, files), its package defines a `typing.Protocol` describing what
it needs, and concrete classes implement it. Code receives implementations as arguments
(dependency injection); only `cli` chooses which implementation to use. This is why the
local SQLite event log can be swapped for Kafka, or SQLite for Postgres, without touching
business logic.

**Each package has a small public surface.** Modules whose names start with `_` are
internal to their package. Other packages import only public modules.

**Validate at the boundaries.** Data entering the system (events, files, configuration) is
validated with Pydantic models once, at the edge. Code inside can then trust its inputs.

**One source of truth per concern.** Settings come only from `sentinel.core.config`,
money conversions only from `sentinel.core.money`, timestamps only from
`sentinel.core.datetimes`. Never re-implement these locally.

## Key decisions

The decisions that shape the system, and why. When a decision changes, update its entry in
the same pull request and say what it replaced.

### 1. Synthetic data, with fraud labels kept apart

- **Decision:** a seeded simulator generates South African bank data and injects fraud
  scenarios calibrated to SABRIC 2025. Fraud labels live in separate `truth` tables that
  scoring never reads, and carry a `reported_at` time so models only train on fraud the bank
  would already know about.
- **Why:** real bank data is unavailable and protected by POPIA. Public datasets (e.g. the
  Kaggle credit-card set) are anonymised, not South African, and lack devices, SIM swaps and
  beneficiaries.
- **Trade-off:** results are only as realistic as the simulator, so its assumptions are
  documented (SZ-3) and tunable.

### 2. Lightweight local stack, no local servers

- **Decision:** develop natively on Windows with plain Python processes. Locally: SQLite for
  operational data and the event log, DuckDB with Parquet for analytics, in-process state
  for rolling features, MLflow with a file store. Kafka and Postgres run only as service
  containers in GitHub Actions.
- **Why:** the project must cost nothing and run on an 8 GB laptop. WSL with idle Docker used
  about 2.2 GB before any service started. Cloud free tiers are time-limited or need payment
  details.
- **Trade-off:** every storage and broker dependency must sit behind an interface (3, 8) so
  local and CI implementations stay interchangeable.

### 3. Event broker behind an interface

- **Decision:** `sentinel.broker` defines one broker interface (publish, consume, commit
  offset, consumer groups) with two implementations: a SQLite event log (WAL mode) for local
  work and a Kafka adapter. The same contract tests run against both; Kafka's run in CI.
- **Why:** banks stream transactions through brokers such as Kafka, but Kafka (or Redpanda)
  can't run on this laptop. An in-memory queue has no offsets or replay.
- **Trade-off:** the local log must mimic Kafka's ordering per key, offsets and at-least-once
  delivery. The contract tests check this.

### 4. Medallion layers with dbt and DuckDB

- **Decision:** bronze holds raw events as append-only Parquet, partitioned by date. Silver is
  typed, deduplicated, validated and PII-masked. Gold holds the star schema and marts. dbt-core
  with the dbt-duckdb adapter builds silver to gold, with dbt tests on every model.
- **Why:** replay and audit, cleaning, and reporting are different jobs. Hand-written SQL has
  no lineage or tests; dbt on Postgres needs a running server.
- **Trade-off:** DuckDB allows one writer, so batch transforms and live writes must use
  different files.

### 5. Rules, machine learning and graph analysis together

- **Decision:** each transaction is scored by a YAML rules engine, a LightGBM model with SHAP
  explanations, and graph features from the money-flow network. A scoring service combines
  them into approve, review or block.
- **Why:** SABRIC 2025 shows most fraud is social engineering on the victim's own device,
  losses sit in a few high-value cases, and stolen money moves fast through mule accounts.
  Rules alone are brittle; ML alone is hard to explain and slow to react.
- **Trade-off:** the combination logic needs its own tests, and features must come from one
  shared module to avoid train/serve skew.

### 6. Prefect and GitHub Actions for batch jobs

- **Decision:** batch work (dbt runs, data-quality reports, retraining) is written as Prefect
  flows, which run without a server. GitHub Actions cron jobs schedule them.
- **Why:** Airflow needs a scheduler, web server and database running all the time; Dagster
  is lighter but still too heavy; plain cron has no retries or run history.
- **Trade-off:** scheduled runs depend on GitHub Actions. Flows map directly to Airflow DAG
  concepts, which the README explains.

### 7. Streamlit for the analyst console

- **Decision:** the console (alert queue, alert detail, customer timeline, case actions) is
  built in Streamlit and reads from the operational database.
- **Why:** the project's focus is data engineering. A React frontend would be weeks of work
  outside that focus.
- **Trade-off:** fine for a demo or internal tool, not for a multi-user production system.

### 8. Enforced layers and strict typing

- **Decision:** code follows the layers above, enforced by import-linter. mypy runs in strict
  mode, Ruff runs an extended rule set with a complexity cap of 10, and coverage must stay at
  85% or above. `uv run poe check` runs all of it locally and in CI.
- **Why:** rules that rely on memory erode under time pressure, and typing is expensive to add
  later.
- **Trade-off:** each change needs type hints, docstrings and tests. In return, refactors are
  caught by the type checker and accidental coupling fails the build.

## Related documents

- [Data model](data-model.md)
- [Contributing guide](../CONTRIBUTING.md)
