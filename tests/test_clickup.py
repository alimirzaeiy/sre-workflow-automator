import pytest
from deploy_automation.integrations.clickup_service import ClickUpService
from deploy_automation.utils.text_helpers import format_clickup_final_comment, format_telegram_review_message
from deploy_automation.models import ProjectReport, CheckItemResult, CheckStatus


def test_recheck_comment_detection():
    service = ClickUpService(api_token="test")
    assert service.is_recheck_comment("سلام، الزامات دیپلوی رعایت شد لطفا بررسی کنید") is True
    assert service.is_recheck_comment("موارد مطرح شده برطرف شد") is True
    assert service.is_recheck_comment("برطرف شد") is True
    assert service.is_recheck_comment("این یک پیام عادی است و ربطی ندارد") is False


def test_format_clickup_final_comment():
    body = "- مستند معماری کلان یافت نشد\n- استیج SonarQube وجود ندارد"
    result = format_clickup_final_comment(body, reporter_username="ali")
    assert "@ali" in result
    assert "الزامات دیپلوی به طور کامل رعایت نشده است:" in result
    assert "مستند معماری کلان" in result


def test_format_telegram_review_message():
    report = ProjectReport(
        project_name="infra/auth-service",
        repo_url="https://gitlab.example.com/infra/auth-service.git",
        has_maintainer_access=True,
        all_passed=False,
        checks=[
            CheckItemResult(
                rule_id=1,
                title="مستند معماری کلان در doc/",
                status=CheckStatus.FAILED,
                passed=False,
                details="یافت نشد",
                remediation="ایجاد doc/architecture.md"
            )
        ],
        summary_text="❌ مستند معماری کلان: یافت نشد",
        comment_text="- مستند معماری کلان: یافت نشد (راهکار: ایجاد doc/architecture.md)"
    )

    msg = format_telegram_review_message(report)
    assert "الزامات دیپلوی برای پروژه infra/auth-service چک شد" in msg
    assert "آیا کامنت شود؟" in msg
    assert "❌ مستند معماری کلان" in msg
