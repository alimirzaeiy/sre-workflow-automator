import pytest
from deploy_automation.integrations.gitlab_service import GitLabService


def test_extract_project_path():
    service = GitLabService(token="test")
    assert service.extract_project_path("https://gitlab.example.com/group/subgroup/project.git") == "group/subgroup/project"
    assert service.extract_project_path("git@gitlab.example.com:core/auth-service.git") == "core/auth-service"
    assert service.extract_project_path("https://gitlab.example.com/payment/gateway") == "payment/gateway"
    assert service.extract_project_path("devops/infra") == "devops/infra"
