# deploy_automation/api — FastAPI Routes & Webhook Pipeline

> [!IMPORTANT]
> **Live Graph Rule**: If you read or edit `routes.py`, update this `GEMINI.md` in the same commit if: a new endpoint was added/removed, the pipeline flow changed, a new dependency was imported, or the `set_telegram_service` wiring changed.

## Purpose

Defines the FastAPI `APIRouter` that handles:
1. **ClickUp webhooks** — triggers the main deploy readiness pipeline.
2. **SRE Dashboard REST API** — `/api/me`, `/api/tasks/*`, `/api/analytics/sre`.
3. **Telegram injection** — receives `TelegramService` instance at startup from `main.py`.

## Files

### `routes.py` (only file)

#### Module-level globals (important!)
```python
router = APIRouter()
clickup_service = ClickUpService()    # instantiated at import time
gitlab_service = GitLabService()      # instantiated at import time
telegram_service = None               # injected at runtime by main.py

def set_telegram_service(tg):         # called from main.py lifespan
    global telegram_service
    telegram_service = tg
```

#### Key Functions

| Function | Description |
|---|---|
| `process_task_pipeline(task_id, is_recheck)` | Main orchestration: fetch task → check GitLab access → run 9-point check → send Telegram review |
| `set_telegram_service(tg)` | Runtime injection of Telegram service |

#### Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/webhook/clickup` | Receives ClickUp webhook events (`taskCreated`, `taskStatusUpdated`) |
| `GET` | `/health` | Health check |
| `GET` | `/api/me` | Current user info from ClickUp |
| `GET` | `/api/tasks/my` | AI-ranked tasks assigned to current user |
| `GET` | `/api/tasks/sre-forms` | All SRE form tasks |
| `GET` | `/api/analytics/sre` | Analytics data (heatmap, throughput, charts) |
| `POST` | `/api/tasks/{task_id}/comments` | Post comment to ClickUp task |

#### Pipeline Flow (`process_task_pipeline`)

```
ClickUp webhook
  → get_task(task_id)             [clickup_service]
  → check_maintainer_access()     [gitlab_service]
  → if no access: post_maintainer_request_comment() + return
  → fetch_repo_files()            [gitlab_service]
  → DeployRequirementsChecker.run_all_checks()
  → create_review_session()       [database]
  → send_review_request()         [telegram_service]
```

## Dependencies

**Imports from project:**
- `deploy_automation.config.settings`
- `deploy_automation.integrations.clickup_service.ClickUpService`
- `deploy_automation.integrations.gitlab_service.GitLabService`
- `deploy_automation.engine.checker.DeployRequirementsChecker`
- `deploy_automation.database` — `create_review_session`, `is_event_processed`, `mark_event_processed`, `update_session_status`
- `deploy_automation.utils.text_helpers.format_telegram_review_message`

**Injected at runtime:**
- `telegram_service` (set by `main.py` via `set_telegram_service()`)

## Agent Notes

- **Idempotency**: `is_event_processed` / `mark_event_processed` guard against duplicate webhook processing.
- `is_recheck=True` triggers a re-run of the pipeline when a "requirements fixed" keyword appears in a comment.
- The analytics endpoint caches data and invalidates at midnight (calendar day boundary).
- Dashboard serves from `static/index.html` — mounted in `main.py`, not here.
