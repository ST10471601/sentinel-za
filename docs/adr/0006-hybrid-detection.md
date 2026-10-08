# 0006. Detect fraud with rules, machine learning and graph analysis together

- **Status:** Accepted
- **Date:** 2026-10-08

## Context

SABRIC 2025 shows most fraud is social engineering through the victim's own device, losses are
concentrated in a few high-value cases, and stolen funds move quickly through mule accounts. No single
technique covers all of these.

## Decision

Score each transaction with three layers: a YAML-configured rules engine (fast, explainable), a
supervised LightGBM model with SHAP explanations, and graph features from the money-flow network to expose
mule rings. A scoring service combines them into approve, review or block.

## Alternatives considered

- **Rules only:** brittle, misses novel patterns.
- **ML only:** harder to explain to analysts; slow to react to a newly reported pattern.

## Consequences

Each layer can be evaluated separately and per scenario. The combination logic must be documented and
tested, and features must come from one shared module to avoid train/serve skew.
