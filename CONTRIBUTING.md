# Contributing

This guide defines how code is written and changed in Sentinel ZA. Most rules are enforced
automatically; run `uv run poe check` before every commit and it will tell you if anything is off.

## Setup

```powershell
uv sync                     # install the project and dev tools
uv run pre-commit install   # run fast checks on every commit
uv run poe check            # everything CI runs
```

| Command | What it does |
|---|---|
| `uv run poe format` | Auto-format and apply safe lint fixes |
| `uv run poe lint` | Lint and verify formatting |
| `uv run poe typecheck` | Strict static type checking (mypy) |
| `uv run poe arch` | Architecture boundary checks (import-linter) |
| `uv run poe test` | All tests with the 85% coverage gate |
| `uv run poe test-unit` | Fast unit tests only |
| `uv run poe check` | All of the above, in CI order |

## Workflow

1. **One branch per task**, named after the Notion task:
   `feat/sz-5-reference-data`, `fix/sz-13-duplicate-offsets`, `docs/sz-4-data-model`.
2. **Small commits** using [Conventional Commits](https://www.conventionalcommits.org/):
   `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`, `ci:`. Imperative mood:
   `feat: generate SA reference data`, not `added stuff`.
3. **Open a pull request** into `main` and fill in the template. CI must pass before merging.
4. **Squash-merge**, then delete the branch. `main` always passes `poe check`.

## Code conventions

**Readability first.** Code is read far more often than it is written. Prefer the obvious
solution; clever code needs a comment explaining why it is clever.

| Topic | Rule |
|---|---|
| Names | `snake_case` functions and variables, `PascalCase` classes, `UPPER_SNAKE_CASE` constants. Names say what a thing *is* or *does*; no abbreviations except standard ones (`id`, `url`, `db`). |
| Functions | Do one thing. Complexity is capped at 10; past that, split it. Prefer keyword arguments for anything non-obvious. |
| Types | Every function is fully annotated (mypy strict). No `Any` without a comment saying why. |
| Docstrings | Every public module, class and function, Google style. Say *what* and *why*; the code shows *how*. |
| Imports | Absolute only (`from sentinel.core.money import to_cents`). Respect the layers in [docs/architecture.md](docs/architecture.md). |
| Config | Read settings via `sentinel.core.config.get_settings()`. Never read `os.environ` or hard-code paths, URLs or thresholds. |
| Money | Integer cents everywhere (`amount_cents`). Convert with `sentinel.core.money`. Never use `float` for money. |
| Time | Timezone-aware UTC datetimes only (`sentinel.core.datetimes.utc_now()`). Convert to SAST only for display. |
| Errors | Raise subclasses of `sentinel.core.errors.SentinelError` with a message that explains how to fix the problem. Never swallow exceptions silently. |
| Logging | `logger = logging.getLogger(__name__)`; structured fields via `extra={...}`. Never `print`. Never log PII. |
| Data contracts | Validate data with Pydantic at system boundaries; trust it inside. |
| Dependencies | Inject collaborators as arguments; only `sentinel.cli` constructs concrete implementations. |

## Tests

- `tests/unit/` mirrors `src/sentinel/`: fast, no network, no real databases.
- `tests/integration/` exercises real I/O (files, SQLite, DuckDB; Kafka and Postgres in CI). Tests
  there are marked `integration` automatically.
- One behaviour per test, named after it: `test_to_cents_rejects_floats_and_bools`.
- Every bug fix starts with a failing test that reproduces the bug.
- Coverage must stay at or above 85%; untested code is unfinished code.

## Decisions

Significant design decisions are recorded as ADRs in [docs/adr/](docs/adr/README.md). If a change
contradicts an accepted ADR, write a new ADR that supersedes it in the same pull request.
