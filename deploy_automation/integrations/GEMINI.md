# deploy_automation/integrations — External Service Clients

> [!IMPORTANT]
> **Live Graph Rule**: If you read or edit any file in `integrations/`, update this `GEMINI.md` in the same commit if: a new public async method was added to any service class, a new service file was created, or the HTTP client pattern changed.

## Purpose

All external API integrations: ClickUp, GitLab, AI providers (OpenAI/Anthropic), SSH-based infrastructure (Nginx, PostgreSQL). Each file is a self-contained service class.

## Files

### `clickup_service.py` — `ClickUpService` ⚠️ Large (61 KB)

The primary ClickUp REST API client. Uses **stdlib `urllib`** (no httpx/aiohttp) via `asyncio.to_thread`.

**Constructor:**
```python
ClickUpService(api_token: Optional[str] = None)
# Defaults to settings.CLICKUP_API_TOKEN
```

**Key methods (all async):**
```python
find_deploy_list_id(team_id) -> Optional[str]       # Finds the "deploy" list under SRE space
get_task(task_id) -> Optional[ClickUpTaskInfo]       # Fetch & parse single task
get_deploy_tasks(statuses) -> list[dict]             # Fetch tasks by status from deploy list
get_user_assigned_tasks(user_id) -> list[dict]       # Tasks assigned to a specific user
get_tasks_for_analytics(days) -> list[dict]          # All tasks for dashboard analytics
get_current_user_id() -> Optional[int]               # Cached user ID lookup
post_comment(task_id, comment) -> bool               # Post a comment to a task
update_task_status(task_id, status) -> bool          # Change task status
post_maintainer_request_comment(task_id, ...) -> bool# Specific comment for access request
remove_followers_except(task_id, keep_ids) -> bool   # Remove all followers except listed
assign_user_to_task(task_id, user_id, reporter_id) -> bool     # Assign user, remove others
remove_user_from_task_assignees(task_id, user_id) -> bool      # Remove a single user from assignees (for withdraw flow)
clean_watchers_keep_user_and_reporter(task_id, user_id, reporter_id) -> bool  # Prune watchers
get_task_members() -> list[dict]                     # Get all SRE team member info
```

**Internal state:**
- `_cached_deploy_list_id` — cached on first lookup
- `_cached_user_id` — cached on first lookup

**HTTP client pattern:**
```python
status, data = await self._http_request("GET", url, params={...})
# Runs sync urllib in asyncio.to_thread
```

---

### `clickup_dispatcher.py` — `SREClickUpDispatcher`

Monitors ClickUp for NEW SRE tasks (created after bot start) and proposes assignments.

**Constructor:**
```python
SREClickUpDispatcher(
    clickup_service=None,    # defaults to ClickUpService()
    ai_provider=None,        # defaults to AIServiceProvider()
    decision_engine=None     # defaults to SREDecisionEngine()
)
# self.start_timestamp = int(time.time() * 1000)  — only processes tasks created AFTER this
```

**Key method:**
```python
await dispatcher.check_and_dispatch_new_tasks(
    telegram_proposal_notifier=sre_bot_instance.notify_task_dispatched
)
```

**Safety rule:** NEVER modifies ClickUp directly. Always sends Telegram proposal first and waits for manager confirmation.

---

### `ai_provider.py` — `AIServiceProvider`

Unified synchronous LLM client (OpenAI-compatible + Anthropic). Used in the dispatcher and bot for text generation.

**Constructor:**
```python
AIServiceProvider(
    provider=None,           # "openai" or "anthropic" (from settings)
    openai_base_url=None, openai_api_key=None, openai_model=None,
    anthropic_base_url=None, anthropic_api_key=None, anthropic_model=None
)
```

**Key method:**
```python
result: Optional[str] = provider.call_llm(prompt, system_prompt=None)
# Synchronous — meant to be called from non-async context or via to_thread
# Fallback order: primary provider → secondary provider
```

Supports local models (Ollama, vLLM) via `OPENAI_BASE_URL=http://localhost:11434/v1`.

---

### `ai_service.py` — `AIService`

Higher-level AI service for dashboard features (task ranking, failure diagnosis). Uses Anthropic-compatible endpoint directly (not via `ai_provider.py`).

**Constructor:**
```python
AIService(base_url=None, auth_token=None)
# Reads ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN from env
# Tries multiple models: gemini-2.5-flash → claude-sonnet-4.6 → gpt-4o
```

**Key methods (all sync):**
```python
rank_tasks(tasks: list[dict]) -> list[dict]          # Returns tasks sorted by priority with reasons
generate_fix_suggestion(error_log: str) -> str       # CI/CD failure diagnosis
```

---

### `gitlab_service.py` — `GitLabService`

GitLab REST API client. Wraps project/file/branch operations.

**Constructor:**
```python
GitLabService(url=settings.GITLAB_URL, token=settings.GITLAB_TOKEN)
```

**Key methods (all async):**
```python
check_maintainer_access(repo_url) -> tuple[bool, Optional[str]]
extract_project_path(repo_url) -> str                # "group/project" from URL
get_project(project_path) -> Optional[GitLabProjectProxy]
fetch_repo_tree(project_id, ref, recursive) -> list[dict]
get_raw_file(project_id, file_path, ref) -> Optional[GitLabRawFile]
fetch_repo_files(repo_url, ref) -> dict[str, str]    # Returns {path: content} for key files
get_branches(project_id) -> list[dict]
create_file(project_id, data) -> bool
update_file(project_id, file_path, content, branch, commit_msg) -> bool
```

**Proxy classes (internal adapters):**
- `GitLabProjectProxy` — wraps project data dict, provides `.files` and `.repository_tree()`
- `GitLabFilesProxy` — wraps file operations on a project proxy
- `GitLabRawFile` — wraps file content, provides `.decode()` and `.save()`

---

### `nginx_service.py` — `NginxService`

SSH-based Nginx configuration deployment.

```python
await nginx_service.configure_nginx(
    server_ip, ssh_user, ssh_key_path,
    domain, upstream_port, ssl_mode, path_routing
)
```

Handles both system daemon mode (`/etc/nginx/`) and Docker container mode (`/srv/`).

---

### `postgres_service.py` — `PostgresService`

SSH-based PostgreSQL provisioning.

```python
await postgres_service.create_database(
    server_ip, ssh_user, ssh_key_path,
    db_name, db_user
)
# Returns: (db_name, db_user, db_password, dsn_string)
```

---

### `ssh_service.py` — `SSHService`

Paramiko-based SSH connection manager. Used by `nginx_service.py` and `postgres_service.py`.

```python
async with ssh_service.connect(host, user, key_path) as client:
    output = await ssh_service.run_command(client, "command")
```

## Agent Notes

- `clickup_service.py` is the largest file (61 KB) — do NOT read in full unless debugging a specific method. Use grep or view specific line ranges.
- `ai_provider.py` is **synchronous** — call via `asyncio.to_thread()` if needed in async context.
- `ai_service.py` is separate from `ai_provider.py` — different use cases (dashboard vs dispatch).
- SSH services require `paramiko` installed and valid SSH key paths.
- `ClickUpService` can accept a custom `api_token` in constructor — this is used in the bot when a user's personal token is temporarily needed.
