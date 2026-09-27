import pytest
from deploy_automation.integrations.ai_provider import AIServiceProvider
from deploy_automation.config_canned_responses import CANNED_CLOSE_RESPONSES, get_canned_response_by_key
from deploy_automation.bot.token_store import InMemoryTokenStore

def test_canned_responses():
    assert len(CANNED_CLOSE_RESPONSES) >= 1
    found = get_canned_response_by_key("main_prod_simultaneous")
    assert "تسک main و پروداکشن همزمان" in found

def test_token_store_in_memory_only():
    user_id = 998877
    token = "glpat-test-secret-token-xyz"
    
    # Set token
    InMemoryTokenStore.set_token(user_id, token, ttl_seconds=60)
    assert InMemoryTokenStore.get_token(user_id) == token

    # Remove token
    InMemoryTokenStore.remove_token(user_id)
    assert InMemoryTokenStore.get_token(user_id) is None

def test_workload_estimation_heuristic():
    ai = AIServiceProvider(provider="none") # no remote call
    new_task = {
        "id": "t1",
        "name": "Deploy AuthService",
        "environment": "Production"
    }
    workloads = [
        {"id": 101, "name": "Ali", "in_progress_tasks": [{"id": "a"}, {"id": "b"}]},
        {"id": 102, "name": "Reza", "in_progress_tasks": [{"id": "c"}]},
        {"id": 103, "name": "Sara", "in_progress_tasks": []}, # least busy
        {"id": 104, "name": "Mohammad", "in_progress_tasks": [{"id": "d"}, {"id": "e"}, {"id": "f"}]}
    ]

    res = ai.estimate_workload_and_pick_assignee(new_task, workloads)
    assert res["assignee_id"] == 103
    assert res["assignee_name"] == "Sara"

def test_deploy_requirements_comment_fallback():
    ai = AIServiceProvider(provider="none")
    failed_checks = [
        {
            "rule_id": 1,
            "title": "Macro-Architecture Document in doc/",
            "details": "doc/ folder was not found.",
            "remediation": "Create doc/architecture.md"
        },
        {
            "rule_id": 4,
            "title": "Standard /metrics Endpoint",
            "details": "/metrics endpoint missing.",
            "remediation": "Add /metrics route."
        }
    ]
    comment = ai.generate_deploy_requirements_comment("order-service", failed_checks)
    assert "order-service" in comment
    assert "Macro-Architecture Document" in comment
    assert "Standard /metrics Endpoint" in comment

@pytest.mark.asyncio
async def test_task_proposal_messages_db():
    import uuid
    from deploy_automation.database import save_task_proposal_message, get_task_proposal_messages, init_db
    await init_db()
    test_task = f"task_sync_test_{uuid.uuid4().hex[:8]}"
    await save_task_proposal_message(test_task, chat_id=111, message_id=10, message_thread_id=None)
    await save_task_proposal_message(test_task, chat_id=222, message_id=20, message_thread_id=3)

    msgs = await get_task_proposal_messages(test_task)
    assert len(msgs) == 2
    chat_ids = {m.chat_id for m in msgs}
    assert 111 in chat_ids
    assert 222 in chat_ids


