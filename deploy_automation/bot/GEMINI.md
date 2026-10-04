# deploy_automation/bot — Telegram Bots

> [!IMPORTANT]
> **Live Graph Rule**: If you read or edit any file in `bot/`, update this `GEMINI.md` in the same commit if: a new command handler was added, a conversation step changed, a new callback action was added (e.g., `flow:*`), or the token store behavior changed.

## Purpose

Contains two Telegram bot implementations and an in-memory token store:

1. **`SREManagerBot`** (primary, 940 lines) — full interactive deploy flow for SRE team members.
2. **`TelegramService`** (legacy) — simpler single-admin bot for webhook-triggered review approve/reject.
3. **`InMemoryTokenStore`** — RAM-only temporary storage for personal GitLab tokens.

## Files

### `sre_manager_bot.py` — `SREManagerBot`

The main bot used in production. Handles the complete deployment lifecycle per team member.

**Commands registered:**
| Command | Handler | Description |
|---|---|---|
| `/start` | `cmd_start` | Welcome message with chat ID |
| `/help` | `cmd_help` | Usage instructions |
| `/id` / `/info` | `cmd_id` | Show user's Telegram chat ID |
| `/tasks` | `cmd_tasks` | List assigned tasks from ClickUp |
| `/resume` | `cmd_resume` | Resume last incomplete conversation |
| Callback | `handle_callback` | Inline keyboard button handler |
| Text | `handle_text_message` | Free-text input during conversation steps |

**Conversation state machine** (stored in DB `task_conversation_states`):
```
ASSIGNED → TOKEN_REQUESTED → ACCESS_CHECKING → 
  ├─ NO_ACCESS → COMMENT_DRAFTED → COMMENT_CONFIRMED/REJECTED
  └─ HAS_ACCESS → REQUIREMENTS_CHECKING → COMMENT_DRAFTED → 
       COMMENT_CONFIRMED → IN_PROGRESS / WAITING_FOR_CUSTOMER
```

**Key methods & properties:**
```python
SREManagerBot(token=..., clickup_service=..., ai_provider=..., proxy=...) # reads PROXY / HTTPS_PROXY
build_application() -> Application          # registers all handlers, configures proxy/get_updates_proxy, sets self.app
notify_task_dispatched(task, member, reason, msg_thread_id)  # sends proposal to topic
```

**Dependencies:**
- `ClickUpService` — fetch tasks, post comments, change status
- `GitLabService` — check maintainer access
- `AIServiceProvider` — generate deployment readiness comment text
- `DeployRequirementsChecker` — run 9-point check
- `InMemoryTokenStore` — collect & retrieve GitLab token from user
- `database` — `save_task_conversation_state`, `get_task_conversation_state`, `get_user_active_conversations`, `save_task_proposal_message`, `get_task_proposal_messages`
- `config_canned_responses` — CANNED_CLOSE_RESPONSES dict

**Security note:** GitLab tokens are collected via private Telegram message and stored ONLY in `InMemoryTokenStore` (RAM) with 30-minute TTL. Never written to DB, logs, or disk.

---

### `telegram_service.py` — `TelegramService`

Simpler, legacy bot used for the webhook-based review flow (triggered by `api/routes.py`).

**Constructor:** `TelegramService(token, admin_chat_id)`

**Key methods:**
```python
build_application() -> Application
send_review_request(session_id, message_text) -> Optional[int]   # sends Approve/Edit/Reject/Handoff buttons
handle_callback(update, context)    # processes button presses
handle_user_message(update, context)# processes free-text edits
```

**Callbacks handled (TelegramService — `approve/edit/reject/handoff`):**
- `approve_{session_id}` → posts comment to ClickUp, status → WAITING_FOR_CUSTOMER
- `edit_{session_id}` → prompts user for new comment text
- `reject_{session_id}` → cancels session
- `handoff_{session_id}` → status → HANDOFF_LAPTOP

**SREManagerBot — `flow:*` callback actions (in `handle_callback`):**
| Action | Description |
|---|---|
| `flow:confirm_assign:{tid}:{c_uid}` | Confirm task assignment to a member |
| `flow:reject_assign:{tid}` | Reject proposal (no assignment) |
| `flow:withdraw_assign:{tid}` | **Opt-out**: resolve telegram_id→clickup_id, remove user's button from Telegram proposal, sync all proposal chats (does NOT touch ClickUp assignees) |
| `flow:start_in_progress:{tid}` | Mark task in-progress in ClickUp |
| `flow:choose_close:{tid}` | Show close options |
| `flow:canned_select:{tid}:{key}` | Select a canned close response |
| `flow:do_close:{tid}` | Execute task close with comment |

---

### `token_store.py` — `InMemoryTokenStore`

Class-level dict (shared across all instances). No database. No files.

```python
InMemoryTokenStore.set_token(user_telegram_id, token, ttl_seconds=1800)
InMemoryTokenStore.get_token(user_telegram_id) -> Optional[str]
InMemoryTokenStore.remove_token(user_telegram_id)
```

TTL default: 30 minutes. Expired tokens are auto-purged on next `get_token()` call.

## Agent Notes

- **`SREManagerBot` is the active bot** — `TelegramService` is used only for legacy webhook pipeline.
- Both bots use `python-telegram-bot >= 21` async API.
- The bot's `Application` object is created by `build_application()` and stored as `self.app`.
- `main.py` calls `build_application()` then `tg_app.initialize()` + `start()` + `updater.start_polling()`.
- Do NOT add disk-based logging of GitLab tokens — security requirement.
- The supergroup uses Telegram "topics" (forum threads) — messages sent with `message_thread_id` parameter.
