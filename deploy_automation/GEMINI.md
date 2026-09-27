# deploy_automation — Package Overview

> [!IMPORTANT]
> **Live Graph Rule**: If you read or edit any file in this package, check whether this `GEMINI.md` needs updating. Update it (in the same commit) if: a new public function was added, a class was renamed, startup flow changed, or a new setting was added to `config.py`.

## Purpose

Top-level Python package. Houses all application code for the Deploy Automation service. The package is started via `python -m deploy_automation.main` or `uvicorn deploy_automation.main:app`.

## Key Files in This Directory

| File | Role |
|---|---|
| `main.py` | FastAPI app factory + asynccontextmanager lifespan (starts bot & monitor) |
| `config.py` | All settings via Pydantic `Settings` (reads `.env`) — single `settings` singleton |
| `database.py` | SQLAlchemy async engine, 4 ORM models, all CRUD async functions |
| `models.py` | Pydantic DTOs used as inter-module data transfer objects |
| `config_canned_responses.py` | Static Persian canned-response texts for closing ClickUp tasks |
| `cli.py` | Standalone interactive CLI deployer (93 KB) — NOT imported by main.py |

## Core Startup Flow (`main.py`)

```python
# lifespan context manager:
await init_db()                          # create SQLite tables if not exist
tg_app = sre_bot_instance.build_application()
set_telegram_service(sre_bot_instance)  # inject into routes.py global
await tg_app.start() + start_polling()
monitor_task = asyncio.create_task(clickup_monitor_loop())  # 60s loop
yield
# shutdown: cancel monitor, stop bot
```

## Settings Object (`config.py`)

Import with: `from deploy_automation.config import settings`

Key attributes:
- `settings.CLICKUP_API_TOKEN` / `settings.CLICKUP_TEAM_ID`
- `settings.TELEGRAM_BOT_TOKEN` / `settings.TELEGRAM_ADMIN_CHAT_ID`
- `settings.TELEGRAM_TEAM_GROUP_ID` / `settings.SRE_TEAM_MEMBERS` (JSON str)
- `settings.AI_PROVIDER` (`"openai"` | `"anthropic"`)
- `settings.GITLAB_URL` / `settings.GITLAB_TOKEN`
- `settings.DATABASE_URL` (default: `sqlite+aiosqlite:///./data/automation.db`)
- `settings.DECISION_MODE` (`"rule_engine"` | `"ai"` | `"hybrid"`)

## Data Models (`models.py`)

| Model | Usage |
|---|---|
| `ClickUpTaskInfo` | Parsed task data from ClickUp API |
| `ProjectReport` | Result of running 9-point checker |
| `CheckItemResult` | Single check outcome (PASSED/FAILED/WARNING/SKIPPED) |
| `CheckStatus` | Enum for check outcomes |
| `ReviewAction` | Enum: APPROVE/REJECT/EDIT/HANDOFF_LAPTOP |
| `PendingReviewSession` | Pydantic view over a DB `ReviewSession` row |

## Database (`database.py`)

All functions are `async`. Import pattern:
```python
from deploy_automation.database import (
    init_db, create_review_session, get_review_session,
    update_session_status, save_task_conversation_state,
    get_task_conversation_state, get_user_active_conversations,
    is_event_processed, mark_event_processed,
    save_task_proposal_message, get_task_proposal_messages,
    get_pending_handoff_sessions
)
```

Has an `ImportError` fallback — all functions become no-ops if SQLAlchemy is missing (safe for testing).

## Agent Notes

- `cli.py` is 93 KB — do NOT read it unless you're specifically working on CLI features.
- `config_canned_responses.py` contains Persian text templates — avoid modifying without user confirmation.
- The `data/` directory at repo root holds `automation.db` (SQLite) — it is gitignored.
- `settings` is imported at module import time — changes to `.env` require restart.
