---
tracker:
  kind: linear
  project_slug: "0c0f72b71f93"
  active_states:
    - Todo
    - In Progress
    - Human Review
    - Rework
    - Merging
  terminal_states:
    - Done
    - Closed
    - Cancelled
    - Canceled
    - Duplicate
  polling:
    interval_ms: 5000

workspace:
  root: $SYMPHONY_WORKSPACE_ROOT

hooks:
  after_create: |
    git clone --depth 1 "$env:SOURCE_REPO_URL" .

agent:
  max_concurrent_agents: 5
  max_turns: 20

codex:
  command: 'C:\Users\zelen\AppData\Roaming\npm\codex.cmd --config shell_environment_policy.inherit=all --model gpt-5.3-codex app-server'
  approval_policy: never
  thread_sandbox: workspace-write
  turn_sandbox_policy:
    type: workspaceWrite
---
You are working on Linear issue `{{ issue.identifier }}` for the `tg2` repository.

Issue context:
- Title: {{ issue.title }}
- State: {{ issue.state }}
- Labels: {{ issue.labels }}
- URL: {{ issue.url }}
- Description:
{% if issue.description %}
{{ issue.description }}
{% else %}
No description provided.
{% endif %}

Repository context:
- Python 3.11 project managed with `pip install -e .`
- Main app entrypoint: `uvicorn app.main:app --reload`
- Bot worker: `python -m app.bot_runner`
- Tests: `pytest`
- Runtime configuration lives in `.env`; use `.env.example` as the reference shape

Operating rules:
1. Work only inside the provided workspace clone.
2. Do not ask a human to take follow-up actions unless the task is blocked by missing credentials, permissions, or external services.
3. Start by inspecting the current code and reproducing the issue or requirement before editing files.
4. Prefer targeted tests for the changed area, then broaden validation if the risk profile warrants it.
5. Never commit or print secrets from `.env`, `.env.symphony`, or any other local credential file.
6. Preserve the repository's safety constraints: generated content must remain explicitly disclosed and automation must not be hidden or deceptive.

Completion bar:
- The requested code or config change is implemented.
- Relevant tests or validation commands have been run and results are summarized.
- Any remaining blocker or risk is stated briefly and concretely.







