# 0009. Enforce a layered architecture and strict typing in CI

- **Status:** Accepted
- **Date:** 2026-10-08

## Context

The codebase must stay readable and safe to change: modifying one part must not silently break another.
Conventions that rely on memory erode over time.

## Decision

Organise code by feature in layers with a downward-only dependency rule, enforced by import-linter. Type
check with mypy in strict mode. Lint with an extended Ruff rule set, including a complexity cap of 10 and
docstrings on public code. Require 85% test coverage. One command, `uv run poe check`, runs all of this
locally and in CI, and `main` only accepts changes with green CI.

## Alternatives considered

- **Conventions documented but not enforced:** relies on discipline; breaks under time pressure.
- **Gradual typing:** cheaper now, expensive to retrofit later.

## Consequences

Some extra effort per change (type hints, docstrings, tests). In return, refactors are caught by the type
checker and tests, and accidental coupling between packages fails the build.
