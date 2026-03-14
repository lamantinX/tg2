# Codex Local Config Design

## Goal

Add a minimal local Codex configuration for the `tg2` repository so an agent can start work with the right commands, safety constraints, and project context without introducing orchestration-specific setup.

## Scope

- Add a root `AGENTS.md` as the primary entry point for Codex in this repository.
- Add `docs/codex.md` as a compact reference for commands, architecture landmarks, and validation expectations.
- Reuse existing project docs instead of duplicating long instructions from `README.md` and `WORKFLOW.md`.

## Approach Options

### Option 1: Single-file config only

Put everything into `AGENTS.md`.

Pros:
- Smallest file count.

Cons:
- Quickly becomes a mixed document with rules, commands, and architecture details.
- Harder to keep concise over time.

### Option 2: Minimal split config

Use `AGENTS.md` for high-signal operating rules and `docs/codex.md` for repository-specific reference material.

Pros:
- Keeps the entry point short.
- Leaves room to extend project guidance without bloating `AGENTS.md`.
- Fits the current repository, which already keeps process docs under `docs/`.

Cons:
- Adds one extra file.

### Option 3: Multi-file handbook

Split commands, safety, and architecture into multiple Codex-only docs.

Pros:
- Maximum separation of concerns.

Cons:
- Overkill for this repository and for the user's stated minimum setup.

## Decision

Use Option 2.

## File Design

### `AGENTS.md`

Include:
- Project summary
- Where the main code lives
- Standard local commands
- Editing and validation expectations
- Safety and secret-handling rules
- Pointer to `docs/codex.md`

Keep it short and operational.

### `docs/codex.md`

Include:
- Repository map
- Typical entrypoints
- Test strategy for small vs broader changes
- Important runtime files and directories
- References to `README.md` and `WORKFLOW.md`

Keep it as a repo cheat sheet, not a second README.

## Non-Goals

- No Symphony or external orchestration setup
- No CI changes
- No changes to runtime code
- No duplication of secrets or environment values
