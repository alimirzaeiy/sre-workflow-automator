from typing import Optional
from deploy_automation.models import ProjectReport


def format_telegram_review_message(report: ProjectReport) -> str:
    """
    Formats the interactive message sent to Telegram admin:
    «الزامات دیپلوی برای پروژه (اسم پروژه) چک شد و نتیجه به شرح زیر است.
    (شرح نتیجه)
    آیا کامنت شود؟»
    """
    msg = (
        f"📋 <b>الزامات دیپلوی برای پروژه {report.project_name} چک شد و نتیجه به شرح زیر است.</b>\n\n"
        f"🔗 <b>ریپازیتوری:</b> <code>{report.repo_url}</code>\n\n"
        f"<b>گزارش بررسی الزامات:</b>\n"
        f"{report.summary_text}\n\n"
        f"<b>متن پیشنهادی جهت کامنت در کلیک‌آپ:</b>\n"
        f"<code>{report.comment_text}</code>\n\n"
        f"❓ <b>آیا کامنت شود؟</b>"
    )
    return msg


def format_clickup_final_comment(comment_body: str, reporter_username: Optional[str] = None) -> str:
    """
    Formats the final comment posted in the ClickUp task:
    «وقت به خیر
    الزامات دیپلوی به طور کامل رعایت نشده است:
    (کامنت)»
    """
    mention = f" @{reporter_username}" if reporter_username else ""
    return (
        f"وقت به خیر{mention}\n"
        f"الزامات دیپلوی به طور کامل رعایت نشده است:\n"
        f"{comment_body}"
    )


def format_clickup_success_comment(reporter_username: Optional[str] = None) -> str:
    mention = f" @{reporter_username}" if reporter_username else ""
    return (
        f"وقت به خیر{mention}\n"
        f"کلیه الزامات دیپلوی با موفقیت بررسی و تایید گردید. فرآیند استقرار در حال پیگیری است."
    )
