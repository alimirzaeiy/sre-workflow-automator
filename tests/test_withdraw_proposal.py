import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from telegram import Update

from deploy_automation.bot.sre_manager_bot import SREManagerBot

@pytest.mark.asyncio
async def test_withdraw_proposal_flow(monkeypatch):
    # Initialize bot and mock the Application and bot
    bot_instance = SREManagerBot(token="test-token")
    app = bot_instance.build_application()
    mock_bot = AsyncMock()
    app.bot = mock_bot
    bot_instance.app = app

    # Mock get_task_proposal_messages to return two messages (current and another)
    fake_msgs = [
        MagicMock(chat_id=12345, message_id=111),  # current message (will be skipped)
        MagicMock(chat_id=67890, message_id=222),  # other message to be edited
    ]
    async def mock_get_msgs(task_id):
        return fake_msgs
    monkeypatch.setattr('deploy_automation.bot.sre_manager_bot.get_task_proposal_messages', mock_get_msgs)

    # Prepare a fake CallbackQuery inside an Update
    mock_query = MagicMock()
    mock_query.message.chat_id = 12345
    mock_query.message.message_id = 111
    mock_query.message.text = "Original proposal text"
    mock_query.message.text_html = "Original proposal text"
    mock_query.message.reply_markup = MagicMock()
    mock_query.message.reply_markup.inline_keyboard = []
    mock_query.data = "flow:withdraw_assign:task123"
    mock_query.from_user = MagicMock(id=99999, full_name="Tester")
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    # Invoke the callback handler
    update = Update(update_id=1, callback_query=mock_query)
    await bot_instance.handle_callback(update, None)

    # Verify that the current message was edited
    mock_query.edit_message_text.assert_awaited_once()
    # Verify that the other proposal message was edited via bot.edit_message_text
    mock_bot.edit_message_text.assert_awaited_once()
    args, kwargs = mock_bot.edit_message_text.call_args
    assert kwargs["chat_id"] == 67890
    assert kwargs["message_id"] == 222
    assert "Original proposal text" in kwargs["text"]
    assert kwargs["parse_mode"] == "HTML"

