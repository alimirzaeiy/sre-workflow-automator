import os
import json
import logging
from typing import Optional, Any
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
from deploy_automation.config_canned_responses import CANNED_CLOSE_RESPONSES, get_canned_response_by_key
from deploy_automation.integrations.clickup_service import ClickUpService
from deploy_automation.integrations.gitlab_service import GitLabService
from deploy_automation.integrations.ai_provider import AIServiceProvider
from deploy_automation.engine.checker import DeployRequirementsChecker
from deploy_automation.database import (
    save_task_conversation_state,
    get_task_conversation_state,
    get_user_active_conversations,
    save_task_proposal_message,
    get_task_proposal_messages
)
from deploy_automation.bot.token_store import InMemoryTokenStore

logger = logging.getLogger(__name__)

class SREManagerBot:
    """
    Advanced interactive bot for SRE team members and manager:
    1. Sends notifications to specific member topics in supergroup.
    2. Interactive private chat (PV) flow for assigned deploy tasks.
    3. Option to confirm In-Progress (removes other watchers) or Close task with canned/custom comment.
    4. Safe, in-memory collection of user's GitLab token.
    5. Access check: if no access -> draft request comment + tag reporter + waiting for customer (with user approval).
    6. If has access -> audit 9 deployment readiness standards -> AI-generated comment -> approve/reject/edit comment -> waiting for customer.
    7. Supports /tasks and /resume to list assigned tasks and continue from last step.
    """

    def __init__(
        self,
        token: str = settings.TELEGRAM_BOT_TOKEN,
        clickup_service: Optional[ClickUpService] = None,
        ai_provider: Optional[AIServiceProvider] = None,
        proxy: Optional[str] = None
    ):
        self.token = token
        self.proxy = proxy or os.getenv("PROXY") or getattr(settings, "PROXY", None) or os.getenv("HTTPS_PROXY") or getattr(settings, "HTTPS_PROXY", None) or os.getenv("HTTP_PROXY") or getattr(settings, "HTTP_PROXY", None) or os.getenv("ALL_PROXY") or getattr(settings, "ALL_PROXY", None)
        self.clickup = clickup_service or ClickUpService()
        self.ai = ai_provider or AIServiceProvider()
        self.app: Optional[Application] = None

    def build_application(self) -> Application:
        builder = Application.builder().token(self.token)
        if self.proxy:
            builder.proxy(self.proxy)
            builder.get_updates_proxy(self.proxy)
        app = builder.build()

        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CommandHandler("id", self.cmd_id))
        app.add_handler(CommandHandler("info", self.cmd_id))
        app.add_handler(CommandHandler("tasks", self.cmd_tasks))
        app.add_handler(CommandHandler("resume", self.cmd_resume))
        app.add_handler(CallbackQueryHandler(self.handle_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))

        self.app = app
        return app

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        await update.message.reply_text(
            f"👋 <b>سلام همکار گرامی!</b>\n\n"
            f"به بات مدیریت و اتوماسیون استقرار تیم SRE خوش آمدید.\n"
            f"شناسه چت تلگرام شما: <code>{chat_id}</code>\n\n"
            f"دستورات موجود:\n"
            f"• /tasks : مشاهده تسک‌های در دست اقدام و واگذارشده\n"
            f"• /resume : ادامه آخرین تسک از نقطه‌ای که رها شده\n"
            f"• /help : راهنمای عملکرد بات",
            parse_mode="HTML"
        )

    async def cmd_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Helper command to get Chat ID and Topic/Thread ID."""
        chat = update.effective_chat
        msg = update.effective_message
        thread_id = getattr(msg, "message_thread_id", None)
        user = update.effective_user

        response_lines = [
            f"ℹ️ <b>اطلاعات چت و تاپیک تلگرام:</b>",
            f"• <b>نوع چت:</b> <code>{chat.type}</code>",
            f"• <b>شناسه چت (Chat ID):</b> <code>{chat.id}</code>",
        ]
        if chat.title:
            response_lines.append(f"• <b>عنوان چت:</b> {chat.title}")
        if thread_id is not None:
            response_lines.append(f"• <b>شناسه تاپیک (Topic ID / Message Thread ID):</b> <code>{thread_id}</code>")
        else:
            response_lines.append(f"• <b>شناسه تاپیک:</b> <i>پیام خارج از تاپیک یا در پی‌وی ارسال شده است.</i>")

        if user:
            response_lines.append(f"• <b>شناسه کاربری شما (User ID):</b> <code>{user.id}</code>")

        await msg.reply_text("\n".join(response_lines), parse_mode="HTML")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "📌 <b>راهنمای سیستم مدیریت SRE:</b>\n"
            "۱. این بات به محض واگذاری تسک جدید، در تاپیک گروه و پی‌وی به شما اطلاع می‌دهد.\n"
            "۲. برای تسک‌های دیپلوی، خلاصه اطلاعات و کامنت‌ها ارسال شده و امکان آغاز، بستن با کامنت، یا بررسی الزامات فراهم است.\n"
            "۳. دستور /id در هر تاپیک شناسه گروه و شناسه دقیق همان تاپیک را به شما اعلام می‌کند.\n"
            "۴. هیچ تغییری روی کلیک‌آپ بدون تایید صریح شما اعمال نخواهد شد.\n"
            "۵. توکن گیت‌لب شما فقط در حافظه رم موقت نگهداری شده و پس از پایان کار کاملاً حذف می‌شود.",
            parse_mode="HTML"
        )

    async def cmd_tasks(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        active_states = await get_user_active_conversations(uid)
        if not active_states:
            await update.message.reply_text("✨ در حال حاضر تسک فعالی در جریان گفتگو با این بات ندارید.")
            return

        lines = ["📋 <b>تسک‌های فعال شما:</b>\n"]
        keyboard = []
        for state in active_states:
            ctx = {}
            try:
                ctx = json.loads(state.context_data_json or "{}")
            except Exception:
                pass
            task_name = ctx.get("task_name", f"Task {state.task_id}")
            lines.append(f"• <b>{task_name}</b> (مرحله: <code>{state.step}</code>)")
            keyboard.append([InlineKeyboardButton(f"▶️ ادامه: {task_name[:25]}", callback_data=f"resume:{state.task_id}")])

        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        active_states = await get_user_active_conversations(uid)
        if not active_states:
            await update.message.reply_text("تسک فعالی برای ادامه یافت نشد.")
            return

        latest = active_states[0]
        await self.resume_task_flow(update.effective_chat.id, latest.task_id, uid)

    async def notify_task_dispatched(self, dispatch_info: dict[str, Any]):
        """
        Sends an interactive assignment PROPOSAL to Telegram.
        ClickUp IS NOT MODIFIED until the user/manager explicitly clicks 'Confirm Assignment'.
        """
        if not self.app:
            return

        task = dispatch_info["task"]
        member = dispatch_info["member"]
        reason = dispatch_info["reason"]

        tid = task.get("id")
        tname = task.get("name", "Untitled")
        turl = task.get("url") or f"https://app.clickup.com/t/{tid}"
        repo_url = task.get("repo_url", "ثبت نشده")
        env_name = task.get("environment", "نامشخص")
        member_name = member.get("name", "همکار")
        c_uid = member.get("id")
        topic_id = member.get("topic_id")
        user_tg_id = member.get("telegram_id")
        group_id = getattr(settings, "TELEGRAM_TEAM_GROUP_ID", 0)

        mode = (getattr(settings, "DECISION_MODE", "rule_engine") or "rule_engine").lower()
        engine_label = "هوش مصنوعی" if mode == "ai" else "موتور تصمیم‌گیری و قوانین"
        engine_btn_icon = "🤖" if mode == "ai" else "⚙️"

        # Build proposal message
        proposal_text = (
            f"🔔 <b>پیشنهاد تخصیص تسک جدید (نیازمند تایید)</b>\n\n"
            f"📌 <b>عنوان تسک:</b> <a href=\"{turl}\">{tname}</a>\n"
            f"🏞 <b>محیط:</b> <code>{env_name}</code>\n"
            f"🔗 <b>ریپو:</b> <code>{repo_url}</code>\n"
            f"👤 <b>عضو پیشنهادی {engine_label}:</b> <b>{member_name}</b>\n"
            f"💡 <b>علت پیشنهاد:</b> {reason}\n\n"
            f"⚠️ <i>هیچ تغییری در کلیک‌آپ اعمال نشده است. جهت واگذاری رسمی لطفاً تایید نمایید:</i>"
        )

        # Build keyboard:
        # 1. Primary button for the suggested member
        keyboard = [
            [
                InlineKeyboardButton(f"{engine_btn_icon} تایید پیشنهاد ({member_name})", callback_data=f"flow:confirm_assign:{tid}:{c_uid}")
            ]
        ]

        # 2. Alternative options for each other team member
        raw_members = getattr(settings, "SRE_TEAM_MEMBERS", "[]")
        try:
            m_list = json.loads(raw_members) if isinstance(raw_members, str) else raw_members
        except Exception:
            m_list = []

        other_buttons = []
        for other_m in m_list:
            other_c_uid = other_m.get("clickup_id")
            other_name = other_m.get("name", "همکار")
            if other_c_uid and str(other_c_uid) != str(c_uid):
                other_buttons.append(
                    InlineKeyboardButton(f"👤 {other_name}", callback_data=f"flow:confirm_assign:{tid}:{other_c_uid}")
                )

        # Pair other member buttons 2-per-row for clean layout
        for i in range(0, len(other_buttons), 2):
            keyboard.append(other_buttons[i:i + 2])

        # 3. Reject button (manager only — full rejection)
        keyboard.append([
            InlineKeyboardButton("❌ رد پیشنهاد (عدم تخصیص)", callback_data=f"flow:reject_assign:{tid}")
        ])

        # 4. Withdraw button — any assignee can opt-out, will be removed from assignees
        keyboard.append([
            InlineKeyboardButton("🚫 از این تسک انصراف می‌دهم", callback_data=f"flow:withdraw_assign:{tid}")
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        # 1. Send proposal to SRE Manager PV
        mgr_chat = getattr(settings, "SRE_MANAGER_TELEGRAM_CHAT_ID", 0) or getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", 0)
        mgr_topic_id = getattr(settings, "SRE_MANAGER_TELEGRAM_TOPIC_ID", None)

        if mgr_chat:
            try:
                m_pv = await self.app.bot.send_message(
                    chat_id=mgr_chat,
                    text=proposal_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
                await save_task_proposal_message(tid, chat_id=mgr_chat, message_id=m_pv.message_id)
            except Exception as e:
                logger.error(f"Failed to send assignment proposal to SRE Manager PV {mgr_chat}: {e}")

        # 2. Send proposal to SRE Manager Topic in supergroup (e.g. topic 3)
        if group_id and mgr_topic_id:
            try:
                m_grp = await self.app.bot.send_message(
                    chat_id=group_id,
                    message_thread_id=mgr_topic_id,
                    text=proposal_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
                await save_task_proposal_message(tid, chat_id=group_id, message_id=m_grp.message_id, message_thread_id=mgr_topic_id)
            except Exception as e:
                logger.error(f"Failed to send proposal to SRE Manager topic {mgr_topic_id} in group {group_id}: {e}")
        elif group_id and not mgr_chat and not mgr_topic_id:
            # Fallback only if no manager PV or topic configured
            try:
                m_fb = await self.app.bot.send_message(
                    chat_id=group_id,
                    text=proposal_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
                await save_task_proposal_message(tid, chat_id=group_id, message_id=m_fb.message_id)
            except Exception as e:
                logger.error(f"Failed to send proposal to group {group_id}: {e}")

    async def start_private_deploy_flow(self, user_telegram_id: int, task: dict[str, Any]):
        """Initiates the private interactive deployment flow in the user's PV."""
        tid = task.get("id")
        tname = task.get("name", "Untitled")
        turl = task.get("url") or f"https://app.clickup.com/t/{tid}"
        desc = (task.get("description") or "بدون توضیحات")[:400]
        repo_url = task.get("repo_url") or "ثبت نشده"
        creator = task.get("creator") or {}
        reporter_name = creator.get("username", "Unknown")
        reporter_id = creator.get("id")

        # Fetch last 3 comments
        comments_raw = await self.clickup.get_task_comments(tid)
        last_3 = comments_raw[-3:] if comments_raw else []
        comments_formatted = []
        for c in last_3:
            u = (c.get("user") or {}).get("username", "کاربر")
            txt = (c.get("comment_text") or "").strip()
            comments_formatted.append(f"▫️ <b>{u}:</b> {txt[:150]}")
        comments_text = "\n".join(comments_formatted) if comments_formatted else "<i>کامنتی ثبت نشده است.</i>"

        msg = (
            f"📋 <b>فرآیند بررسی و انجام تسک:</b> <a href=\"{turl}\">{tname}</a>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>ایجادکننده (Reporter):</b> @{reporter_name}\n"
            f"🔗 <b>ریپازیتوری گیت‌لب:</b> <code>{repo_url}</code>\n\n"
            f"📝 <b>توضیحات تسک:</b>\n{desc}\n\n"
            f"💬 <b>۳ کامنت آخر:</b>\n{comments_text}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"آیا مایلید این تسک <b>In Progress</b> شود یا نیاز به <b>بستن با کامنت</b> دارد؟"
        )

        keyboard = [
            [
                InlineKeyboardButton("▶️ بله، In Progress شود", callback_data=f"flow:start_in_progress:{tid}"),
            ],
            [
                InlineKeyboardButton("🚫 بستن تسک با کامنت", callback_data=f"flow:choose_close:{tid}")
            ]
        ]

        # Save initial state in DB
        context_data = {
            "task_id": tid,
            "task_name": tname,
            "repo_url": repo_url,
            "reporter_id": reporter_id,
            "reporter_username": reporter_name
        }
        await save_task_conversation_state(tid, user_telegram_id, "AWAITING_IN_PROGRESS_CONFIRM", context_data)

        try:
            await self.app.bot.send_message(
                chat_id=user_telegram_id,
                text=msg,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to send private interactive message to user {user_telegram_id}: {e}")

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        data = query.data or ""
        uid = update.effective_user.id
        parts = data.split(":")
        action_prefix = parts[0]

        if action_prefix == "resume":
            tid = parts[1]
            await self.resume_task_flow(query.message.chat_id, tid, uid)
            return

        if action_prefix != "flow" or len(parts) < 3:
            return

        action = parts[1]
        task_id = parts[2]

        state = await get_task_conversation_state(task_id, uid)
        ctx = {}
        if state:
            try:
                ctx = json.loads(state.context_data_json or "{}")
            except Exception:
                pass

        if action == "confirm_assign":
            # Explicit user approval to assign task in ClickUp
            target_c_uid = int(parts[3])
            rep_id = ctx.get("reporter_id") if ctx else None
            assign_ok = await self.clickup.assign_user_to_task(task_id, target_c_uid, reporter_id=rep_id)
            # Ensure followers/watchers cleanup (keep only assigned user and reporter)
            await self.clickup.clean_watchers_keep_user_and_reporter(task_id, user_id=target_c_uid, reporter_id=rep_id)

            # Find member details
            raw_members = getattr(settings, "SRE_TEAM_MEMBERS", "[]")
            m_list = json.loads(raw_members) if isinstance(raw_members, str) else raw_members
            target_member = next((m for m in m_list if m.get("clickup_id") == target_c_uid), None)
            m_name = target_member.get("name", "کاربر") if target_member else "کاربر"
            target_tg_id = target_member.get("telegram_id") if target_member else None

            current_chat_id = query.message.chat_id if query.message else None
            current_message_id = query.message.message_id if query.message else None
            original_text = query.message.text if query.message else ""

            status_suffix = (
                f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ <b>تخصیص تسک به {m_name} در کلیک‌آپ با تایید شما ثبت گردید.</b>"
            )

            # Edit current message
            try:
                await query.edit_message_text(
                    f"{original_text}{status_suffix}",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning(f"Could not edit callback query message: {e}")

            # Also edit any corresponding proposal messages in other chats (PV <-> Group sync)
            try:
                proposal_msgs = await get_task_proposal_messages(task_id)
                for pmsg in proposal_msgs:
                    if pmsg.chat_id == current_chat_id and pmsg.message_id == current_message_id:
                        continue
                    try:
                        await self.app.bot.edit_message_text(
                            chat_id=pmsg.chat_id,
                            message_id=pmsg.message_id,
                            text=f"{original_text}{status_suffix}",
                            parse_mode="HTML"
                        )
                    except Exception as ex:
                        logger.debug(f"Could not sync proposal message in chat {pmsg.chat_id}: {ex}")
            except Exception as e:
                logger.warning(f"Error syncing proposal messages across chats: {e}")

            task_details = await self.clickup.get_task(task_id)
            group_id = getattr(settings, "TELEGRAM_TEAM_GROUP_ID", 0)
            member_topic_id = target_member.get("topic_id") if target_member else None

            # Notify the assigned member's topic in the team group
            if group_id and member_topic_id:
                turl = f"https://app.clickup.com/t/{task_id}"
                tname = task_details.task_name if task_details else f"Task {task_id}"
                try:
                    await self.app.bot.send_message(
                        chat_id=group_id,
                        message_thread_id=member_topic_id,
                        text=(
                            f"📋 <b>تسک جدید به شما تخصیص یافت:</b>\n\n"
                            f"📌 <a href=\"{turl}\">{tname}</a>\n"
                            f"👤 <b>مسئول:</b> {m_name}\n"
                            f"ℹ️ لطفاً جهت پیگیری و آغاز فرآیند استقرار به ربات در پی‌وی مراجعه کنید."
                        ),
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.warning(f"Could not notify member topic {member_topic_id}: {e}")

            # Start interactive private flow with the assigned member in PV
            if task_details and target_tg_id:
                raw_dict = await self.clickup.get_task_details(task_id) or {}
                await self.start_private_deploy_flow(int(target_tg_id), raw_dict)
            return

        elif action == "reject_assign":
            current_chat_id = query.message.chat_id if query.message else None
            current_message_id = query.message.message_id if query.message else None
            original_text = query.message.text if query.message else ""

            reject_suffix = (
                f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
                f"❌ <b>پیشنهاد تخصیص توسط شما رد شد. هیچ تغییری در کلیک‌آپ اعمال نشد.</b>"
            )

            try:
                await query.edit_message_text(
                    f"{original_text}{reject_suffix}",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning(f"Could not edit callback query message on reject: {e}")

            # Sync rejection to other chats (PV <-> Group sync)
            try:
                proposal_msgs = await get_task_proposal_messages(task_id)
                for pmsg in proposal_msgs:
                    if pmsg.chat_id == current_chat_id and pmsg.message_id == current_message_id:
                        continue
                    try:
                        await self.app.bot.edit_message_text(
                            chat_id=pmsg.chat_id,
                            message_id=pmsg.message_id,
                            text=f"{original_text}{reject_suffix}",
                            parse_mode="HTML"
                        )
                    except Exception as ex:
                        logger.debug(f"Could not sync reject proposal message in chat {pmsg.chat_id}: {ex}")
            except Exception as e:
                logger.warning(f"Error syncing reject proposal messages across chats: {e}")
            return

        elif action == "withdraw_assign":
            # User opts out: remove them from ClickUp assignees, rebuild keyboard without their button
            withdrawing_tg_id = query.from_user.id if query.from_user else None
            withdrawer_name = query.from_user.full_name if query.from_user else "کاربر"
            withdrawer_clickup_id = None

            # Resolve telegram_id → clickup_id + name from SRE_TEAM_MEMBERS
            try:
                raw_members = getattr(settings, "SRE_TEAM_MEMBERS", "[]")
                m_list = json.loads(raw_members) if isinstance(raw_members, str) else raw_members
                matched = next(
                    (m for m in m_list if str(m.get("telegram_id")) == str(withdrawing_tg_id)),
                    None
                )
                if matched:
                    withdrawer_name = matched.get("name", withdrawer_name)
                    withdrawer_clickup_id = matched.get("clickup_id")
            except Exception:
                pass

            # Note: Do NOT touch ClickUp assignees. Only remove from Telegram options.

            # 1. Rebuild keyboard: filter out any button whose callback_data references this withdrawer
            # (both the primary confirm button and any alternative member button)
            new_keyboard = []
            if query.message and query.message.reply_markup:
                for row in query.message.reply_markup.inline_keyboard:
                    new_row = []
                    for btn in row:
                        cb = btn.callback_data or ""
                        # Drop buttons that would assign this withdrawer (their clickup_id appears in callback)
                        if withdrawer_clickup_id and str(withdrawer_clickup_id) in cb and "confirm_assign" in cb:
                            continue
                        new_row.append(btn)
                    if new_row:
                        new_keyboard.append(new_row)
            new_markup = InlineKeyboardMarkup(new_keyboard) if new_keyboard else None

            # 2. Build updated text — preserve HTML by using text_html attribute
            original_html = ""
            if query.message:
                try:
                    original_html = query.message.text_html or query.message.text or ""
                except Exception:
                    original_html = query.message.text or ""

            withdraw_suffix = (
                f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
                f"🚫 <b>{withdrawer_name} از این تسک انصراف داد و از گزینه‌های تلگرام حذف شد.</b>"
            )
            new_text = f"{original_html}{withdraw_suffix}"

            current_chat_id = query.message.chat_id if query.message else None
            current_message_id = query.message.message_id if query.message else None

            # 4. Edit current message (text + keyboard)
            try:
                await query.edit_message_text(
                    new_text,
                    parse_mode="HTML",
                    reply_markup=new_markup
                )
            except Exception as e:
                logger.warning(f"Could not edit withdraw message: {e}")

            # 5. Sync to all other proposal messages (PV ↔ Group)
            try:
                proposal_msgs = await get_task_proposal_messages(task_id)
                for pmsg in proposal_msgs:
                    if pmsg.chat_id == current_chat_id and pmsg.message_id == current_message_id:
                        continue
                    try:
                        await self.app.bot.edit_message_text(
                            chat_id=pmsg.chat_id,
                            message_id=pmsg.message_id,
                            text=new_text,
                            parse_mode="HTML",
                            reply_markup=new_markup
                        )
                    except Exception as ex:
                        logger.debug(f"Could not sync withdraw message in chat {pmsg.chat_id}: {ex}")
            except Exception as e:
                logger.warning(f"Error syncing withdraw proposal messages: {e}")

            await query.answer("✅ انصراف شما ثبت شد و دکمه شما از پیام تلگرام حذف گردید.")
            return


        elif action == "start_in_progress":
            # 1. Update task to in-progress
            await self.clickup.update_task_status(task_id, "in progress")
            # 2. Clean watchers: keep only assigned user and reporter
            c_uid = ctx.get("clickup_user_id")
            if not c_uid:
                # Resolve user clickup id
                my_id = await self.clickup.get_current_user_id()
                c_uid = my_id
            if c_uid:
                await self.clickup.clean_watchers_keep_user_and_reporter(
                    task_id,
                    user_id=int(c_uid),
                    reporter_id=ctx.get("reporter_id")
                )

            repo_url = ctx.get("repo_url")
            has_repo = repo_url and repo_url not in ["ثبت نشده", "None", ""]

            if has_repo:
                # 3. Deploy task with repo: Request GitLab token in PV (memory only)
                await save_task_conversation_state(task_id, uid, "AWAITING_GITLAB_TOKEN", ctx)
                await query.edit_message_text(
                    f"✅ وضعیت تسک به <b>In Progress</b> تغییر یافت و سایر فالورها حذف شدند.\n\n"
                    f"🔐 <b>مرحله بعد: احراز دسترسی به مخزن گیت‌لب</b>\n"
                    f"لطفاً توکن شخصی گیت‌لب خود (Personal Access Token) را ارسال کنید.\n\n"
                    f"⚠️ <i>توجه امنیتی: این توکن صرفاً در حافظه موقت (RAM) پردازش شده و به هیچ وجه روی دیسک ذخیره نمی‌شود. بلافاصله پس از اتمام این گفتگو از حافظه حذف خواهد شد.</i>",
                    parse_mode="HTML"
                )
            else:
                # Non-deploy task without repo: In progress completed!
                await save_task_conversation_state(task_id, uid, "IN_PROGRESS", ctx)
                await query.edit_message_text(
                    f"✅ وضعیت تسک به <b>In Progress</b> تغییر یافت و سایر اعضا از واچرها حذف شدند.\n\n"
                    f"📌 این تسک فاقد مخزن گیت‌لب است؛ نیازی به بررسی توکن گیت‌لب نمی‌باشد و تسک در اختیار شماست.",
                    parse_mode="HTML"
                )

        elif action == "choose_close":
            # Offer canned response options + custom option
            keyboard = []
            for resp in CANNED_CLOSE_RESPONSES:
                keyboard.append([InlineKeyboardButton(f"📌 {resp.title}", callback_data=f"flow:canned_select:{task_id}:{resp.key}")])
            keyboard.append([InlineKeyboardButton("✍️ نوشتن کامنت دلخواه", callback_data=f"flow:custom_close:{task_id}")])
            keyboard.append([InlineKeyboardButton("🔙 انصراف", callback_data=f"flow:cancel_close:{task_id}")])

            await save_task_conversation_state(task_id, uid, "CHOOSING_CLOSE_OPTION", ctx)
            await query.edit_message_text(
                "🚫 <b>بستن تسک با کامنت</b>\nلطفاً یکی از متون پیش‌فرض را انتخاب کنید یا گزینه نوشتن متن دلخواه را بزنید:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )

        elif action == "canned_select":
            canned_key = parts[3]
            canned_text = get_canned_response_by_key(canned_key)
            ctx["pending_close_comment"] = canned_text
            await save_task_conversation_state(task_id, uid, "CONFIRMING_CLOSE_COMMENT", ctx)

            keyboard = [
                [
                    InlineKeyboardButton("✅ تایید و بستن تسک", callback_data=f"flow:do_close:{task_id}"),
                    InlineKeyboardButton("✏️ ویرایش متن", callback_data=f"flow:edit_close:{task_id}"),
                ],
                [
                    InlineKeyboardButton("🔙 بازگشت", callback_data=f"flow:choose_close:{task_id}")
                ]
            ]
            await query.edit_message_text(
                f"📝 <b>پیش‌نمایش کامنت بستن تسک:</b>\n\n"
                f"<blockquote>{canned_text}</blockquote>\n\n"
                f"آیا برای ثبت در کلیک‌آپ و بستن تسک تایید می‌کنید؟",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )

        elif action == "custom_close" or action == "edit_close":
            await save_task_conversation_state(task_id, uid, "AWAITING_CUSTOM_CLOSE_TEXT", ctx)
            await query.edit_message_text(
                "✏️ <b>لطفاً متن کامنت مورد نظرتان برای بستن تسک را ارسال کنید:</b>",
                parse_mode="HTML"
            )

        elif action == "cancel_close":
            await self.resume_task_flow(query.message.chat_id, task_id, uid)

        elif action == "do_close":
            comment = ctx.get("pending_close_comment", "تسک بسته شد.")
            reporter_id = ctx.get("reporter_id")
            reporter_username = ctx.get("reporter_username")

            # Post comment to ClickUp with tag
            await self.clickup.post_tagged_comment(
                task_id=task_id,
                reporter_id=reporter_id,
                reporter_username=reporter_username,
                body_text=comment
            )
            # Close task in ClickUp
            await self.clickup.update_task_status(task_id, "closed")
            await save_task_conversation_state(task_id, uid, "CLOSED", ctx)

            await query.edit_message_text(
                f"✅ <b>کامنت با موفقیت ثبت شد و تسک در کلیک‌آپ بسته گردید.</b>\n\n"
                f"📝 متن ارسال شده:\n<blockquote>{comment}</blockquote>",
                parse_mode="HTML"
            )

        elif action == "approve_no_access_comment":
            # Post comment and set waiting for customer
            comment_text = ctx.get("no_access_comment", "من به این پروژه دسترسی ندارم. لطفاً دسترسی Maintainer به من بدهید.")
            rep_id = ctx.get("reporter_id")
            rep_user = ctx.get("reporter_username")

            await self.clickup.post_tagged_comment(
                task_id=task_id,
                reporter_id=rep_id,
                reporter_username=rep_user,
                body_text=comment_text
            )
            await self.clickup.update_task_status(task_id, settings.CLICKUP_WAITING_STATUS, reporter_id=rep_id)
            await save_task_conversation_state(task_id, uid, "WAITING_FOR_CUSTOMER", ctx)
            InMemoryTokenStore.remove_token(uid)

            await query.edit_message_text(
                f"✅ کامنت درخواست دسترسی ثبت شد و تسک به وضعیت <b>{settings.CLICKUP_WAITING_STATUS}</b> تغییر یافت.\n"
                f"🔐 توکن گیت‌لب نیز از حافظه پاک شد.",
                parse_mode="HTML"
            )

        elif action == "edit_no_access_comment":
            await save_task_conversation_state(task_id, uid, "AWAITING_EDIT_NO_ACCESS_TEXT", ctx)
            await query.edit_message_text(
                "✏️ لطفاً متن اصلاح‌شده درخواست دسترسی را تایپ و ارسال کنید:",
                parse_mode="HTML"
            )

        elif action == "approve_defects_comment":
            comment_text = ctx.get("defects_comment", "")
            rep_id = ctx.get("reporter_id")
            rep_user = ctx.get("reporter_username")

            await self.clickup.post_tagged_comment(
                task_id=task_id,
                reporter_id=rep_id,
                reporter_username=rep_user,
                body_text=comment_text
            )
            await self.clickup.update_task_status(task_id, settings.CLICKUP_WAITING_STATUS, reporter_id=rep_id)
            await save_task_conversation_state(task_id, uid, "WAITING_FOR_CUSTOMER", ctx)
            InMemoryTokenStore.remove_token(uid)

            await query.edit_message_text(
                f"✅ کامنت ممیزی الزامات با موفقیت در کلیک‌آپ ثبت شد و تسک به وضعیت <b>{settings.CLICKUP_WAITING_STATUS}</b> رفت.\n"
                f"🔐 توکن گیت‌لب از حافظه موقت پاک شد.",
                parse_mode="HTML"
            )

        elif action == "reject_defects_comment":
            await save_task_conversation_state(task_id, uid, "COMMENT_REJECTED", ctx)
            InMemoryTokenStore.remove_token(uid)
            await query.edit_message_text(
                "❌ کامنت توسط شما رد شد. هیچ تغییری در کلیک‌آپ اعمال نگردید.\n"
                "🔐 توکن گیت‌لب از حافظه موقت پاک شد.",
                parse_mode="HTML"
            )

        elif action == "edit_defects_comment":
            await save_task_conversation_state(task_id, uid, "AWAITING_EDIT_DEFECTS_TEXT", ctx)
            await query.edit_message_text(
                "✏️ لطفاً متن اصلاح‌شده کامنت الزامات دیپلوی را ارسال فرمایید:",
                parse_mode="HTML"
            )

    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        text = update.message.text.strip()

        active_states = await get_user_active_conversations(uid)
        if not active_states:
            await update.message.reply_text("در حال حاضر مرحله منتظری برای دریافت ورودی وجود ندارد. جهت مشاهده تسک‌ها از /tasks استفاده کنید.")
            return

        current_state = active_states[0]
        task_id = current_state.task_id
        step = current_state.step
        ctx = {}
        try:
            ctx = json.loads(current_state.context_data_json or "{}")
        except Exception:
            pass

        if step == "AWAITING_CUSTOM_CLOSE_TEXT":
            ctx["pending_close_comment"] = text
            await save_task_conversation_state(task_id, uid, "CONFIRMING_CLOSE_COMMENT", ctx)
            keyboard = [
                [
                    InlineKeyboardButton("✅ تایید و بستن تسک", callback_data=f"flow:do_close:{task_id}"),
                    InlineKeyboardButton("✏️ ویرایش متن", callback_data=f"flow:edit_close:{task_id}"),
                ],
                [
                    InlineKeyboardButton("🔙 انصراف", callback_data=f"flow:choose_close:{task_id}")
                ]
            ]
            await update.message.reply_text(
                f"📝 <b>پیش‌نمایش کامنت بستن تسک:</b>\n\n"
                f"<blockquote>{text}</blockquote>\n\n"
                f"آیا برای ثبت در کلیک‌آپ تایید می‌کنید؟",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )

        elif step == "AWAITING_GITLAB_TOKEN":
            # User sent gitlab token
            token = text
            InMemoryTokenStore.set_token(uid, token)

            # Delete the user message for extra security if possible
            try:
                await update.message.delete()
            except Exception:
                pass

            wait_msg = await update.message.reply_text("🔍 در حال اعتبارسنجی توکن و بررسی دسترسی به مخزن...")
            await self.verify_gitlab_access_and_proceed(update.effective_chat.id, wait_msg.message_id, task_id, uid, token, ctx)

        elif step == "AWAITING_EDIT_NO_ACCESS_TEXT":
            ctx["no_access_comment"] = text
            await save_task_conversation_state(task_id, uid, "CONFIRMING_NO_ACCESS_COMMENT", ctx)
            keyboard = [
                [
                    InlineKeyboardButton("✅ تایید و ارسال", callback_data=f"flow:approve_no_access_comment:{task_id}"),
                    InlineKeyboardButton("✏️ ویرایش مجدد", callback_data=f"flow:edit_no_access_comment:{task_id}")
                ]
            ]
            await update.message.reply_text(
                f"📝 <b>پیش‌نمایش کامنت درخواست دسترسی:</b>\n\n"
                f"<blockquote>{text}</blockquote>\n\n"
                f"آیا تایید می‌کنید؟",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )

        elif step == "AWAITING_EDIT_DEFECTS_TEXT":
            ctx["defects_comment"] = text
            await save_task_conversation_state(task_id, uid, "CONFIRMING_DEFECTS_COMMENT", ctx)
            keyboard = [
                [
                    InlineKeyboardButton("✅ تایید و ارسال", callback_data=f"flow:approve_defects_comment:{task_id}"),
                    InlineKeyboardButton("✏️ ویرایش مجدد", callback_data=f"flow:edit_defects_comment:{task_id}"),
                ],
                [
                    InlineKeyboardButton("❌ رد (عدم ارسال)", callback_data=f"flow:reject_defects_comment:{task_id}")
                ]
            ]
            await update.message.reply_text(
                f"📝 <b>پیش‌نمایش متن ویرایش‌شده کامنت الزامات:</b>\n\n"
                f"<blockquote>{text}</blockquote>\n\n"
                f"آیا برای ثبت در کلیک‌آپ و تغییر وضعیت به waiting for customer تایید می‌کنید؟",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )

    async def verify_gitlab_access_and_proceed(self, chat_id: int, status_msg_id: int, task_id: str, uid: int, user_token: str, ctx: dict[str, Any]):
        repo_url = ctx.get("repo_url")
        gl = GitLabService(token=user_token)

        # Check access
        has_access, err = await gl.check_maintainer_access(repo_url)

        if not has_access:
            default_comment = "من به این پروژه دسترسی ندارم. لطفاً دسترسی Maintainer به این مخزن را به من بدهید تا فرآیند استقرار ادامه یابد."
            ctx["no_access_comment"] = default_comment
            await save_task_conversation_state(task_id, uid, "AWAITING_NO_ACCESS_CONFIRM", ctx)

            keyboard = [
                [
                    InlineKeyboardButton("✅ تایید و ارسال کامنت", callback_data=f"flow:approve_no_access_comment:{task_id}"),
                    InlineKeyboardButton("✏️ ویرایش متن کامنت", callback_data=f"flow:edit_no_access_comment:{task_id}")
                ]
            ]

            await self.app.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=(
                    f"⚠️ <b>عدم دسترسی به مخزن گیت‌لب!</b>\n"
                    f"پروژه: <code>{repo_url}</code>\n\n"
                    f"کاربر شما دسترسی Maintainer به این مخزن را ندارد ({err or 'Access Denied'}).\n\n"
                    f"📝 <b>پیش‌نویس کامنت به ایجادکننده تسک:</b>\n"
                    f"<blockquote>{default_comment}</blockquote>\n\n"
                    f"در صورت تایید، این کامنت با تگ کردن ریپورتر ثبت شده و وضعیت به <code>{settings.CLICKUP_WAITING_STATUS}</code> تغییر خواهد کرد."
                ),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            return

        # User has access! Fetch files and verify 9 deployment standards
        await self.app.bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg_id,
            text="✅ دسترسی Maintainer تایید شد.\n📥 در حال بررسی ساختار مخزن و الزامات ۹‌گانه استقرار Git Flow پالیز..."
        )

        project = await gl.get_project(repo_url)
        files_map = await gl.get_repository_files_map(project)
        branches = await gl.get_branches_and_protection(project)

        project_name = gl.extract_project_path(repo_url)
        checker = DeployRequirementsChecker(project_name, repo_url, files_map, branches)
        report = checker.run_all_checks(has_maintainer_access=True)

        if report.all_passed:
            # Everything passed!
            await save_task_conversation_state(task_id, uid, "REQUIREMENTS_PASSED", ctx)
            InMemoryTokenStore.remove_token(uid)
            await self.app.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=(
                    f"🎉 <b>کلیه الزامات ۹‌گانه استقرار رعایت شده است!</b>\n\n"
                    f"پروژه: <code>{project_name}</code>\n"
                    f"تمام موارد سند Git Flow و چک‌لیست استقرار Production تایید گردید.\n"
                    f"اکنون می‌توانید مراحل دیپلوی سرویس را ادامه دهید."
                ),
                parse_mode="HTML"
            )
            return

        # Some checks failed: generate comment with AI
        failed_checks = [
            {"rule_id": c.rule_id, "title": c.title, "details": c.details, "remediation": c.remediation}
            for c in report.checks if not c.passed
        ]

        ai_comment = self.ai.generate_deploy_requirements_comment(project_name, failed_checks)
        ctx["defects_comment"] = ai_comment
        await save_task_conversation_state(task_id, uid, "AWAITING_DEFECTS_REVIEW", ctx)

        keyboard = [
            [
                InlineKeyboardButton("✅ تایید و ارسال کامنت", callback_data=f"flow:approve_defects_comment:{task_id}"),
                InlineKeyboardButton("✏️ ویرایش متن", callback_data=f"flow:edit_defects_comment:{task_id}"),
            ],
            [
                InlineKeyboardButton("❌ رد (عدم ارسال)", callback_data=f"flow:reject_defects_comment:{task_id}")
            ]
        ]

        await self.app.bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg_id,
            text=(
                f"⚠️ <b>نواقص در الزامات استقرار Git Flow مشاهده شد:</b>\n"
                f"پروژه: <code>{project_name}</code>\n\n"
                f"🤖 <b>متن پیشنهادی هوش مصنوعی برای کامنت کلیک‌آپ:</b>\n\n"
                f"<blockquote>{ai_comment}</blockquote>\n\n"
                f"آیا مایلید این متن با منشن کردن ریپورتر ارسال شود و تسک به <code>{settings.CLICKUP_WAITING_STATUS}</code> برود؟"
            ),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def resume_task_flow(self, chat_id: int, task_id: str, uid: int):
        state = await get_task_conversation_state(task_id, uid)
        if not state:
            await self.app.bot.send_message(chat_id=chat_id, text="سابقه‌ای از این تسک یافت نشد.")
            return

        step = state.step
        ctx = {}
        try:
            ctx = json.loads(state.context_data_json or "{}")
        except Exception:
            pass

        tname = ctx.get("task_name", f"Task {task_id}")

        if step == "AWAITING_IN_PROGRESS_CONFIRM":
            keyboard = [
                [InlineKeyboardButton("▶️ بله، In Progress شود", callback_data=f"flow:start_in_progress:{task_id}")],
                [InlineKeyboardButton("🚫 بستن تسک با کامنت", callback_data=f"flow:choose_close:{task_id}")]
            ]
            await self.app.bot.send_message(
                chat_id=chat_id,
                text=f"تسک: <b>{tname}</b>\nدر انتظار تایید شروع یا بستن تسک:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )

        elif step == "AWAITING_GITLAB_TOKEN":
            await self.app.bot.send_message(
                chat_id=chat_id,
                text=f"تسک: <b>{tname}</b>\nدر انتظار دریافت Personal Access Token گیت‌لب شما (صرفاً در حافظه موقت RAM نگهداری می‌شود). لطفاً توکن را ارسال کنید:"
            )

        elif step in ["AWAITING_NO_ACCESS_CONFIRM", "CONFIRMING_NO_ACCESS_COMMENT"]:
            comment = ctx.get("no_access_comment", "")
            keyboard = [
                [
                    InlineKeyboardButton("✅ تایید و ارسال کامنت", callback_data=f"flow:approve_no_access_comment:{task_id}"),
                    InlineKeyboardButton("✏️ ویرایش متن", callback_data=f"flow:edit_no_access_comment:{task_id}")
                ]
            ]
            await self.app.bot.send_message(
                chat_id=chat_id,
                text=f"تسک: <b>{tname}</b>\nپیش‌نویس کامنت عدم دسترسی:\n<blockquote>{comment}</blockquote>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )

        elif step in ["AWAITING_DEFECTS_REVIEW", "CONFIRMING_DEFECTS_COMMENT"]:
            comment = ctx.get("defects_comment", "")
            keyboard = [
                [
                    InlineKeyboardButton("✅ تایید و ارسال کامنت", callback_data=f"flow:approve_defects_comment:{task_id}"),
                    InlineKeyboardButton("✏️ ویرایش متن", callback_data=f"flow:edit_defects_comment:{task_id}"),
                ],
                [InlineKeyboardButton("❌ رد (عدم ارسال)", callback_data=f"flow:reject_defects_comment:{task_id}")]
            ]
            await self.app.bot.send_message(
                chat_id=chat_id,
                text=f"تسک: <b>{tname}</b>\nپیش‌نویس کامنت ممیزی الزامات:\n<blockquote>{comment}</blockquote>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
        else:
            await self.app.bot.send_message(
                chat_id=chat_id,
                text=f"تسک <b>{tname}</b> در وضعیت <code>{step}</code> قرار دارد."
            )
