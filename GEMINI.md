# Project Guidelines & Rules

- **Git Commit on Changes**: Always commit changes to git immediately after applying and verifying any modifications or new features, using clean conventional commit messages (e.g. `feat:`, `fix:`, `refactor:`).
- **Persian Text Formatting (RTL)**: Always wrap Persian responses and paragraphs in `<div dir="rtl">...</div>` (or use explicit RTL direction) so that all Persian text is rendered right-to-left correctly.
- **Live Graph Updates**: After reading, analyzing, or editing any source file in this project, check whether the corresponding `GEMINI.md` for that module is still accurate. If any of the following changed — public functions/methods, class signatures, imports, behavior, or inter-module wiring — update the relevant `GEMINI.md` in the same commit. Module-to-GEMINI.md map:
  - `deploy_automation/*.py` → `deploy_automation/GEMINI.md`
  - `deploy_automation/api/` → `deploy_automation/api/GEMINI.md`
  - `deploy_automation/bot/` → `deploy_automation/bot/GEMINI.md`
  - `deploy_automation/engine/` → `deploy_automation/engine/GEMINI.md`
  - `deploy_automation/integrations/` → `deploy_automation/integrations/GEMINI.md`
  - `deploy_automation/utils/` → `deploy_automation/utils/GEMINI.md`

---

# Deploy Automation — Project Architecture Graph

## Overview

A FastAPI + python-telegram-bot service that automates SRE deployment workflows on top of ClickUp + GitLab + Telegram. Three concurrent loops run in the same process:

1. **FastAPI HTTP server** (port 8085/8086) — serves the web dashboard + REST API + webhook receiver.
2. **Telegram Bot** (polling) — interactive deployment flow for SRE team members.
3. **ClickUp Monitor Loop** (asyncio, 60s interval) — watches for new SRE tasks and dispatches assignments.

## Module Map

```
deploy_automation/          → See deploy_automation/GEMINI.md
├── main.py                 → Entry point: wires all 3 loops, FastAPI lifespan
├── config.py               → Pydantic settings (reads .env)
├── database.py             → SQLite via SQLAlchemy async — 4 tables + CRUD helpers
├── models.py               → Pydantic DTOs shared across modules
├── api/                    → See deploy_automation/api/GEMINI.md
│   └── routes.py           → FastAPI routes + main webhook orchestration pipeline
├── bot/                    → See deploy_automation/bot/GEMINI.md
│   ├── sre_manager_bot.py  → Primary Telegram bot (940 lines, full deploy flow)
│   ├── telegram_service.py → Legacy single-admin bot (review approve/reject)
│   └── token_store.py      → In-memory RAM token store (no disk writes)
├── engine/                 → See deploy_automation/engine/GEMINI.md
│   ├── checker.py          → 9-point Git Flow readiness checker
│   ├── decision_engine.py  → Rule-based / AI task assignment engine
│   ├── task_state_detector.py → Detects current deploy milestone from task state
│   ├── cicd_generator.py   → Generates .gitlab-ci.yml deploy jobs
│   ├── compose_generator.py→ Generates docker-compose.yml
│   └── nginx_parser.py     → Parses / generates Nginx config blocks
├── integrations/           → See deploy_automation/integrations/GEMINI.md
│   ├── clickup_service.py  → ClickUp REST API client (61 KB, all endpoints)
│   ├── clickup_dispatcher.py → SRE task monitor + member assignment dispatcher
│   ├── ai_provider.py      → Unified AI client (OpenAI / Anthropic compatible)
│   ├── ai_service.py       → High-level AI analysis (dashboard ranking, diagnosis)
│   ├── gitlab_service.py   → GitLab API client (access check, file CRUD)
│   ├── nginx_service.py    → SSH-based Nginx deployment helper
│   ├── postgres_service.py → SSH-based PostgreSQL provisioning
│   └── ssh_service.py      → SSH connection manager (paramiko)
└── utils/                  → See deploy_automation/utils/GEMINI.md
    └── text_helpers.py     → Telegram/ClickUp message formatters
```

## Critical Wiring (main.py)

```python
# Runtime injection pattern — NOT constructor injection:
set_telegram_service(sre_bot_instance)   # routes.py reads a module-level global
```

## Key Environment Variables

| Variable | Purpose |
|---|---|
| `CLICKUP_API_TOKEN` | ClickUp personal token (`pk_...`) |
| `CLICKUP_TEAM_ID` | ClickUp workspace ID |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `TELEGRAM_ADMIN_CHAT_ID` | Legacy single-admin chat ID |
| `TELEGRAM_TEAM_GROUP_ID` | SRE supergroup with topic threads |
| `SRE_TEAM_MEMBERS` | JSON array with `name/clickup_id/telegram_id/topic_id` |
| `AI_PROVIDER` | `openai` or `anthropic` |
| `GITLAB_URL` | GitLab instance base URL |
| `GITLAB_TOKEN` | Personal access token for GitLab |
| `DATABASE_URL` | Default: `sqlite+aiosqlite:///./data/automation.db` |

## Database Tables

| Table | Purpose |
|---|---|
| `review_sessions` | Tracks webhook-triggered deploy review state |
| `task_conversation_states` | Per-user, per-task Telegram conversation step |
| `processed_events` | Idempotency guard for ClickUp webhooks |
| `task_proposal_messages` | Tracks Telegram messages sent per task for editing |

## Agent Notes

- **Never hardcode tokens** — always read from `settings` or env.
- **`cli.py`** is a standalone interactive CLI (93 KB) — runs independently from the FastAPI server.
- **`apply_step_refactor.py` / `apply_resume_feature.py`** at root are one-off migration scripts, not part of the running service.
- The `static/` directory holds the web dashboard (HTML/JS/CSS) served by FastAPI.
- HTTP client in `clickup_service.py` uses **stdlib `urllib`** (no httpx), run inside `asyncio.to_thread`.

