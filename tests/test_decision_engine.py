import pytest
from deploy_automation.engine.decision_engine import SREDecisionEngine


def test_decision_engine_single_member():
    engine = SREDecisionEngine()
    task = {"name": "Setup redis"}
    workloads = [{"id": 101, "name": "Ali", "in_progress_tasks": []}]
    res = engine.evaluate_task_assignment(task, workloads)
    assert res["assignee_id"] == 101
    assert res["assignee_name"] == "Ali"


def test_decision_engine_workload_balance_no_tasks():
    engine = SREDecisionEngine()
    task = {"name": "Deploy api service"}
    workloads = [
        {"id": 101, "name": "Ali", "in_progress_tasks": [{"name": "Deploy v1", "list_name": "deploy"}]},
        {"id": 102, "name": "Reza", "in_progress_tasks": []}
    ]
    res = engine.evaluate_task_assignment(task, workloads)
    assert res["assignee_id"] == 102
    assert "بدون تسک فعال" in res["reason"]


def test_decision_engine_penalty_scoring():
    """
    Test penalty rules:
    - Deploy: -5
    - Third party / Change / Issue: -3
    - Internal: -2
    - Change env: -1
    """
    engine = SREDecisionEngine()
    
    # Check individual penalties
    assert engine.calculate_task_penalty({"list_name": "deploy"})[0] == 5
    assert engine.calculate_task_penalty({"list_name": "third-party"})[0] == 3
    assert engine.calculate_task_penalty({"list_name": "change"})[0] == 3
    assert engine.calculate_task_penalty({"list_name": "issue"})[0] == 3
    assert engine.calculate_task_penalty({"list_name": "internal"})[0] == 2
    
    # Change env test (-1 point)
    change_env_task = {
        "list_name": "change",
        "custom_fields": [{"name": "envs", "value": "DB_HOST=1.2.3.4"}]
    }
    assert engine.calculate_task_penalty(change_env_task)[0] == 1

    change_env_title_task = {
        "list_name": "change",
        "name": "تغییر env متغیر سرویس auth"
    }
    assert engine.calculate_task_penalty(change_env_title_task)[0] == 1


def test_decision_engine_comparison_winner():
    """
    Candidate 1: Ali has 1 deploy task (-5) -> Total penalty: 5
    Candidate 2: Reza has 1 change-env task (-1) + 1 internal task (-2) -> Total penalty: 3
    Candidate 3: Sara has 1 third-party task (-3) + 1 issue task (-3) -> Total penalty: 6

    Winner should be Reza (penalty 3, score 97) vs Ali (score 95) vs Sara (score 94).
    """
    engine = SREDecisionEngine()
    incoming_task = {"name": "New incoming task", "list_name": "deploy"}

    workloads = [
        {
            "id": 101,
            "name": "Ali",
            "in_progress_tasks": [
                {"name": "Deploy X", "list_name": "deploy"}
            ]
        },
        {
            "id": 102,
            "name": "Reza",
            "in_progress_tasks": [
                {"name": "Env update", "list_name": "change", "envs": "PORT=80"},
                {"name": "Internal cleanup", "list_name": "internal"}
            ]
        },
        {
            "id": 103,
            "name": "Sara",
            "in_progress_tasks": [
                {"name": "Third party sync", "list_name": "third party"},
                {"name": "Fix bug", "list_name": "issue"}
            ]
        }
    ]

    res = engine.evaluate_task_assignment(incoming_task, workloads)
    assert res["assignee_id"] == 102
    assert res["assignee_name"] == "Reza"
    assert "1 تغییر env و 1 اینترنال" in res["reason"]
    assert "3- امتیاز منفی" in res["reason"]

