# 0004. Put the event broker behind an interface

- **Status:** Accepted
- **Date:** 2026-10-08

## Context

Banks stream transactions through brokers such as Kafka, and Kafka experience is valuable for the target
role. Running Kafka locally is not possible on the available hardware (ADR 0003).

## Decision

Define a broker interface (publish, consume, commit offset, consumer groups) in `sentinel.broker`.
Implement it twice: a local event log on SQLite (WAL mode) for development, and a Kafka adapter. The same
contract tests run against both; Kafka's run in GitHub Actions.

## Alternatives considered

- **Kafka only:** cannot run locally.
- **In-memory queue only:** no offsets, no replay, no realistic semantics, and no Kafka evidence.
- **Redpanda locally:** lighter than Kafka but still too heavy alongside the rest of the stack.

## Consequences

Producers and consumers are written once against the interface. The local log must faithfully mimic
Kafka semantics (ordering per key, offsets, at-least-once delivery), which the contract tests verify.
