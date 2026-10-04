import os
from typing import Optional

def load_dotenv_fallback(env_path: str = ".env"):
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k:
                            os.environ[k] = v
        except Exception:
            pass

load_dotenv_fallback()

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    from pydantic import Field

    class Settings(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            extra="ignore"
        )
        APP_NAME: str = "Deploy Automation Service"
        HOST: str = "0.0.0.0"
        PORT: int = 8086
        DEBUG: bool = False
        LOG_LEVEL: str = "INFO"
        DATABASE_URL: str = "sqlite+aiosqlite:///./data/automation.db"
        CLICKUP_API_TOKEN: str = Field(default="", description="ClickUp Personal API Token (pk_...)")
        CLICKUP_API_BASE: str = "https://api.clickup.com/api/v2"
        CLICKUP_TEAM_ID: Optional[str] = Field(default=None, description="ClickUp Team/Workspace ID")
        CLICKUP_SRE_SPACE_NAME: str = "SRE"
        CLICKUP_DEPLOY_FORM_NAME: str = "deploy"
        CLICKUP_REPO_FIELD_NAME: str = "repo"
        CLICKUP_WAITING_STATUS: str = "waiting for customer"
        CLICKUP_WEBHOOK_SECRET: Optional[str] = None
        GITLAB_URL: str = Field(default="https://gitlab.example.com", description="GitLab instance URL")
        GITLAB_TOKEN: str = Field(default="", description="GitLab Personal Access Token")
        GITLAB_MIN_ACCESS_LEVEL: int = 40
        TELEGRAM_BOT_TOKEN: str = Field(default="", description="Telegram Bot API Token")
        TELEGRAM_ADMIN_CHAT_ID: int = Field(default=0, description="Telegram Chat ID of SRE / Reviewer")

        # Unified Proxy Configuration (used across Telegram, ClickUp, and AI)
        PROXY: Optional[str] = Field(default=None, description="Unified proxy URL for ClickUp, Telegram, and AI (e.g. http://127.0.0.1:1080 or socks5://127.0.0.1:1080)")
        HTTP_PROXY: Optional[str] = Field(default=None, description="Fallback HTTP proxy")
        HTTPS_PROXY: Optional[str] = Field(default=None, description="Fallback HTTPS proxy")
        ALL_PROXY: Optional[str] = Field(default=None, description="Fallback general proxy")

        # AI Provider Settings (OpenAI / Anthropic compatible)
        AI_PROVIDER: str = "openai"
        OPENAI_BASE_URL: str = "https://api.openai.com/v1"
        OPENAI_API_KEY: str = ""
        OPENAI_MODEL: str = "gpt-4o"
        ANTHROPIC_BASE_URL: str = "https://api.anthropic.com"
        ANTHROPIC_API_KEY: str = ""
        ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20241022"

        # Decision / Assignment Mode: 'rule_engine' (default fast/deterministic), 'ai' (LLM), or 'hybrid'
        DECISION_MODE: str = "rule_engine"
        ASSIGNMENT_RULES_PATH: Optional[str] = None

        # SRE Team & Group Settings
        TELEGRAM_TEAM_GROUP_ID: int = Field(default=0, description="Telegram Supergroup ID with team topics")
        SRE_MANAGER_NAME: str = "SRE Manager"
        SRE_MANAGER_CLICKUP_ID: Optional[int] = None
        SRE_MANAGER_TELEGRAM_CHAT_ID: int = 0
        SRE_MANAGER_TELEGRAM_TOPIC_ID: Optional[int] = None

        # SRE Team members JSON format:
        # [{"name": "Ali", "clickup_id": 101, "telegram_id": 111111, "topic_id": 2}, ...]
        SRE_TEAM_MEMBERS: str = "[]"

        RECHECK_KEYWORDS: list = [
            "الزامات دیپلوی رعایت شد",
            "موارد مطرح شده برطرف شد",
            "موارد برطرف شد",
            "الزامات برطرف شد",
            "برطرف شد",
            "رعایت شد",
            "deploy readiness fixed",
            "requirements fixed",
            "fixes applied"
        ]

    settings = Settings()

