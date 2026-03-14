# Codex Notes

## Repository map

- `app/main.py`: FastAPI entrypoint.
- `app/bot_runner.py`: bot worker entrypoint.
- `app/api.py`: HTTP API routes.
- `app/bot.py`: Telegram bot handlers and menu flows.
- `app/services.py`: service-layer operations.
- `app/repositories.py`: persistence access patterns.
- `app/db.py`: database setup.
- `app/config.py`: environment-driven settings.
- `app/ai.py`: AI generation integration and fallback behavior.
- `app/scheduler.py`: send scheduling logic.
- `tests/`: pytest coverage for focused behavior checks.
- `scripts/`: deployment helpers.
- `data/`: runtime artifacts such as SQLite DB, sessions, and logs.

## Main commands

Install:

```powershell
pip install -e .
```

API:

```powershell
uvicorn app.main:app --reload
```

Bot:

```powershell
python -m app.bot_runner
```

Tests:

```powershell
pytest
```

Single test file:

```powershell
pytest tests/test_services.py -v
```

Single test:

```powershell
pytest tests/test_services.py::test_name -v
```

## Practical guidance

- Start from the user-facing entrypoint nearest to the change: API in `app/api.py`, bot flow in `app/bot.py`, scheduling in `app/scheduler.py`, AI behavior in `app/ai.py`.
- Trace business logic through `app/services.py` and `app/repositories.py` before editing data flow.
- Prefer small, targeted edits. This repository is still compact enough that broad refactors are rarely justified unless the task directly requires them.
- Respect existing safety rules from `README.md`: no stealth automation and no removal of AI disclosure behavior.

## Validation expectations

- Doc-only changes: manual content review is enough.
- Small code changes: run the most relevant test file or test case.
- Cross-cutting changes: run targeted tests first and then `pytest`.
- If runtime behavior depends on external credentials or Telegram/OpenAI access, state that validation is limited locally instead of guessing.

## Docs to reuse

- `README.md`: product scope, commands, environment shape, deployment notes.
- `WORKFLOW.md`: agent workflow and issue execution context.
- `docs/superpowers/specs/`: design history for larger features.
- `docs/superpowers/plans/`: implementation plans for larger tasks.
