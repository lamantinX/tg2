# AGENTS.md

## Project

`tg2` is a Python 3.11 MVP for a Telegram-controlled service that manages authorized Telegram user sessions and posts AI-generated messages with explicit disclosure.

Core code lives in `app/`, tests live in `tests/`, deployment scripts live in `scripts/`, and longer project docs live in `README.md`, `WORKFLOW.md`, and `docs/`.

## Local setup

Use the repository root as the working directory.

Install dependencies:

```powershell
pip install -e .
```

Run the API:

```powershell
uvicorn app.main:app --reload
```

Run the Telegram bot worker:

```powershell
python -m app.bot_runner
```

Run tests:

```powershell
pytest
```

## Working rules

- Prefer targeted tests for the changed area before running the full suite.
- Do not print or copy secrets from `.env`, `.env.symphony`, or any other local credential file.
- Preserve the project's safety constraint: generated content must remain explicitly disclosed and automation must not be hidden or deceptive.
- Keep changes local to this repository unless the user explicitly asks for external tooling or infra changes.
- Follow existing repository patterns before introducing new structure.

## Validation

- For doc-only changes, check formatting and link targets manually.
- For code changes, run the smallest relevant test set first, then broaden validation if risk increases.
- Summarize what was verified and what was not.

## Reference

Read `docs/codex.md` for the repository map, important files, and practical guidance for common tasks.