except ImportError:
    class FallbackSettings:
        APP_NAME: str = os.getenv("APP_NAME", "Deploy Automation Service")
        HOST: str = os.getenv("HOST", "0.0.0.0")
        PORT: int = int(os.getenv("PORT", "8086"))
        DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
        LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
        DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/automation.db")
        CLICKUP_API_TOKEN: str = os.getenv("CLICKUP_API_TOKEN", "")
        CLICKUP_API_BASE: str = os.getenv("CLICKUP_API_BASE", "https://api.clickup.com/api/v2")
        CLICKUP_TEAM_ID: Optional[str] = os.getenv("CLICKUP_TEAM_ID", None)
        CLICKUP_SRE_SPACE_NAME: str = os.getenv("CLICKUP_SRE_SPACE_NAME", "SRE")
        CLICKUP_DEPLOY_FORM_NAME: str = os.getenv("CLICKUP_DEPLOY_FORM_NAME", "deploy")
        CLICKUP_REPO_FIELD_NAME: str = os.getenv("CLICKUP_REPO_FIELD_NAME", "repo")
        CLICKUP_WAITING_STATUS: str = os.getenv("CLICKUP_WAITING_STATUS", "waiting for customer")
        CLICKUP_WEBHOOK_SECRET: Optional[str] = os.getenv("CLICKUP_WEBHOOK_SECRET", None)
        GITLAB_URL: str = os.getenv("GITLAB_URL", "https://gitlab.example.com")
        GITLAB_TOKEN: str = os.getenv("GITLAB_TOKEN", "")
        GITLAB_MIN_ACCESS_LEVEL: int = int(os.getenv("GITLAB_MIN_ACCESS_LEVEL", "40"))
        TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
        TELEGRAM_ADMIN_CHAT_ID: int = int(os.getenv("TELEGRAM_ADMIN_CHAT_ID", "0"))

        # Unified Proxy Configuration (used across Telegram, ClickUp, and AI)
        PROXY: Optional[str] = os.getenv("PROXY", None)
        HTTP_PROXY: Optional[str] = os.getenv("HTTP_PROXY", None)
        HTTPS_PROXY: Optional[str] = os.getenv("HTTPS_PROXY", None)
        ALL_PROXY: Optional[str] = os.getenv("ALL_PROXY", None)

        AI_PROVIDER: str = os.getenv("AI_PROVIDER", "openai")
        OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
        OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")
        ANTHROPIC_BASE_URL: str = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", os.getenv("ANTHROPIC_AUTH_TOKEN", ""))
        ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
        DECISION_MODE: str = os.getenv("DECISION_MODE", "rule_engine")
        ASSIGNMENT_RULES_PATH: Optional[str] = os.getenv("ASSIGNMENT_RULES_PATH", None)

        TELEGRAM_TEAM_GROUP_ID: int = int(os.getenv("TELEGRAM_TEAM_GROUP_ID", "0"))
        SRE_MANAGER_NAME: str = os.getenv("SRE_MANAGER_NAME", "SRE Manager")
        SRE_MANAGER_CLICKUP_ID: Optional[int] = int(os.getenv("SRE_MANAGER_CLICKUP_ID")) if os.getenv("SRE_MANAGER_CLICKUP_ID") else None
        SRE_MANAGER_TELEGRAM_CHAT_ID: int = int(os.getenv("SRE_MANAGER_TELEGRAM_CHAT_ID", "0"))
        SRE_MANAGER_TELEGRAM_TOPIC_ID: Optional[int] = int(os.getenv("SRE_MANAGER_TELEGRAM_TOPIC_ID")) if os.getenv("SRE_MANAGER_TELEGRAM_TOPIC_ID") else None
        SRE_TEAM_MEMBERS: str = os.getenv("SRE_TEAM_MEMBERS", "[]")

        RECHECK_KEYWORDS: list = [
            "الزامات دیپلوی رعایت شد",
            "موارد مطرح شده برطرف شد",
            "موارد برطرف شد",
            "الزامات برطرف شد",
            "برطرف شد",
            "رعایت شد",
            "deploy readiness fixed",
            "requirements fixed",
            "fixes applied"
        ]

    settings = FallbackSettings()
