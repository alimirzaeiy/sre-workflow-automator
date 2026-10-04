import os
import json
import urllib.request
import urllib.error
from typing import Optional, Any


class AIService:
    """
    Service for automated pipeline failure diagnosis and fix generation using LLM.
    Uses conduit/Anthropic-compatible endpoint configured in environment.
    """

    def __init__(self, base_url: Optional[str] = None, auth_token: Optional[str] = None, proxy: Optional[str] = None):
        self.base_url = (base_url or os.environ.get("ANTHROPIC_BASE_URL", "")).rstrip("/")
        self.auth_token = auth_token or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        self.proxy = proxy or os.environ.get("PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("ALL_PROXY")
        self._opener = None
        if self.proxy:
            proxy_handler = urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})
            self._opener = urllib.request.build_opener(proxy_handler)
        self.candidate_models = [
            "gemini-2.5-flash",
            "gemini-3-flash",
            "claude-sonnet-4.6",
            "claude-sonnet-4.5",
            "gpt-4o"
        ]

    def _call_llm(self, prompt: str, system_prompt: Optional[str] = None) -> Optional[str]:
        if not self.base_url or not self.auth_token:
            return None

        url = f"{self.base_url}/v1/messages"
        headers = {
            "x-api-key": self.auth_token,
            "Authorization": f"Bearer {self.auth_token}",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }

        for model in self.candidate_models:
            payload: dict[str, Any] = {
                "model": model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}]
            }
            if system_prompt:
                payload["system"] = system_prompt

            try:
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                open_fn = self._opener.open if self._opener else urllib.request.urlopen
                with open_fn(req, timeout=35) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    content = resp_data.get("content", [])
                    if content and isinstance(content, list):
                        text = content[0].get("text", "")
                        if text:
                            return text
            except Exception:
                continue

        return None

    def diagnose_and_propose_fix(
        self,
        project_name: str,
        repo_url: str,
        failed_job_name: str,
        error_trace: str,
        gitlab_ci: str = "",
        docker_compose: str = "",
        env_content: str = "",
    ) -> Optional[dict[str, Any]]:
        """
        Sends the pipeline failure context (.gitlab-ci.yml, docker-compose.yml, .env, and job logs)
        to the AI and returns structured diagnosis and fix actions.
        """
        system_prompt = (
            "You are an expert DevOps engineer specializing in GitLab CI/CD, Docker, and Linux server deployment.\n"
            "Analyze deployment pipeline failures and provide an accurate fix.\n"
            "Your output MUST be a single valid JSON object, with no preamble or trailing explanation outside the JSON."
        )

        prompt = f"""We are deploying a project named '{project_name}' (repository: {repo_url}).
The GitLab CI job '{failed_job_name}' failed with the following error log (last lines):
================ ERROR TRACE ================
{error_trace}
=============================================

Here is the current .gitlab-ci.yml:
================ .gitlab-ci.yml ================
{gitlab_ci}
================================================

Here is the current /srv/{project_name}/docker-compose.yml on the deployment server:
================ docker-compose.yml ================
{docker_compose}
====================================================

Here is the current /srv/{project_name}/.env on the deployment server (redacted secrets):
================ .env ================
{env_content}
======================================

Diagnose why the job failed and determine how to fix it.
A fix might require:
- Changing .gitlab-ci.yml (e.g. runner tags, script commands, docker compose vs docker-compose fallback, login flags).
- Changing files on the server (e.g. docker-compose.yml syntax/port/image, .env variables, permissions, or executing bash commands).
- Both.

Respond ONLY with a JSON object in this exact schema:
{{
  "diagnosis": "Concise explanation in Persian (or English) of what caused the failure",
  "fix_location": "SERVER" | "GITLAB_CI" | "BOTH",
  "server_changes": {{
    "docker_compose_changed": true | false,
    "updated_docker_compose": "full updated docker-compose.yml if changed, else null",
    "env_changed": true | false,
    "updated_env": "full updated .env if changed, else null",
    "commands": ["command1", "command2"]
  }},
  "gitlab_ci_changes": {{
    "changed": true | false,
    "updated_gitlab_ci": "full updated .gitlab-ci.yml if changed, else null"
  }}
}}
"""

        raw_response = self._call_llm(prompt, system_prompt=system_prompt)
        if not raw_response:
            return None

        clean_json = raw_response.strip()
        if clean_json.startswith("```"):
            lines = clean_json.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            clean_json = "\n".join(lines).strip()

        try:
            return json.loads(clean_json)
        except Exception:
            # Fallback: find json substring between first { and last }
            first_idx = clean_json.find("{")
            last_idx = clean_json.rfind("}")
            if first_idx != -1 and last_idx != -1 and last_idx > first_idx:
                try:
                    return json.loads(clean_json[first_idx:last_idx+1])
                except Exception:
                    pass
        return None

    def calculate_heuristic_priority(self, task: dict[str, Any]) -> dict[str, Any]:
        """
        Intelligent scoring engine that calculates priority score (1-100),
        urgency level, and Persian reasoning based on SRE domain heuristics.
        """
        import time
        score = 40  # baseline
        reasons = []
        name = (task.get("name") or "").lower()
        desc = (task.get("description") or "").lower()
        form_name = (task.get("form_name") or task.get("list_name") or "").lower()
        status = (task.get("status") or "").lower()
        environment = (task.get("environment") or "").lower()
        priority_raw = str(task.get("priority") or "").lower()

        # 1. ClickUp native priority if provided
        if priority_raw in ["urgent", "1"]:
            score += 25
            reasons.append("اولویت کلیک‌آپ: اضطراری")
        elif priority_raw in ["high", "2"]:
            score += 15
            reasons.append("اولویت کلیک‌آپ: بالا")
        elif priority_raw in ["normal", "3"]:
            score += 5
        elif priority_raw in ["low", "4"]:
            score -= 10

        # 2. Form type / Category impact
        if any(w in form_name for w in ["issue", "خطا", "مشکل", "باگ", "incident"]):
            score += 25
            reasons.append("نوع درخواست: گزارش خطا و مشکل (Issue)")
        elif any(w in form_name for w in ["deploy", "دیپلوی"]):
            score += 20
            reasons.append("نوع درخواست: استقرار سرویس (Deploy)")
        elif any(w in form_name for w in ["change", "تغییر"]):
            score += 12
            reasons.append("نوع درخواست: تغییرات زیرساخت (Change)")
        elif any(w in form_name for w in ["access", "دسترسی"]):
            score += 10
            reasons.append("نوع درخواست: دسترسی و مجوزها (Access)")

        # 3. Environment sensitivity
        if any(w in environment for w in ["prod", "production", "لایو", "اصلی"]) or "prod" in name or "پروداکشن" in name:
            score += 20
            reasons.append("محیط پروداکشن (حساسیت عملیاتی بالا)")
        elif any(w in environment for w in ["main", "stage", "staging"]):
            score += 10
            reasons.append("محیط اصلی / استیج")

        # 4. Critical keywords in title or description
        critical_keywords = [
            ("down", 25, "اختلال یا قطعی کامل سرویس"),
            ("قطع", 25, "اختلال یا قطعی سرویس"),
            ("فوری", 20, "دارای برچسب فوریت"),
            ("urgent", 20, "دارای فوریت بالا"),
            ("critical", 25, "وضعیت بحرانی"),
            ("باگ", 15, "گزارش نقص نرم‌افزاری"),
            ("پرداخت", 20, "زیرساخت درگاه پرداخت"),
            ("بانک", 15, "ارتباط با شبکه بانکی"),
            ("دیتابیس", 15, "دیتابیس و پایگاه داده"),
            ("postgres", 15, "سرویس دیتابیس PostgreSQL"),
            ("redis", 12, "ردیس / کش"),
            ("security", 20, "مسئله امنیتی"),
            ("امنیت", 20, "مسئله امنیتی"),
            ("certificate", 15, "سرتیفیکیت SSL"),
            ("ssl", 15, "گواهی SSL"),
            ("auth", 15, "سرویس احراز هویت"),
            ("fail", 15, "خطای عملیاتی پایپ‌لاین"),
        ]
        matched_kw_reasons = []
        for kw, pts, label in critical_keywords:
            if kw in name or kw in desc[:300]:
                score += pts
                if label not in matched_kw_reasons:
                    matched_kw_reasons.append(label)
                if len(matched_kw_reasons) >= 2:
                    break
        if matched_kw_reasons:
            reasons.extend(matched_kw_reasons)

        # 5. Status weight
        if status in ["in progress", "در حال انجام"]:
            score += 10
            reasons.append("در حال انجام فعال")
        elif status in ["ready to test", "تست", "waiting for customer"]:
            score += 8
            reasons.append(f"وضعیت نیازمند پیگیری ({status})")
        elif status in ["new", "open"]:
            score += 5

        # 6. Task Age & Staleness Evaluation
        # Differentiates between creation age, update recency, and fresh comment activity
        date_created_ms = task.get("date_created")
        date_updated_ms = task.get("date_updated")
        latest_comment_ms = task.get("latest_comment_date")

        # Fallback if comments list exists in task dict
        if not latest_comment_ms and isinstance(task.get("comments"), list) and task["comments"]:
            valid_c_dates = [int(c["date"]) for c in task["comments"] if c.get("date")]
            if valid_c_dates:
                latest_comment_ms = max(valid_c_dates)

        now_ts = time.time()
        days_since_created = None
        if date_created_ms:
            try:
                days_since_created = (now_ts - (int(date_created_ms) / 1000.0)) / (3600.0 * 24.0)
            except Exception:
                pass

        days_since_updated = None
        if date_updated_ms:
            try:
                days_since_updated = (now_ts - (int(date_updated_ms) / 1000.0)) / (3600.0 * 24.0)
            except Exception:
                pass

        days_since_comment = None
        if latest_comment_ms:
            try:
                days_since_comment = (now_ts - (int(latest_comment_ms) / 1000.0)) / (3600.0 * 24.0)
            except Exception:
                pass

        # Check if task is old (created more than 20 days ago)
        is_old_task = days_since_created is not None and days_since_created > 20
        # Check if there is fresh comment activity (comment within last 7 days)
        has_recent_comment = days_since_comment is not None and days_since_comment <= 7
        # Severe staleness: no update or comment for a very long time
        effective_activity_ms = latest_comment_ms or date_updated_ms or date_created_ms
        effective_activity_days = (now_ts - (int(effective_activity_ms) / 1000.0)) / (3600.0 * 24.0) if effective_activity_ms else 0

        is_severely_stale = False
        is_old_without_new_comments = False

        if is_old_task:
            if has_recent_comment:
                # Old task that is currently active because of new comments
                score += 10
                reasons.append(f"تسک با سابقه ({int(days_since_created)} روز) همراه با کامنت و پیگیری جدید")
            else:
                # Old task with NO new comments: minor updates do not make it critical/urgent!
                is_old_without_new_comments = True
                if days_since_created > 90:
                    score -= 30
                    reasons.append(f"عمر تسک بالا ({int(days_since_created)} روز پیش) بدون کامنت جدید")
                elif days_since_created > 45:
                    score -= 20
                    reasons.append(f"تسک قدیمی ({int(days_since_created)} روز پیش) بدون کامنت جدید")
                else:
                    score -= 15
                    reasons.append(f"تسک بیش از ۲ هفته پیش ایجاد شده ({int(days_since_created)} روز) بدون کامنت جدید")
        else:
            # Newer task
            if days_since_created is not None and days_since_created <= 3:
                score += 10
                reasons.append("تسک تازه ثبت‌شده (زیر ۳ روز)")
            elif days_since_updated is not None and days_since_updated <= 3:
                score += 5
                reasons.append("به‌روزرسانی اخیر (فعال)")

        # Penalize if entirely inactive without any update or comment
        if effective_activity_days > 180:
            score -= 40
            is_severely_stale = True
            reasons.append("راکد بسیار قدیمی (بیش از ۶ ماه بدون تغییر وضعیت یا کامنت)")
        elif effective_activity_days > 60:
            score -= 30
            is_severely_stale = True
            reasons.append("راکد (بیش از ۲ ماه بدون تغییر وضعیت یا کامنت)")
        elif effective_activity_days > 30:
            score -= 15
            reasons.append("کم‌اثر (بیش از ۱ ماه بدون فعالیت)")

        # 7. Due date / Deadlines
        due_date = task.get("due_date")
        if due_date:
            try:
                due_ts = int(due_date) / 1000.0
                diff_hours = (due_ts - time.time()) / 3600.0
                if diff_hours < 0:
                    # Overdue: if very old, it is just abandoned, else urgent
                    if abs(diff_hours) > 24 * 14:
                        score -= 15
                        reasons.append("ددلاین بسیار منقضی شده و پیگیری نشده")
                    else:
                        score += 15
                        reasons.append("مهلت تحویل سپری شده (Overdue)")
                elif diff_hours < 24:
                    score += 15
                    reasons.append("مهلت تحویل تا کمتر از ۲۴ ساعت")
                elif diff_hours < 72:
                    score += 8
                    reasons.append("نزدیک به زمان سررسید")
            except Exception:
                pass

        # Cap scores based on staleness and age rules
        if is_severely_stale:
            score = min(40, score)
        elif is_old_without_new_comments:
            # Old tasks without recent comments can NEVER be CRITICAL or HIGH; capped at 60 (MEDIUM)
            score = min(60, score)

        # Normalize score between 10 and 99
        score = max(10, min(99, score))

        # Urgency level
        if score >= 80:
            urgency_level = "CRITICAL"
            suggested_action = "بررسی و اقدام فوری بدون معطلی"
        elif score >= 65:
            urgency_level = "HIGH"
            suggested_action = "اقدام در اولویت نخست کاری امروز"
        elif score >= 45:
            urgency_level = "MEDIUM"
            suggested_action = "رسیدگی در روال کاری معمول"
        else:
            urgency_level = "LOW"
            suggested_action = "در صف و پیگیری بر اساس زمان‌بندی (یا بررسی جهت بایگانی)"

        ai_reasoning = "؛ ".join(reasons) if reasons else "تسک در روال معمول مطابق فرم ثبت‌شده"

        return {
            "priority_score": score,
            "urgency_level": urgency_level,
            "ai_reasoning": ai_reasoning,
            "suggested_action": suggested_action
        }

    def prioritize_tasks(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Prioritizes a list of tasks using dual-engine approach:
        1. Evaluates all tasks via SRE heuristic engine.
        2. Tries LLM refinement for top tasks if endpoint is accessible.
        Returns the tasks array sorted by priority_score descending.
        """
        if not tasks:
            return []

        # 1. Compute baseline priority for each task using heuristic engine
        for t in tasks:
            h = self.calculate_heuristic_priority(t)
            t["priority_score"] = h["priority_score"]
            t["urgency_level"] = h["urgency_level"]
            t["ai_reasoning"] = h["ai_reasoning"]
            t["suggested_action"] = h["suggested_action"]

        # 2. Attempt LLM refinement if reachable
        if self.base_url and self.auth_token:
            try:
                compact_tasks = []
                now_ts = time.time()
                for t in tasks[:15]:
                    date_cr = t.get("date_created")
                    date_up = t.get("date_updated")
                    latest_c = t.get("latest_comment_date")
                    days_created = round((now_ts - int(date_cr)/1000)/(3600*24), 1) if date_cr else None
                    days_updated = round((now_ts - int(date_up)/1000)/(3600*24), 1) if date_up else None
                    days_comment = round((now_ts - int(latest_c)/1000)/(3600*24), 1) if latest_c else None
                    compact_tasks.append({
                        "id": str(t["id"]),
                        "name": t.get("name", ""),
                        "form": t.get("form_name", ""),
                        "status": t.get("status", ""),
                        "env": t.get("environment", ""),
                        "days_since_created": days_created,
                        "days_since_updated": days_updated,
                        "days_since_latest_comment": days_comment,
                        "comments_count": t.get("comments_count", 0)
                    })
                sys_prompt = (
                    "You are an SRE AI priority expert. Rules: "
                    "1. If a task was created a long time ago (high days_since_created, e.g. >20-30 days), "
                    "a recent minor update (low days_since_updated) DOES NOT make it urgent or critical. "
                    "2. Old tasks should only have high priority IF they have a recent comment (days_since_latest_comment <= 7). "
                    "Otherwise, cap old tasks at MEDIUM or LOW. "
                    "Output JSON array of objects with id, priority_score (1-100), urgency_level (CRITICAL/HIGH/MEDIUM/LOW), and ai_reasoning in Persian."
                )
                user_prompt = f"Adjust priorities for these tasks:\n{json.dumps(compact_tasks, ensure_ascii=False)}"
                llm_resp = self._call_llm(user_prompt, system_prompt=sys_prompt)
                if llm_resp:
                    clean = llm_resp.strip()
                    if clean.startswith("```"):
                        lines = clean.split("\n")
                        if lines[0].startswith("```"): lines = lines[1:]
                        if lines and lines[-1].strip().startswith("```"): lines = lines[:-1]
                        clean = "\n".join(lines).strip()
                    parsed = json.loads(clean)
                    if isinstance(parsed, list):
                        p_map = {str(item.get("id")): item for item in parsed if isinstance(item, dict) and "id" in item}
                        for t in tasks:
                            tid = str(t["id"])
                            if tid in p_map:
                                t["priority_score"] = p_map[tid].get("priority_score", t["priority_score"])
                                t["urgency_level"] = p_map[tid].get("urgency_level", t["urgency_level"])
                                if p_map[tid].get("ai_reasoning"):
                                    t["ai_reasoning"] = p_map[tid]["ai_reasoning"]
            except Exception:
                pass

        # Sort tasks descending by priority score
        tasks.sort(key=lambda x: x.get("priority_score", 0), reverse=True)
        return tasks

