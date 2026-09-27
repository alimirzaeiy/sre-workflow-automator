import json
import logging
import os
from typing import Any, Optional
from deploy_automation.config import settings

logger = logging.getLogger(__name__)


class SREDecisionEngine:
    """
    Deterministic rule & policy engine for task assignment and workload balancing.
    Can be used as a standalone engine (replacing LLM AI) or in hybrid mode.
    
    Features:
    1. Skill & Domain Rules: Matches task titles/tags/lists to member domain expertise.
    2. Workload Balancing: Prioritizes members with the fewest active 'in progress' tasks.
    3. Transparent Reason Generation: Produces clean, readable Persian explanations for the manager.
    4. Deterministic & Sub-millisecond Execution: Zero dependence on external LLM APIs.
    """

    def __init__(self, rules_config_path: Optional[str] = None):
        self.rules_config_path = rules_config_path or getattr(settings, "ASSIGNMENT_RULES_PATH", None)
        self.rules = self._load_rules()

    def _load_rules(self) -> dict[str, Any]:
        """Loads domain matching rules from json file or default fallbacks."""
        if self.rules_config_path and os.path.exists(self.rules_config_path):
            try:
                with open(self.rules_config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load assignment rules from {self.rules_config_path}: {e}")

        # Default standard domain/skill rules for SRE team
        return {
            "domain_skills": {
                "redis": ["redis", "والیوم", "aof", "کَش", "cache"],
                "database": ["database", "دیتابیس", "postgres", "mysql", "mongodb", "backup", "بکاپ"],
                "k8s_infra": ["kubernetes", "k8s", "کوبرنتیز", "helm", "node", "cluster", "کلاستر"],
                "devops_deploy": ["deploy", "دیپلوی", "استقرار", "gitlab", "pipeline", "پایپ‌لاین", "ci/cd"],
                "network_os": ["network", "شبکه", "vpn", "firewall", "فایروال", "vm", "ماشین مجازی"]
            }
        }

    @staticmethod
    def is_change_env_task(task: dict[str, Any]) -> bool:
        """
        Checks whether a change task is specifically an environment variable change.
        Looks at custom fields (envs, env vars, etc.) or task title/description.
        """
        custom_fields = task.get("custom_fields") or []
        for cf in custom_fields:
            name = (cf.get("name") or "").lower()
            val = cf.get("value") or cf.get("display_value")
            if any(k in name for k in ["env", "متغیر"]):
                if val:
                    return True

        if task.get("envs"):
            return True

        name = (task.get("name") or "").lower()
        desc = (task.get("description") or "").lower()
        env_keywords = ["تغییر env", "env var", "environment variable", "متغیر محیطی", "متغیرهای محیطی", "env "]
        return any(k in name or k in desc for k in env_keywords)

    @classmethod
    def calculate_task_penalty(cls, task: dict[str, Any]) -> tuple[int, str]:
        """
        Calculates penalty points for an active assigned task:
        - deploy: -5 points (5 امتیاز منفی)
        - third party, change, issue: -3 points (3 امتیاز منفی)
          * EXCEPT if change is env change: -1 point (1 امتیاز منفی)
        - internal: -2 points (2 امتیاز منفی)
        - other/fallback: -2 points
        Returns (penalty, category_name)
        """
        list_name = ((task.get("list") or {}).get("name") or task.get("list_name") or task.get("form_name") or "").lower().strip()
        task_name = (task.get("name") or "").lower()

        # Check deploy
        if "deploy" in list_name or "دیپلوی" in list_name:
            return 5, "دیپلوی"

        # Check change
        if "change" in list_name or "تغییر" in list_name:
            if cls.is_change_env_task(task):
                return 1, "تغییر env"
            return 3, "change"

        # Check third party
        if "third party" in list_name or "third-party" in list_name or "thirdparty" in list_name or "ترد پارتی" in list_name:
            return 3, "third party"

        # Check issue
        if "issue" in list_name or "ایشو" in list_name:
            return 3, "issue"

        # Check internal
        if "internal" in list_name or "اینترنال" in list_name or "داخلی" in list_name:
            return 2, "اینترنال"

        # Fallback inspection on task name
        if "deploy" in task_name or "دیپلوی" in task_name:
            return 5, "دیپلوی"
        if "change" in task_name or "تغییر" in task_name:
            if cls.is_change_env_task(task):
                return 1, "تغییر env"
            return 3, "change"
        if "third party" in task_name or "third-party" in task_name or "thirdparty" in task_name:
            return 3, "third party"
        if "issue" in task_name:
            return 3, "issue"
        if "internal" in task_name or "داخلی" in task_name:
            return 2, "اینترنال"

        # Default for other tasks
        return 2, "سایر"

    def evaluate_task_assignment(
        self,
        task: dict[str, Any],
        members_workloads: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Evaluates task and team workloads using deterministic rule policies:
        - Calculates total negative penalty points for each member based on their active sole-assigned tasks.
        - Picks the candidate with the highest score (least negative penalty).
        - Generates clear, concise, and transparent Persian reason.
        """
        if not members_workloads:
            return {
                "assignee_id": None,
                "assignee_name": None,
                "reason": "هیچ عضوی در تیم برای تخصیص یافت نشد."
            }

        if len(members_workloads) == 1:
            m = members_workloads[0]
            return {
                "assignee_id": m["id"],
                "assignee_name": m["name"],
                "reason": "تخصیص بر اساس تنها عضو فعال موجود در تنظیمات تیم."
            }

        scored_candidates = []
        for m in members_workloads:
            active_tasks = m.get("in_progress_tasks", [])
            total_penalty = 0
            category_counts: dict[str, int] = {}

            for t in active_tasks:
                penalty, cat_name = self.calculate_task_penalty(t)
                total_penalty += penalty
                category_counts[cat_name] = category_counts.get(cat_name, 0) + 1

            # Score: higher is better (0 penalty -> 100 score, each penalty point deducts)
            score = 100 - total_penalty

            scored_candidates.append({
                "member": m,
                "score": score,
                "total_penalty": total_penalty,
                "task_count": len(active_tasks),
                "category_counts": category_counts
            })

        # Sort by highest score (least penalty); break ties by fewest total tasks
        scored_candidates.sort(key=lambda x: (x["score"], -x["task_count"]), reverse=True)
        winner = scored_candidates[0]
        chosen_m = winner["member"]
        total_pen = winner["total_penalty"]
        cnt = winner["task_count"]
        cat_counts = winner["category_counts"]

        # Build clean, fluent and concise Persian reason
        if cnt == 0:
            reason_str = "کمترین بار کاری (بدون تسک فعال)"
        else:
            # Format grouped counts: e.g. "۲ اینترنال و ۱ دیپلوی"
            parts = [f"{count} {cat}" for cat, count in cat_counts.items()]
            summary_tasks = " و ".join(parts)
            reason_str = f"کمترین بار کاری با {summary_tasks} (مجموع {total_pen}- امتیاز منفی)"

        return {
            "assignee_id": chosen_m["id"],
            "assignee_name": chosen_m["name"],
            "reason": reason_str
        }

