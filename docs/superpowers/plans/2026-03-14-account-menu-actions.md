# Account Menu Actions Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add account list buttons with name and phone, per-account manual check, and cascading account deletion in the Telegram bot menu.

**Architecture:** Keep menu rendering in `app/bot.py` and move account-specific behavior into `AccountService` and repositories. Reuse the existing audit logic for a new single-account check path and keep destructive deletion logic centralized in the service layer.

**Tech Stack:** Python, aiogram, SQLAlchemy async ORM, Telethon, unittest

---

## Chunk 1: Presentation Helpers And Bot Rendering

### Task 1: Add failing presentation tests for account menu text

**Files:**
- Modify: `tests/test_bot.py`
- Test: `tests/test_bot.py`

- [ ] **Step 1: Write the failing tests**

Add tests covering:
- account list button label format `Name | +phone`
- account detail text contains both name and phone
- account detail text renders a missing character safely

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bot.py -q`
Expected: FAIL because account menu helpers do not exist yet.

- [ ] **Step 3: Write minimal implementation**

Modify `app/bot.py` to add:
- account label helper
- account details formatter
- account list keyboard builder
- account details keyboard builder

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_bot.py -q`
Expected: PASS for the new presentation tests.

## Chunk 2: Account Service And Repository Behavior

### Task 2: Add failing service tests for manual check and cascading deletion

**Files:**
- Modify: `tests/test_services.py`
- Test: `tests/test_services.py`

- [ ] **Step 1: Write the failing tests**

Add tests covering:
- single-account manual check resumes auto-paused bindings for a healthy account
- single-account manual check pauses bindings for a revoked account
- single-account manual check syncs account name when Telegram returns one
- account deletion removes bindings and the account row
- account deletion tolerates a missing session file

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_services.py -q`
Expected: FAIL because the new service and repository methods do not exist yet.

- [ ] **Step 3: Write minimal implementation**

Modify:
- `app/repositories.py` to add account deletion support
- `app/services.py` to add single-account sync, single-account check, and cascading deletion

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_services.py -q`
Expected: PASS for the new service tests.

## Chunk 3: Wire Menu Flow To The New Service Logic

### Task 3: Connect account callbacks and command handlers

**Files:**
- Modify: `app/bot.py`
- Test: `tests/test_bot.py`

- [ ] **Step 1: Add callback coverage if needed**

Add or extend focused tests for helper output used by the account list and detail screens.

- [ ] **Step 2: Run targeted tests before implementation**

Run: `python -m pytest tests/test_bot.py -q`
Expected: PASS or FAIL only for the not-yet-wired behavior you are adding in this task.

- [ ] **Step 3: Write minimal implementation**

Modify `app/bot.py` so that:
- `/accounts` and `menu:accounts` render the same account list screen
- selecting an account opens the account detail card
- manual check refreshes the chosen account and redraws the detail card
- deletion removes the account and returns to the refreshed account list
- missing names are synced before rendering the list

- [ ] **Step 4: Run targeted tests**

Run: `python -m pytest tests/test_bot.py tests/test_services.py -q`
Expected: PASS

## Chunk 4: Full Verification

### Task 4: Verify the complete change set

**Files:**
- Modify: `app/bot.py`
- Modify: `app/repositories.py`
- Modify: `app/services.py`
- Modify: `tests/test_bot.py`
- Modify: `tests/test_services.py`

- [ ] **Step 1: Run the focused verification suite**

Run: `python -m pytest tests/test_bot.py tests/test_services.py -q`
Expected: PASS

- [ ] **Step 2: Run the broader suite if the focused suite is clean**

Run: `python -m pytest -q`
Expected: PASS, or surface any unrelated pre-existing failures clearly.

- [ ] **Step 3: Review the diff**

Run: `git diff -- app/bot.py app/repositories.py app/services.py tests/test_bot.py tests/test_services.py`
Expected: only the account-menu feature changes are present.
