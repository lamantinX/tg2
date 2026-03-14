# Account Menu Actions Design

## Summary

The account menu will move from a plain text dump to an account-centric navigation flow.
Users will see each account as a separate button labeled with the resolved Telegram name and phone number.
From the account details screen they will be able to run a manual health check for one account or delete the account together with all related chat bindings.

## Current Context

- The bot currently renders `/accounts` and `menu:accounts` as plain text in `app/bot.py`.
- Account names are already stored in `telegram_accounts.account_name`, but the account list does not surface them consistently.
- A global account audit already exists in `AccountService.audit_accounts`, but there is no targeted single-account check exposed in the menu.
- Bindings can already be deleted by account id in `BindingRepository.delete_by_account_id`, but there is no account deletion flow.

## Goals

- Show account name and phone number together in the account menu.
- Attempt to resolve and persist missing Telegram names before rendering the account list.
- Add a dedicated account details view with per-account actions.
- Add a manual check action for a single account.
- Add account deletion that also removes all related bindings.
- Keep business logic in services and keep callbacks thin.

## Non-Goals

- No pagination or search for the account list in this iteration.
- No confirmation dialog for deletion in this iteration.
- No changes to the global audit report beyond reusing its logic where possible.

## UX Flow

### Account List

Entering `Аккаунты` will show an inline keyboard where each row is one account.
The button label format will be:

`<Имя Фамилия> | <+номер>`

Rules:

- The phone number is always shown.
- If `account_name` is missing, the bot will try to fetch it from Telegram before rendering the list.
- If name resolution fails, the list still renders with the phone number and any existing stored name if available.

### Account Details

Selecting an account opens a detail card that includes:

- account name
- phone number
- auth status
- active flag
- assigned character name

The details view includes these actions:

- `Ручная проверка`
- `Удалить аккаунт`
- `Назад к списку`
- `В меню`

### Manual Check Result

Pressing `Ручная проверка` runs a targeted account health check and then redraws the same account details card with updated data and a short result message.

## Service Design

### Single-Account Name Sync

`AccountService` will expose a helper that ensures a single account has an up-to-date name.
The menu list flow will use this helper before rendering each account that has no stored `account_name`.

### Single-Account Health Check

`AccountService` will expose a targeted method for checking one account.
The behavior matches the existing audit rules:

- resolve proxy
- create `TelegramAccountClient`
- run `check_health()`
- if authorized and active:
  - sync account name
  - update `auth_status` and `is_active`
  - resume auto-paused bindings for that account
- if unauthorized, revoked, banned, or error:
  - update `auth_status` and `is_active`
  - auto-pause bindings for that account with the returned reason

The method returns both the refreshed account object and a compact status payload for UI messaging.

### Account Deletion

`AccountService` will expose a delete method for one account.
Deletion order:

1. Load the account by id and fail with `ValueError` if it does not exist.
2. Delete all `chat_bindings` for the account.
3. Delete the account row.
4. Remove the local Telethon session file from `data/sessions/<session_name>.session` if present.
5. Ignore missing session files.

This keeps callbacks simple and ensures one place owns the destructive flow.

## Repository Changes

The repository layer will gain a direct account delete operation by id or by model instance.
Existing binding deletion by account id will be reused.

## Bot Changes

`app/bot.py` will gain:

- account list keyboard builder
- account details keyboard builder
- account list formatter helpers
- callback handlers for:
  - open account details
  - manual account check
  - delete account
  - return to account list

The old plain text `/accounts` output will be replaced with the same account-list screen used by the menu callback so both entry points stay consistent.

## Error Handling

- Missing account id returns a friendly error with a back/menu action.
- Manual check errors are translated into inactive status using the service result rather than crashing the callback.
- Name fetch failures do not block rendering.
- Missing session files during deletion are ignored.

## Testing Strategy

### Bot Presentation Tests

Add tests for:

- account button label uses `account_name | phone`
- account details text shows both account name and phone
- account details text falls back gracefully when character is absent

### Service Tests

Add tests for:

- single-account manual check resumes auto-paused bindings on healthy authorized account
- single-account manual check pauses bindings on revoked or failed account
- manual check syncs the account name when Telegram returns one
- cascading account deletion removes bindings, deletes the account, and attempts to remove the session file
- cascading account deletion does not fail if the session file is already absent

## Risks And Tradeoffs

- Fetching names during list rendering adds Telegram I/O when names are missing, but this only happens for unresolved accounts and keeps the menu useful.
- Skipping a delete confirmation keeps the first iteration faster but increases the risk of accidental deletion.
- The implementation should avoid broad refactors because `app/bot.py` is already carrying a lot of menu logic.

## Acceptance Criteria

- Opening `Аккаунты` shows one button per account with Telegram name and phone.
- If a missing name can be resolved, it is saved and shown.
- Opening an account shows a dedicated details screen with manual check and delete actions.
- Manual check updates status and binding auto-pause/resume state only for the chosen account.
- Deleting an account removes the account, all related bindings, and its local Telethon session file when present.
