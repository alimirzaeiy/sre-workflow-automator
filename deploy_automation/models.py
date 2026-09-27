from enum import Enum
from typing import Optional, Any
from datetime import datetime

try:
    from pydantic import BaseModel, Field
except ImportError:
    # Standard python dataclass fallback
    from dataclasses import dataclass, field
    class BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
    def Field(default=None, default_factory=None, **kwargs):
        return default if default is not None else (default_factory() if default_factory else None)


class CheckStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    WARNING = "WARNING"
    SKIPPED = "SKIPPED"


class CheckItemResult(BaseModel):
    rule_id: int = 0
    title: str = ""
    status: CheckStatus = CheckStatus.PASSED
    passed: bool = True
    details: str = ""
    remediation: Optional[str] = None


class ProjectReport(BaseModel):
    project_name: str = ""
    project_id: Optional[int] = None
    repo_url: str = ""
    branch: str = "main"
    has_maintainer_access: bool = False
    all_passed: bool = False
    checks: list = None
    summary_text: str = ""
    comment_text: str = ""
    created_at: Any = None


class ClickUpTaskInfo(BaseModel):
    task_id: str = ""
    task_name: str = ""
    repo_url: Optional[str] = None
    reporter_id: Optional[int] = None
    reporter_username: Optional[str] = None
    reporter_email: Optional[str] = None
    status: Optional[str] = None
    space_name: Optional[str] = None
    list_name: Optional[str] = None
    service_needs: list = None
    environment: Optional[str] = None
    custom_fields: dict = None
    assignees: list = None
    envs: Optional[str] = None  # Raw text content of the "Envs" custom field


class ReviewAction(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    EDIT = "EDIT"
    HANDOFF_LAPTOP = "HANDOFF_LAPTOP"


class PendingReviewSession(BaseModel):
    id: str = ""
    task_id: str = ""
    project_name: str = ""
    repo_url: str = ""
    reporter_id: Optional[int] = None
    default_comment: str = ""
    current_comment: str = ""
    status: str = "PENDING"
    telegram_message_id: Optional[int] = None
    service_needs: list = None
    environment: Optional[str] = None
    created_at: Any = None
