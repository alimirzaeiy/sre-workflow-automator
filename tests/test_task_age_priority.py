import time
import pytest
from deploy_automation.integrations.ai_service import AIService


def test_old_task_without_comment_is_not_critical():
    ai = AIService()
    now_ms = int(time.time() * 1000)

    # Old task created 70 days ago, with minor update 1 day ago, no comments
    task = {
        "id": "test_old",
        "name": "دسترسی به سیستم آلرت",
        "environment": "production",
        "form_name": "Access",
        "status": "new",
        "date_created": str(now_ms - (70 * 86400 * 1000)),
        "date_updated": str(now_ms - (1 * 86400 * 1000)),
        "latest_comment_date": None,
        "comments": []
    }

    result = ai.calculate_heuristic_priority(task)
    assert result["urgency_level"] in ["LOW", "MEDIUM"]
    assert result["priority_score"] <= 60
    assert "بدون کامنت جدید" in result["ai_reasoning"]


def test_old_task_with_recent_comment_is_elevated():
    ai = AIService()
    now_ms = int(time.time() * 1000)

    # Old task created 70 days ago, with a fresh comment 2 days ago
    task = {
        "id": "test_old_commented",
        "name": "دسترسی به سیستم آلرت",
        "environment": "production",
        "form_name": "Access",
        "status": "new",
        "date_created": str(now_ms - (70 * 86400 * 1000)),
        "date_updated": str(now_ms - (1 * 86400 * 1000)),
        "latest_comment_date": str(now_ms - (2 * 86400 * 1000)),
        "comments": [{"date": str(now_ms - (2 * 86400 * 1000)), "comment_text": "پیگیری فوری"}]
    }

    result = ai.calculate_heuristic_priority(task)
    assert result["urgency_level"] in ["HIGH", "CRITICAL"]
    assert result["priority_score"] >= 75
    assert "کامنت و پیگیری جدید" in result["ai_reasoning"]


def test_new_task_maintains_fresh_urgency():
    ai = AIService()
    now_ms = int(time.time() * 1000)

    # New task created 1 day ago
    task = {
        "id": "test_new",
        "name": "خطای دیتابیس درگاه پرداخت",
        "environment": "production",
        "form_name": "Issue",
        "status": "new",
        "date_created": str(now_ms - (1 * 86400 * 1000)),
        "date_updated": str(now_ms - (1 * 86400 * 1000)),
        "latest_comment_date": None
    }

    result = ai.calculate_heuristic_priority(task)
    assert result["urgency_level"] == "CRITICAL"
    assert result["priority_score"] >= 80
    assert "تسک تازه ثبت‌شده" in result["ai_reasoning"]
