import logging
from typing import Optional, Any
from fastapi import APIRouter, BackgroundTasks, Request, Response, Header, HTTPException
from deploy_automation.config import settings
from deploy_automation.integrations.clickup_service import ClickUpService
from deploy_automation.integrations.gitlab_service import GitLabService
from deploy_automation.engine.checker import DeployRequirementsChecker
from deploy_automation.database import (
    create_review_session,
    is_event_processed,
    mark_event_processed,
    update_session_status
)
from deploy_automation.utils.text_helpers import format_telegram_review_message
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

clickup_service = ClickUpService()
gitlab_service = GitLabService()

# Will be injected from main.py
telegram_service = None


def set_telegram_service(tg):
    global telegram_service
    telegram_service = tg


async def process_task_pipeline(task_id: str, is_recheck: bool = False):
    """
    Main orchestration pipeline:
    1. Fetch task details from ClickUp (including service_needs & environment).
    2. Check GitLab maintainer permission.
    3. If no permission -> post request comment.
    4. If has permission -> fetch repo & run 9-point checks.
    5. Send interactive Telegram review message to SRE lead.
    """
    logger.info(f"Starting deploy readiness pipeline for task {task_id} (is_recheck={is_recheck})")
    
    task_info = await clickup_service.get_task(task_id)
    if not task_info:
        logger.warning(f"Could not retrieve task {task_id} from ClickUp")
        return

    repo_url = task_info.repo_url
    if not repo_url:
        logger.warning(f"No repository URL found in task {task_id}")
        return

    project_name = gitlab_service.extract_project_path(repo_url)
    
    # 1. Check Maintainer Access in GitLab
    has_access, err = await gitlab_service.check_maintainer_access(repo_url)
    if not has_access:
        logger.info(f"Maintainer access missing for {repo_url}. Posting comment.")
        await clickup_service.post_maintainer_request_comment(
            task_id=task_id,
            reporter_id=task_info.reporter_id,
            reporter_username=task_info.reporter_username
        )
        return

    # 2. Fetch repo files & branches
    project = await gitlab_service.get_project(repo_url)
    if not project:
        logger.error(f"Project {repo_url} could not be loaded from GitLab")
        return

    files_map = await gitlab_service.get_repository_files_map(project)
    branches = await gitlab_service.get_branches_and_protection(project)

    # 3. Run Deploy Requirements Check
    checker = DeployRequirementsChecker(
        project_name=project_name,
        repo_url=repo_url,
        files_map=files_map,
        branches=branches
    )
    report = checker.run_all_checks(has_maintainer_access=True)

    # 4. Create review session in DB
    session_id = await create_review_session(
        task_id=task_id,
        project_name=project_name,
        repo_url=repo_url,
        default_comment=report.comment_text,
        reporter_id=task_info.reporter_id,
        service_needs=task_info.service_needs,
        environment=task_info.environment
    )

    # 5. Send Telegram interactive prompt
    msg_text = format_telegram_review_message(report)
    if telegram_service:
        msg_id = await telegram_service.send_review_request(session_id, msg_text)
        if msg_id:
            await update_session_status(session_id, "PENDING", telegram_message_id=msg_id)
    else:
        logger.warning("Telegram service not connected to send review message.")


