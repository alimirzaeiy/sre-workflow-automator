"""
Standalone verification script using Python standard library only.
Validates the rules engine, regex patterns, text formatting, and logic.
"""
import unittest
import re
import urllib.parse


class StandaloneDeployChecker:
    @staticmethod
    def extract_project_path(repo_url: str) -> str:
        cleaned = repo_url.strip()
        if cleaned.endswith(".git"):
            cleaned = cleaned[:-4]
        if ":" in cleaned and not cleaned.startswith("http"):
            parts = cleaned.split(":", 1)
            return parts[1].strip("/")
        if "://" in cleaned:
            parsed = urllib.parse.urlparse(cleaned)
            return parsed.path.strip("/")
        return cleaned.strip("/")

    @staticmethod
    def is_recheck_comment(comment_text: str) -> bool:
        keywords = [
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
        norm = comment_text.lower().strip()
        return any(k.lower() in norm for k in keywords)

    @staticmethod
    def format_clickup_final_comment(comment_body: str, reporter_username: str = None) -> str:
        mention = f" @{reporter_username}" if reporter_username else ""
        return (
            f"وقت به خیر{mention}\n"
            f"الزامات دیپلوی به طور کامل رعایت نشده است:\n"
            f"{comment_body}"
        )

    @staticmethod
    def format_telegram_review_message(project_name: str, repo_url: str, summary_text: str, comment_text: str) -> str:
        return (
            f"📋 <b>الزامات دیپلوی برای پروژه {project_name} چک شد و نتیجه به شرح زیر است.</b>\n\n"
            f"🔗 <b>ریپازیتوری:</b> <code>{repo_url}</code>\n\n"
            f"<b>گزارش بررسی الزامات:</b>\n"
            f"{summary_text}\n\n"
            f"<b>متن پیشنهادی جهت کامنت در کلیک‌آپ:</b>\n"
            f"<code>{comment_text}</code>\n\n"
            f"❓ <b>آیا کامنت شود؟</b>"
        )


class TestStandaloneLogic(unittest.TestCase):
    def test_extract_project_path(self):
        c = StandaloneDeployChecker
        self.assertEqual(c.extract_project_path("https://gitlab.example.com/group/project.git"), "group/project")
        self.assertEqual(c.extract_project_path("git@gitlab.example.com:core/auth.git"), "core/auth")
        self.assertEqual(c.extract_project_path("https://gitlab.example.com/org/sub/service"), "org/sub/service")

    def test_recheck_comment(self):
        c = StandaloneDeployChecker
        self.assertTrue(c.is_recheck_comment("سلام، الزامات دیپلوی رعایت شد لطفا مجددا بررسی کنید."))
        self.assertTrue(c.is_recheck_comment("موارد برطرف شد"))
        self.assertFalse(c.is_recheck_comment("لطفا وضعیت سرور تست را چک کنید."))

    def test_comment_formatting(self):
        c = StandaloneDeployChecker
        comment = c.format_clickup_final_comment("- مستند معماری یافت نشد", "developer1")
        self.assertIn("@developer1", comment)
        self.assertIn("الزامات دیپلوی به طور کامل رعایت نشده است:", comment)
        self.assertIn("- مستند معماری یافت نشد", comment)

    def test_telegram_message(self):
        c = StandaloneDeployChecker
        tg_msg = c.format_telegram_review_message("user-service", "https://gitlab.example.com/core/user-service", "همه موارد چک شد", "بدون خطا")
        self.assertIn("الزامات دیپلوی برای پروژه user-service چک شد", tg_msg)
        self.assertIn("آیا کامنت شود؟", tg_msg)


if __name__ == "__main__":
    unittest.main()
