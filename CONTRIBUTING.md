# Contributing

How code is written and changed in Sentinel ZA. Most rules are checked automatically:
run `uv run poe check` before every commit.

## Setup

```powershell
uv sync                     # install the project and dev tools
uv run pre-commit install   # code checks and commit message check on every commit
uv run poe check            # everything CI runs
```

| Command | What it does |
|---|---|
| `uv run poe format` | Format code and apply safe lint fixes |
| `uv run poe lint` | Lint and check formatting |
| `uv run poe typecheck` | Strict type checking (mypy) |
| `uv run poe arch` | Architecture boundary checks (import-linter) |
| `uv run poe test` | All tests, with the 85% coverage gate |
| `uv run poe test-unit` | Fast unit tests only |
| `uv run poe check` | All of the above, in CI order |

## Workflow

1. Create a branch for the task. Never commit to `main` (a hook blocks it).
2. Make small commits.
3. Open a pull request. CI must pass.
4. Squash-merge and delete the branch.

## Branch names

`<type>/sz-<task number>-<two to four words>`, lowercase, words joined by hyphens.
Leave out the task number for small work that has no task.

```text
feat/sz-5-reference-data
fix/sz-13-duplicate-offsets
docs/sz-4-data-model
chore/code-style
```

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/) format, checked by a hook.

- Subject: `<type>: <what changed>`, lowercase, imperative, no full stop, 50 characters or less.
- Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`, `perf`.
- Body only when the reason isn't obvious: one or two plain sentences on *why*.
- No bullet-point essays, emojis or sign-off lines.

```text
feat: generate customers and accounts
fix: reject naive datetimes in ensure_utc
refactor: split merchant generation into its own module
```

Pull request titles follow the same format. Descriptions say what changed, why, and how it was tested, in a few sentences.

## Naming

- Use plain business words: `customer`, `account`, `amount_cents`, `fraud_type`.
- Functions start with a verb: `generate_customers`, `to_cents`, `load_settings`.
- Booleans read as yes/no questions: `is_fraud`, `has_card`.
- Collections are plural nouns: `customers`, not `customer_list`.
- No vague names: avoid `data`, `info`, `item`, `temp`, `result2`, and avoid `Manager`, `Handler`,
  `Helper`, `Util` and `Processor` in class or module names.
- No abbreviations except well-known ones (`id`, `db`, `url`, `sa`).
- Constants are `UPPER_SNAKE_CASE`, classes `PascalCase`, everything else `snake_case`.

## Code structure

- One module does one job. If you can't describe it in one short sentence, split it.
- Keep functions short. Complexity is capped at 10 by the linter; most functions should be far below that.
- Return early instead of nesting `if` blocks.
- Prefer plain functions. Add a class only when it holds state or represents a real thing.
- Don't add abstraction (base classes, interfaces, factories) until there is a second real use.
  The exception is the I/O boundaries already decided in [docs/architecture.md](docs/architecture.md#key-decisions) (broker, storage).
- No `utils.py` or `helpers.py` grab-bags. Put code next to what uses it.
- Follow the layers in [docs/architecture.md](docs/architecture.md); `uv run poe arch` enforces them.

## Comments and docstrings

- Every public module, class and function has a one-line docstring saying what it does.
- Add more only when something isn't obvious from the name and types.
- Inline comments explain *why*, not *what*. Keep them short.

```python
# Good: explains a decision the code can't show
SAST = timezone(timedelta(hours=2))  # SA has no daylight saving

# Bad: repeats the code
total = price * quantity  # multiply price by quantity
```

- No commented-out code (the linter blocks it). Git keeps history.

## Shared rules

| Topic | Rule |
|---|---|
| Config | Read settings with `get_settings()`. Never read `os.environ` directly or hard-code paths and thresholds. |
| Money | Integer cents (`amount_cents`). Convert with `sentinel.core.money`. Never use `float`. |
| Time | Timezone-aware UTC (`utc_now()`). Convert to SAST only for display. |
| Errors | Raise `SentinelError` subclasses with a message that says what went wrong. Never swallow exceptions. |
| Logging | `logging.getLogger(__name__)`, extra fields via `extra={...}`. No `print`. Never log personal data. |
| Types | Every function fully typed (mypy strict). |
| Data | Validate with Pydantic where data enters the system; trust it after that. |

## Tests

- `tests/unit/` mirrors `src/sentinel/`: fast, no real databases or network.
- `tests/integration/` covers real I/O and is marked `integration` automatically.
- One behaviour per test, named after it: `test_to_cents_rejects_floats_and_bools`.
- A bug fix starts with a test that reproduces the bug.
- Coverage stays at 85% or above.

## Decisions

Significant design decisions are recorded under [Key decisions](docs/architecture.md#key-decisions).
If a change goes against one, update that entry in the same pull request.