@router.post("/webhook/clickup")
async def clickup_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_signature: Optional[str] = Header(None)
):
    """
    Receives events from ClickUp Webhook (taskCreated, taskCommentPosted).
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event_type = body.get("event")
    task_id = body.get("task_id")
    event_id = body.get("webhook_id", "") + "_" + str(body.get("date", ""))

    if not task_id:
        return {"status": "ignored", "reason": "no_task_id"}

    # Avoid duplicate event processing
    if event_id and await is_event_processed(event_id):
        return {"status": "duplicate_ignored"}

    if event_id:
        await mark_event_processed(event_id, event_type or "unknown", task_id)

    # Handle Task Created
    if event_type == "taskCreated":
        background_tasks.add_task(process_task_pipeline, task_id, False)
        return {"status": "task_creation_queued", "task_id": task_id}

    # Handle Task Comment Posted
    elif event_type == "taskCommentPosted":
        history_items = body.get("history_items") or []
        comment_text = ""
        for item in history_items:
            comment_obj = item.get("comment") or {}
            comment_text += " " + (comment_obj.get("text_content") or "")

        if clickup_service.is_recheck_comment(comment_text):
            logger.info(f"Re-check triggered by comment on task {task_id}: {comment_text}")
            background_tasks.add_task(process_task_pipeline, task_id, True)
            return {"status": "recheck_queued", "task_id": task_id}
        else:
            return {"status": "comment_ignored", "reason": "not_recheck_keyword"}

    return {"status": "event_ignored", "event": event_type}


class ManualCheckRequest(BaseModel):
    task_id: str


@router.post("/api/manual-check")
async def manual_check(req: ManualCheckRequest, background_tasks: BackgroundTasks):
    """
    Manually trigger checks for a ClickUp task ID.
    """
    background_tasks.add_task(process_task_pipeline, req.task_id, False)
    return {"status": "queued", "task_id": req.task_id}


# ==============================================================================
# Liquid Glass Dashboard APIs
# ==============================================================================

from deploy_automation.integrations.ai_service import AIService
import time

ai_service = AIService()

# Simple cache for fast responsiveness
_dashboard_cache: dict[str, Any] = {
    "my_tasks": {},  # key: user_id or "default" -> {"data": ..., "timestamp": ...}
    "sre_form_tasks": {"data": None, "timestamp": 0}
}
CACHE_TTL = 90  # seconds


class CommentCreateRequest(BaseModel):
    comment_text: str
    notify_all: bool = False


# Known DevOps / SRE team members list (dynamically loaded or generic template)
def _get_configured_team_members():
    import json
    try:
        if settings.SRE_TEAM_MEMBERS:
            items = json.loads(settings.SRE_TEAM_MEMBERS)
            members = []
            for it in items:
                name = it.get("name", "Team Member")
                parts = name.split()
                initials = "".join([p[0].upper() for p in parts[:2]]) if parts else "TM"
                members.append({
                    "id": it.get("clickup_id", 1001),
                    "username": name,
                    "email": it.get("email", ""),
                    "initials": initials,
                    "color": "#6366f1",
                    "profile_picture": None,
                    "role": "DevOps Engineer"
                })
            if members:
                return members
    except Exception:
        pass
    return [
        {
            "id": 1001,
            "username": "Alice Developer",
            "email": "alice@example.com",
            "initials": "AD",
            "color": "#6366f1",
            "profile_picture": None,
            "role": "DevOps Engineer"
        },
        {
            "id": 1002,
            "username": "Bob Engineer",
            "email": "bob@example.com",
            "initials": "BE",
            "color": "#06b6d4",
            "profile_picture": None,
            "role": "DevOps Engineer"
        }
    ]

DEVOPS_TEAM_MEMBERS = _get_configured_team_members()


@router.get("/api/me")
async def get_current_user_info():
    """Returns current user profile information."""
    profile = await clickup_service.get_current_user_profile()
    if not profile:
        raise HTTPException(status_code=404, detail="User profile not found")
    return profile


@router.get("/api/team/members")
async def get_devops_members():
    """Returns the list of DevOps / SRE team members."""
    return {"members": DEVOPS_TEAM_MEMBERS}


@router.get("/api/tasks/my")
async def get_my_tasks(user_id: Optional[int] = None, refresh: bool = False):
    """
    Returns tasks assigned to the specified user (or current user if omitted),
    categorized by state (status) and prioritized by AI.
    """
    now = time.time()
    cache_key = str(user_id) if user_id else "default"
    cache_entry = _dashboard_cache["my_tasks"].get(cache_key)
    if not refresh and cache_entry and cache_entry["data"] and (now - cache_entry["timestamp"] < CACHE_TTL):
        return cache_entry["data"]

    tasks = await clickup_service.get_user_assigned_tasks(user_id=user_id)
    tasks = [t for t in tasks if (t.get("status") or "").lower().strip() not in ["ready to test", "closed", "done"]]
    ranked_tasks = ai_service.prioritize_tasks(tasks)

    # Group by state / status
    state_map: dict[str, dict[str, Any]] = {}
    critical_count = 0
    high_count = 0

    for t in ranked_tasks:
        st = t.get("status") or "open"
        st_color = t.get("status_color") or "#6b7280"
        urgency = t.get("urgency_level")
        if urgency == "CRITICAL":
            critical_count += 1
        elif urgency == "HIGH":
            high_count += 1

        if st not in state_map:
            state_map[st] = {
                "state": st,
                "state_color": st_color,
                "tasks": []
            }
        state_map[st]["tasks"].append(t)

    # Sort state groups: active states first
    def state_sort_key(item):
        s = item["state"].lower()
        if "in progress" in s:
            return 0
        if "waiting" in s:
            return 1
        if "new" in s or "open" in s:
            return 2
        return 3

    groups = sorted(state_map.values(), key=state_sort_key)

    response_data = {
        "user_id": user_id,
        "total_tasks": len(ranked_tasks),
        "critical_count": critical_count,
        "high_count": high_count,
        "state_groups": groups,
        "cached_at": now
    }

    # Only cache non-empty results so an API issue or temporary empty result isn't stuck for CACHE_TTL
    if len(ranked_tasks) > 0:
        _dashboard_cache["my_tasks"][cache_key] = {"data": response_data, "timestamp": now}
    return response_data


@router.get("/api/tasks/sre-forms")
async def get_sre_form_tasks_endpoint(refresh: bool = False):
    """
    Returns all tasks from SRE space Form folder / form response lists,
    ordered by AI priority (flat list with form name, assignees, and state).
    """
    now = time.time()
    cache_entry = _dashboard_cache["sre_form_tasks"]
    if not refresh and cache_entry["data"] and (now - cache_entry["timestamp"] < CACHE_TTL):
        return cache_entry["data"]

    tasks = await clickup_service.get_sre_form_tasks()
    tasks = [t for t in tasks if (t.get("status") or "").lower().strip() not in ["ready to test", "closed", "done"]]
    ranked_tasks = ai_service.prioritize_tasks(tasks)

    # Extract unique form names for filters
    form_names = sorted(list({t.get("form_name") for t in ranked_tasks if t.get("form_name")}))
    states = sorted(list({t.get("status") for t in ranked_tasks if t.get("status")}))

    critical_count = sum(1 for t in ranked_tasks if t.get("urgency_level") == "CRITICAL")
    high_count = sum(1 for t in ranked_tasks if t.get("urgency_level") == "HIGH")

    response_data = {
        "total_tasks": len(ranked_tasks),
        "critical_count": critical_count,
        "high_count": high_count,
        "form_names": form_names,
        "states": states,
        "tasks": ranked_tasks,
        "cached_at": now
    }

    _dashboard_cache["sre_form_tasks"] = {"data": response_data, "timestamp": now}
    return response_data


@router.get("/api/tasks/{task_id}/details")
async def get_task_details_endpoint(task_id: str):
    """Returns full details, custom fields, comments and AI analysis for the modal."""
    details = await clickup_service.get_task_details(task_id)
    if not details:
        raise HTTPException(status_code=404, detail="Task not found")

    # Add AI analysis
    heuristic = ai_service.calculate_heuristic_priority(details)
    details["priority_score"] = heuristic["priority_score"]
    details["urgency_level"] = heuristic["urgency_level"]
    details["ai_reasoning"] = heuristic["ai_reasoning"]
    details["suggested_action"] = heuristic["suggested_action"]

    return details


@router.post("/api/tasks/{task_id}/comments")
async def post_task_comment_endpoint(task_id: str, req: CommentCreateRequest):
    """Submits a new comment to ClickUp directly from the modal."""
    if not req.comment_text or not req.comment_text.strip():
        raise HTTPException(status_code=400, detail="متن کامنت نمی‌تواند خالی باشد")

    success = await clickup_service.post_comment(
        task_id=task_id,
        comment_text=req.comment_text.strip(),
        notify_all=req.notify_all
    )
    if not success:
        raise HTTPException(status_code=500, detail="خطا در ارسال کامنت به کلیک‌آپ")

    return {"status": "success", "task_id": task_id}


_is_analytics_refreshing = False

@router.get("/api/analytics/sre")
async def get_sre_analytics_endpoint(response: Response, background_tasks: BackgroundTasks, refresh: bool = False):
    """
    Returns analytics and metrics for SRE dashboard charts.
    Uses stale-while-revalidate caching to ensure near-instantaneous responses.
    Ensures that when a calendar day changes, cache is invalidated.
    """
    global _is_analytics_refreshing
    import time
    import datetime
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    now = time.time()
    today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    cache_entry = _dashboard_cache.get("analytics_sre", {"data": None, "timestamp": 0})

    # Invalidate cache if it was generated on a previous calendar day
    cache_day = None
    if cache_entry["data"] and "generated_at" in cache_entry["data"]:
        try:
            cache_day = datetime.datetime.fromtimestamp(
                cache_entry["data"]["generated_at"], tz=datetime.timezone.utc
            ).strftime("%Y-%m-%d")
        except Exception:
            pass

    is_cache_from_previous_day = (cache_day is not None and cache_day != today_str)

    async def _bg_refresh():
        global _is_analytics_refreshing
        try:
            new_data = await clickup_service.get_sre_analytics_data()
            _dashboard_cache["analytics_sre"] = {"data": new_data, "timestamp": time.time()}
        finally:
            _is_analytics_refreshing = False

    # If cache exists, is from today, and is fresh (< 300 seconds), return immediately
    if cache_entry["data"] and not is_cache_from_previous_day and (now - cache_entry["timestamp"] < 300) and not refresh:
        return cache_entry["data"]

    # If cache exists and is from today but stale, return stale immediately & refresh in background
    if cache_entry["data"] and not is_cache_from_previous_day and not refresh:
        if not _is_analytics_refreshing:
            _is_analytics_refreshing = True
            background_tasks.add_task(_bg_refresh)
        return cache_entry["data"]

    # Otherwise (forced refresh, cache empty, or date rolled over) fetch synchronously
    data = await clickup_service.get_sre_analytics_data()
    _dashboard_cache["analytics_sre"] = {"data": data, "timestamp": now}
    return data


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": settings.APP_NAME}

