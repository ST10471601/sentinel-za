# 0001. Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-10-08

## Context

Decisions made early (storage, broker, orchestration) shape everything after them. Without a
written record, the reasoning is lost and later changes risk undoing a decision for the wrong reasons.

## Decision

Record every significant architectural decision as an ADR in `docs/adr/`, using the template.
ADRs are immutable once accepted; a changed decision gets a new ADR that supersedes the old one.

## Alternatives considered

- **Decisions only in Notion:** not versioned with the code and invisible to reviewers of the repo.

## Consequences

Reviewers and future contributors can see why the system looks the way it does. Writing an ADR
takes a few minutes per decision.
