import json
import time
import logging
import asyncio
from typing import Optional, Any
from deploy_automation.config import settings
from deploy_automation.integrations.clickup_service import ClickUpService
from deploy_automation.integrations.ai_provider import AIServiceProvider
from deploy_automation.engine.decision_engine import SREDecisionEngine

logger = logging.getLogger(__name__)

class SREClickUpDispatcher:
    """
    Monitors SRE tasks on ClickUp:
    1. ONLY monitors tasks created AFTER the bot started (start_timestamp).
    2. Proposes assignments using SREDecisionEngine (rule/policy engine) or AI.
    3. ABSOLUTELY DOES NOT modify ClickUp without explicit user confirmation.
       Sends an approval request to Telegram first!
    """

    def __init__(
        self,
        clickup_service: Optional[ClickUpService] = None,
        ai_provider: Optional[AIServiceProvider] = None,
        decision_engine: Optional[SREDecisionEngine] = None
    ):
        self.clickup = clickup_service or ClickUpService()
        self.ai = ai_provider or AIServiceProvider()
        self.decision_engine = decision_engine or SREDecisionEngine()
        # Mark startup time in milliseconds (ClickUp timestamps are in ms)
        self.start_timestamp = int(time.time() * 1000)
        self._processed_task_ids: set[str] = set()
        logger.info(f"SREClickUpDispatcher initialized. Watching strictly for tasks created after timestamp: {self.start_timestamp}")

    def get_team_members(self) -> list[dict[str, Any]]:
        """Parses SRE_TEAM_MEMBERS from settings/env."""
        raw = getattr(settings, "SRE_TEAM_MEMBERS", "[]")
        try:
            members = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(members, list):
                return members
        except Exception as e:
            logger.warning(f"Could not parse SRE_TEAM_MEMBERS: {e}")
        return []

    async def get_member_in_progress_tasks(self, clickup_user_id: int) -> list[dict[str, Any]]:
        """
        Fetches active tasks for a member where:
        1. That person is the SOLE assignee (len(assignees) == 1 and assignee_id == person_id).
        2. Status is either 'in progress' or 'new'.
        """
        tasks = await self.clickup.get_user_assigned_tasks(user_id=clickup_user_id)
        sole_active_tasks = []
        for t in tasks:
            st = (t.get("status") or "").lower().strip()
            # Must be 'in progress' or 'new'
            if "progress" not in st and st not in ["new", "in progress"]:
                continue

            # Sole assignee check: only this person is assigned
            assignees = t.get("assignees") or []
            if len(assignees) == 1:
                first_assignee = assignees[0]
                # first_assignee can be dict with 'id' or int
                assignee_id = first_assignee.get("id") if isinstance(first_assignee, dict) else first_assignee
                if str(assignee_id) == str(clickup_user_id):
                    sole_active_tasks.append(t)

        return sole_active_tasks


    async def check_and_dispatch_new_tasks(self, telegram_proposal_notifier=None) -> list[dict[str, Any]]:
        """
        Scans SRE form tasks strictly for tasks created AFTER bot start time.
        Evaluates workload with AI and sends an approval proposal to Telegram.
        DOES NOT assign in ClickUp automatically!
        """
        team_members = self.get_team_members()
        if not team_members:
            return []

        all_tasks = await self.clickup.get_sre_form_tasks()
        proposals_created = []

        # Find strictly new tasks created after bot startup
        new_incoming_tasks = []
        for t in all_tasks:
            tid = t.get("id")
            if not tid or tid in self._processed_task_ids:
                continue

            # Strict check: task must be created AFTER the bot started
            created_ts = int(t.get("date_created") or 0)
            if created_ts <= self.start_timestamp:
                # Task existed prior to bot start - ignore completely!
                self._processed_task_ids.add(tid)
                continue

            status = (t.get("status") or "").lower().strip()
            assignees = t.get("assignees") or []

            # Check if task is 'new' or unassigned
            if status in ["new", "open", "to do", "در انتظار بررسی"] or len(assignees) == 0:
                new_incoming_tasks.append(t)

        if not new_incoming_tasks:
            return []

        # Collect current workload of each configured team member
        workloads = []
        for m in team_members:
            c_uid = m.get("clickup_id")
            if not c_uid:
                continue
            in_prog_tasks = await self.get_member_in_progress_tasks(int(c_uid))
            workloads.append({
                "id": c_uid,
                "name": m.get("name", "Member"),
                "telegram_id": m.get("telegram_id"),
                "topic_id": m.get("topic_id"),
                "in_progress_tasks": in_prog_tasks
            })

        for task in new_incoming_tasks:
            tid = task.get("id")
            self._processed_task_ids.add(tid)

            # Decision logic: evaluate via rule_engine or AI based on DECISION_MODE
            decision_mode = (getattr(settings, "DECISION_MODE", "rule_engine") or "rule_engine").lower()
            if decision_mode == "rule_engine":
                decision = self.decision_engine.evaluate_task_assignment(task, workloads)
            elif decision_mode == "ai":
                decision = self.ai.estimate_workload_and_pick_assignee(task, workloads)
            else:
                # Hybrid: evaluate via rule engine first
                decision = self.decision_engine.evaluate_task_assignment(task, workloads)
                if not decision.get("assignee_id"):
                    decision = self.ai.estimate_workload_and_pick_assignee(task, workloads)

            chosen_uid = decision.get("assignee_id")
            chosen_name = decision.get("assignee_name")
            reason = decision.get("reason")

            if not chosen_uid:
                continue

            matched_member = next((m for m in workloads if str(m["id"]) == str(chosen_uid)), None)
            if not matched_member:
                continue

            # IMPORTANT: We DO NOT call self.clickup.assign_user_to_task here!
            # Zero modifications to ClickUp without user confirmation.
            logger.info(f"Generated assignment proposal for task {tid} ({task.get('name')}) -> {chosen_name}. Awaiting user confirmation.")

            proposal_info = {
                "task": task,
                "member": matched_member,
                "reason": reason
            }
            proposals_created.append(proposal_info)

            # Send interactive proposal to Telegram for user confirmation
            if telegram_proposal_notifier:
                try:
                    await telegram_proposal_notifier(proposal_info)
                except Exception as ex:
                    logger.error(f"Error in telegram_proposal_notifier for task {tid}: {ex}")

        return proposals_created
