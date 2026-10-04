import os
import logging
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from deploy_automation.config import settings
from deploy_automation.database import (
    get_review_session,
    update_session_status,
    get_latest_awaiting_edit_session,
)
from deploy_automation.integrations.clickup_service import ClickUpService
from deploy_automation.utils.text_helpers import format_clickup_final_comment

logger = logging.getLogger(__name__)


class TelegramService:
    def __init__(self, token: str = settings.TELEGRAM_BOT_TOKEN, admin_chat_id: int = settings.TELEGRAM_ADMIN_CHAT_ID, proxy: Optional[str] = None):
        self.token = token
        self.admin_chat_id = admin_chat_id
        self.proxy = proxy or os.getenv("PROXY") or getattr(settings, "PROXY", None) or os.getenv("HTTPS_PROXY") or getattr(settings, "HTTPS_PROXY", None) or os.getenv("HTTP_PROXY") or getattr(settings, "HTTP_PROXY", None) or os.getenv("ALL_PROXY") or getattr(settings, "ALL_PROXY", None)
        self.clickup_service = ClickUpService()
        self.app: Optional[Application] = None

    def build_application(self) -> Application:
        builder = Application.builder().token(self.token)
        if self.proxy:
            builder.proxy(self.proxy)
            builder.get_updates_proxy(self.proxy)
        app = builder.build()

        # Handlers
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CallbackQueryHandler(self.handle_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_user_message))

        self.app = app
        return app

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        await update.message.reply_text(
            f"سلام! بات بررسی خودکار الزامات دیپلوی فعال است.\n"
            f"شناسه چت شما: `{chat_id}`",
            parse_mode="Markdown"
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "این بات پس از بررسی هر تسک دیپلوی در ClickUp، نتایج را برای شما ارسال کرده و گزینه‌های تایید، ویرایش، رد یا هدایت به لپ‌تاپ را فراهم می‌کند."
        )

    async def send_review_request(self, session_id: str, message_text: str) -> Optional[int]:
        """
        Sends an interactive message with Approve / Edit / Reject / Handoff to laptop buttons.
        """
        if not self.app or not self.token or not self.admin_chat_id:
            logger.warning("Telegram Bot is not fully configured. Skipping telegram message.")
            return None

        keyboard = [
            [
                InlineKeyboardButton("🚀 الزامات دیپلوی رعایت شد، به لپتاپ مراجعه شود", callback_data=f"handoff:{session_id}")
            ],
            [
                InlineKeyboardButton("✅ تایید و ارسال کامنت عدم رعایت", callback_data=f"approve:{session_id}"),
                InlineKeyboardButton("✏️ ویرایش کامنت", callback_data=f"edit:{session_id}"),
            ],
            [
                InlineKeyboardButton("❌ رد (عدم ارسال)", callback_data=f"reject:{session_id}"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        msg = await self.app.bot.send_message(
            chat_id=self.admin_chat_id,
            text=message_text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        return msg.message_id

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        data = query.data or ""
        parts = data.split(":", 1)
        if len(parts) != 2:
            return

        action, session_id = parts
        session = await get_review_session(session_id)

        if not session:
            await query.edit_message_text("⚠️ سشن مورد نظر یافت نشد یا منقضی شده است.")
            return

        if action == "handoff":
            await update_session_status(session_id, "HANDOFF_LAPTOP")
            await query.edit_message_text(
                f"{query.message.text}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💻 <b>الزامات دیپلوی تایید شد. فرآیند استقرار به لپ‌تاپ هدایت گردید.</b>\n"
                f"📌 لطفاً در ترمینال لپ‌تاپ با اجرای اسکریپت یا تایید دستورات ادامه دهید.",
                parse_mode="HTML"
            )

        elif action == "approve":
            # Post comment to ClickUp and set waiting for customer
            task_info = await self.clickup_service.get_task(session.task_id)
            reporter_username = task_info.reporter_username if task_info else None
            
            final_comment = format_clickup_final_comment(session.current_comment, reporter_username)
            await self.clickup_service.post_comment(session.task_id, final_comment, notify_all=True)
            await self.clickup_service.update_task_status(session.task_id, settings.CLICKUP_WAITING_STATUS)

            await update_session_status(session_id, "APPROVED")
            await query.edit_message_text(
                f"{query.message.text}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ <b>کامنت عدم رعایت الزامات ارسال شد و وضعیت تسک به {settings.CLICKUP_WAITING_STATUS} تغییر یافت.</b>",
                parse_mode="HTML"
            )

        elif action == "reject":
            await update_session_status(session_id, "REJECTED")
            await query.edit_message_text(
                f"{query.message.text}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"❌ <b>کامنت توسط شما رد شد و هیچ تغییری در تسک اعمال نگردید.</b>",
                parse_mode="HTML"
            )

        elif action == "edit":
            await update_session_status(session_id, "AWAITING_EDIT_INPUT")
            await query.edit_message_text(
                f"{query.message.text}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✏️ <b>لطفاً متن جدید و اصلاح‌شده برای کامنت را در پاسخ به همین ربات ارسال نمایید:</b>",
                parse_mode="HTML"
            )

    async def handle_user_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_text = update.message.text.strip()
        session = await get_latest_awaiting_edit_session()

        if not session:
            await update.message.reply_text("درخواستی در انتظار ویرایش متن وجود ندارد.")
            return

        # Use new edited text as current_comment
        task_info = await self.clickup_service.get_task(session.task_id)
        reporter_username = task_info.reporter_username if task_info else None

        final_comment = format_clickup_final_comment(user_text, reporter_username)
        await self.clickup_service.post_comment(session.task_id, final_comment, notify_all=True)
        await self.clickup_service.update_task_status(session.task_id, settings.CLICKUP_WAITING_STATUS)

        await update_session_status(session.id, "EDITED", new_comment=user_text)

        await update.message.reply_text(
            f"✅ <b>متن اصلاح‌شده دریافت شد و در تسک کلیک‌آپ کامنت گردید.</b>\n\n"
            f"📌 <b>وضعیت تسک:</b> <code>{settings.CLICKUP_WAITING_STATUS}</code>\n"
            f"📝 <b>متن ارسال شده:</b>\n<code>{final_comment}</code>",
            parse_mode="HTML"
        )
