# 0002. Use synthetic data with separate ground truth

- **Status:** Accepted
- **Date:** 2026-10-08

## Context

Real bank transaction data is unavailable and protected by POPIA. Public fraud datasets are
anonymised, not South African, and lack the event detail (devices, SIM swaps, beneficiaries)
needed for realistic detection.

## Decision

Generate synthetic South African bank data with a seeded simulator that injects fraud scenarios
calibrated to SABRIC 2025 statistics. Fraud labels are written to separate `truth` tables that the
scoring service never reads. Labels carry a `reported_at` time so models only train on fraud the bank
would already know about.

## Alternatives considered

- **Kaggle credit-card dataset:** anonymised PCA features, no South African patterns, no streaming events.
- **Labels inside the transaction table:** easy to leak into features by accident.

## Consequences

Detection can be measured per scenario because ground truth is known. Results are only as realistic
as the simulator, so its assumptions are documented (task SZ-3) and tunable.
