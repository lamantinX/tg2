# tg2 MVP

MVP for a Telegram-controlled service that manages authorized Telegram user sessions and posts AI-generated messages with explicit disclosure.

## Scope

- Manage multiple Telegram user sessions.
- Support per-account proxies for large account pools.
- Authorize sessions with Telegram login code flow and optional 2FA password.
- Assign a session to a target group/chat.
- Create groups with title, description, and username.
- Read recent context and generate a disclosed AI message.
- Configure per-binding system prompt, random send interval range, reply-to-latest interval, and context depth.
- Trigger actions through a Telegram bot and HTTP API.

## Safety constraints

- Use only accounts you own or are authorized to operate.
- Every generated message is prefixed with `AI_DISCLOSURE_PREFIX`.
- No stealth mode, human impersonation mode, or hidden automation.

## Quick start

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -e .
```

3. Copy `.env.example` to `.env` and fill in credentials.
   Relative runtime paths are resolved from the repository root, so the API and bot worker can be started from another working directory as long as `PYTHONPATH` points at the project.
4. Start the API:

```bash
uvicorn app.main:app --reload
```

5. In a separate terminal, start the Telegram bot worker:

```bash
python -m app.bot_runner
```

## Bot Menu

Telegram command menu is configured automatically on bot startup. In the chat with the bot, open the commands menu near the input field to see available actions.

## Wizard

- `/wizard` starts step-by-step onboarding for account creation, login, 2FA, and optional chat binding.
- `/cancel` resets the current wizard.
- During wizard steps you can send `skip` for optional proxy or chat binding.

## Short Instruction

1. Send `/start` to open the main menu.
2. Send `/wizard` for step-by-step setup.
3. Enter phone number like `+15550000001`.
4. Enter proxy URL or `skip`.
5. Enter the Telegram login code.
6. If asked, enter the Telegram 2FA password.
7. Enter `@chat_name` or `skip`.
8. Enter interval in minutes or `skip`.
9. Use `/chats` to see created bindings.
10. Use `/binding_settings <binding_id>` to inspect one binding.
11. Use `/set_binding_interval <binding_id> <min> [max]` for fixed or random intervals.
12. Use `/set_binding_reply_interval <binding_id> <min> [max]` to make the bot reply to one of the last 10 chat messages on a separate timer, or `off` to disable it.
13. Use `/set_binding_context <binding_id> <count>` to control how many messages are parsed before generation.
14. Use `/set_binding_prompt <binding_id> <text>` to set a per-binding system prompt.
15. Use `/reset_binding_prompt <binding_id>` to clear a custom prompt.
16. Use `/send_status` to see last and next send time.
17. Use `/audit_accounts` to validate accounts and clean bindings for inactive sessions.

For the full in-bot guide use `/help`.

## Bot commands

Navigation:
- `/start`
- `/help`
- `/wizard`
- `/cancel`

Accounts:
- `/accounts`
- `/add_account <phone> [proxy_url]`
- `/login_code <account_id>`
- `/login_finish <account_id> <code> [password]`
- `/login_password <account_id> <your_2fa_password>`
- `/audit_accounts`

Bindings and chats:
- `/chats`
- `/bind_chat <account_id> <chat_id_or_username> [interval_minutes]`
- `/binding_settings <binding_id>`
- `/delete_binding <binding_id>`

Binding settings:
- `/set_binding_interval <binding_id> <min_minutes> [max_minutes]`
- `/set_binding_reply_interval <binding_id> <min_minutes> [max_minutes] | off`
- `/set_binding_context <binding_id> <message_count>`
- `/set_binding_prompt <binding_id> <text>`
- `/reset_binding_prompt <binding_id>`

Generation and groups:
- `/send_status`
- `/generate_once <account_id> <chat_id_or_username>`
- `/create_group <account_id> <title> | <about> | <username>`

## Proxy format

Supported examples:

- `socks5://127.0.0.1:9050`
- `socks5://user:pass@127.0.0.1:1080`
- `http://user:pass@10.0.0.2:8080`

## HTTP API

- `POST /api/accounts`
- `POST /api/accounts/login/request`
- `POST /api/accounts/login/complete`
- `POST /api/accounts/login/password`
- `POST /api/bindings`
- `POST /api/generate`
- `POST /api/groups`

## Notes

- OpenAI integration uses the Responses API when `OPENAI_API_KEY` is configured.
- If OpenAI is unavailable, generation falls back to a local stub so the worker does not crash.
- Avatar upload and pinned post expansion are still limited in this MVP.

## Deployment

### Prerequisites

- Docker and Docker Compose installed on the server.
- `.env` file configured with production values.

### Quick Deploy (Linux/macOS)

```bash
chmod +x scripts/deploy.sh
./scripts/deploy.sh
```

### Quick Deploy (Windows PowerShell)

```powershell
.\scripts\deploy.ps1
```

### Manual Docker Deployment

1. Build and start containers:
```bash
docker-compose up -d --build
```

2. Check logs:
```bash
docker-compose logs -f
```

3. Stop services:
```bash
docker-compose down
```

### Data Persistence

By default, the project uses a local `./data` directory mapped to `/app/data` inside the containers. You can override it with `DATA_DIR` if you want runtime files somewhere else.

Relative runtime paths are resolved from the repository root, and runtime artifacts are stored under `DATA_DIR`. This covers:
- SQLite database (`app.db`)
- Telegram sessions (`sessions/`)
- Application logs (`logs/`)

With the default settings, these files are preserved between container restarts and updates under `./data`.
