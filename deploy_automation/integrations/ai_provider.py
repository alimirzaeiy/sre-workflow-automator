import os
import json
import urllib.request
import urllib.error
from typing import Optional, Any
from deploy_automation.config import settings

class AIServiceProvider:
    """
    Unified AI Service Provider that supports:
    1. OpenAI-compatible API (e.g. OpenAI, OpenRouter, vLLM, Ollama, Local AI proxy)
    2. Anthropic-compatible API (e.g. Claude, conduit)
    Configurable via environment variables.
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        openai_base_url: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        openai_model: Optional[str] = None,
        anthropic_base_url: Optional[str] = None,
        anthropic_api_key: Optional[str] = None,
        anthropic_model: Optional[str] = None,
        proxy: Optional[str] = None
    ):
        self.provider = (provider or os.getenv("AI_PROVIDER") or getattr(settings, "AI_PROVIDER", "openai")).lower()
        self.openai_base_url = (openai_base_url or os.getenv("OPENAI_BASE_URL") or getattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY") or getattr(settings, "OPENAI_API_KEY", "")
        self.openai_model = openai_model or os.getenv("OPENAI_MODEL") or getattr(settings, "OPENAI_MODEL", "gpt-4o")

        self.anthropic_base_url = (anthropic_base_url or os.getenv("ANTHROPIC_BASE_URL") or getattr(settings, "ANTHROPIC_BASE_URL", "https://api.anthropic.com")).rstrip("/")
        self.anthropic_api_key = anthropic_api_key or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY") or getattr(settings, "ANTHROPIC_API_KEY", "")
        self.anthropic_model = anthropic_model or os.getenv("ANTHROPIC_MODEL") or getattr(settings, "ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

        self.proxy = proxy or os.getenv("PROXY") or getattr(settings, "PROXY", None) or os.getenv("HTTPS_PROXY") or getattr(settings, "HTTPS_PROXY", None) or os.getenv("HTTP_PROXY") or getattr(settings, "HTTP_PROXY", None) or os.getenv("ALL_PROXY") or getattr(settings, "ALL_PROXY", None)
        self._opener = None
        if self.proxy:
            proxy_handler = urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})
            self._opener = urllib.request.build_opener(proxy_handler)

    def _open_url(self, req: urllib.request.Request, timeout: int = 30):
        if self._opener:
            return self._opener.open(req, timeout=timeout)
        return urllib.request.urlopen(req, timeout=timeout)

    def call_llm(self, prompt: str, system_prompt: Optional[str] = None) -> Optional[str]:
        """Routes call based on configured AI provider with fallback."""
        if self.provider == "anthropic" and self.anthropic_api_key:
            res = self._call_anthropic(prompt, system_prompt)
            if res:
                return res
        
        # Default or fallback to OpenAI format
        if self.openai_api_key or "localhost" in self.openai_base_url or "127.0.0.1" in self.openai_base_url:
            res = self._call_openai(prompt, system_prompt)
            if res:
                return res

        # Try Anthropic if OpenAI was primary but failed or not configured
        if self.anthropic_api_key:
            return self._call_anthropic(prompt, system_prompt)

        return None

    def _call_openai(self, prompt: str, system_prompt: Optional[str] = None) -> Optional[str]:
        url = f"{self.openai_base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.openai_api_key}"
        }
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.openai_model,
            "messages": messages,
            "temperature": 0.2
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with self._open_url(req, timeout=30) as resp:
                resp_json = json.loads(resp.read().decode("utf-8"))
                choices = resp_json.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip()
        except Exception as e:
            # logger fallback or ignore
            pass
        return None

    def _call_anthropic(self, prompt: str, system_prompt: Optional[str] = None) -> Optional[str]:
        url = f"{self.anthropic_base_url}/v1/messages"
        headers = {
            "x-api-key": self.anthropic_api_key,
            "Authorization": f"Bearer {self.anthropic_api_key}",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }
        payload: dict[str, Any] = {
            "model": self.anthropic_model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}]
        }
        if system_prompt:
            payload["system"] = system_prompt

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with self._open_url(req, timeout=30) as resp:
                resp_json = json.loads(resp.read().decode("utf-8"))
                content = resp_json.get("content", [])
                if content and isinstance(content, list):
                    return content[0].get("text", "").strip()
        except Exception:
            pass
        return None

    def estimate_workload_and_pick_assignee(
        self,
        new_task: dict[str, Any],
        members_workloads: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Uses LLM to analyze the active in-progress tasks of each SRE member,
        estimates their current busy-ness level, and selects the least busy member.
        Falls back to minimum task count heuristic if AI is unreachable.
        """
        if not members_workloads:
            return {"assignee_id": None, "assignee_name": None, "reason": "No members available"}

        if len(members_workloads) == 1:
            return {
                "assignee_id": members_workloads[0]["id"],
                "assignee_name": members_workloads[0]["name"],
                "reason": "Only one team member configured."
            }

        prompt = f"""You are the SRE Team Manager AI.
Your job is to assign a new incoming task to the SRE team member who currently has the least workload.

New Task:
- Title: {new_task.get('name', 'Untitled')}
- Description: {new_task.get('description', '')[:500]}
- Environment: {new_task.get('environment', 'Unknown')}
- Repo: {new_task.get('repo_url', 'None')}

Team Members and their current IN PROGRESS tasks:
"""
        for m in members_workloads:
            prompt += f"\nMember: {m['name']} (ClickUp User ID: {m['id']}):\n"
            in_prog = m.get("in_progress_tasks", [])
            if not in_prog:
                prompt += "  - No tasks currently in progress (Free)\n"
            else:
                for t in in_prog:
                    prompt += f"  - Task '{t.get('name', 'Untitled')}' [Status: {t.get('status')}]\n"

        prompt += """
Output your decision strictly as a valid JSON object with the following schema:
{
  "selected_user_id": <int or str of ClickUp User ID>,
  "selected_user_name": "<name>",
  "reason": "<persian concise reason for assignment>"
}
Do not include markdown blocks or any other text outside the JSON.
"""

        system_prompt = "You are an expert SRE engineering manager assistant. Evaluate technical task complexity and team busyness accurately. Answer only in JSON."
        llm_response = self.call_llm(prompt, system_prompt)

        if llm_response:
            try:
                cleaned = llm_response.strip()
                if cleaned.startswith("```"):
                    lines = cleaned.splitlines()
                    cleaned = "\n".join([line for line in lines if not line.strip().startswith("```")])
                parsed = json.loads(cleaned)
                sel_id = parsed.get("selected_user_id")
                sel_name = parsed.get("selected_user_name")
                reason = parsed.get("reason", "تخصیص بر اساس کمترین حجم کاری توسط هوش مصنوعی")
                
                # Verify sel_id exists in members
                for m in members_workloads:
                    if str(m["id"]) == str(sel_id):
                        return {
                            "assignee_id": m["id"],
                            "assignee_name": m["name"],
                            "reason": reason
                        }
            except Exception:
                pass

        # Heuristic Fallback: pick member with lowest count of in_progress tasks
        sorted_members = sorted(members_workloads, key=lambda m: len(m.get("in_progress_tasks", [])))
        best = sorted_members[0]
        cnt = len(best.get("in_progress_tasks", []))
        return {
            "assignee_id": best["id"],
            "assignee_name": best["name"],
            "reason": f"تخصیص خودکار (دارای {cnt} تسک در حال انجام، کمترین بار کاری)"
        }

    def generate_deploy_requirements_comment(
        self,
        project_name: str,
        failed_checks: list[dict[str, Any]]
    ) -> str:
        """
        Generates a professional Persian comment explaining missing deployment requirements
        based on the organizational 9-point deployment readiness standards.
        """
        if not failed_checks:
            return "کلیه الزامات استقرار مطابق با استانداردهای تعریف‌شده بررسی و تایید شد."

        prompt = f"""You are an SRE Manager AI assistant.
A deployment task was submitted for microservice/project '{project_name}'.
During our automated audit against the project deployment readiness standards (الزامات استقرار), the following issues were found:

Issues detected:
"""
        for fc in failed_checks:
            prompt += f"- قاعده {fc.get('rule_id')}: {fc.get('title')}\n  توضیحات: {fc.get('details')}\n  راهکار اصلاح: {fc.get('remediation')}\n"

        prompt += """
Please draft a polite, highly professional and clear Persian comment to the developer/reporter.
Start with a respectful greeting.
List each missing requirement with clear instructions on how to resolve it according to organizational standards.
End with asking them to review and fix the items, and let us know once resolved.
Format in clean Markdown. Output only the comment text.
"""
        system_prompt = "You are a professional SRE lead providing constructive code review feedback in Persian."
        res = self.call_llm(prompt, system_prompt)
        if res:
            return res.strip()

        # Fallback template
        lines = [
            "با سلام و احترام،",
            f"بررسی الزامات استقرار برای پروژه {project_name} انجام شد و موارد زیر نیاز به بررسی و اصلاح دارند:\n"
        ]
        for fc in failed_checks:
            lines.append(f"❌ **{fc.get('title')}**:")
            lines.append(f"   {fc.get('details')}")
            if fc.get('remediation'):
                lines.append(f"   💡 راهکار: {fc.get('remediation')}")
            lines.append("")

        lines.append("لطفاً پس از رفع موارد فوق، جهت ادامه فرآیند دیپلوی اطلاع دهید. با تشکر 🙏")
        return "\n".join(lines)
