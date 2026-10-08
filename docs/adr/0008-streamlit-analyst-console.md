# 0008. Build the analyst console with Streamlit

- **Status:** Accepted
- **Date:** 2026-10-08

## Context

Fraud analysts need an alert queue, alert detail with reasons, customer timelines and case actions. The
project's focus is data engineering, not frontend development.

## Decision

Build the analyst console in Streamlit, in Python, reading from the operational database.

## Alternatives considered

- **React frontend with an API:** stronger for full-stack roles, but weeks of frontend work outside the
  project's focus.

## Consequences

The console is quick to build and change. It suits a demo and internal tool, not a multi-user production
system; the README states this.
