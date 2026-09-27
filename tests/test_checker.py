import pytest
from deploy_automation.engine.checker import DeployRequirementsChecker
from deploy_automation.models import CheckStatus


def test_checker_all_passed():
    files_map = {
        "doc/architecture.md": "# High Level Architecture",
        "doc/scope.md": "# Scope and Responsibilities",
        "src/app.py": "from prometheus_client import make_wsgi_app\n@app.route('/metrics')\n@app.route('/healthz')",
        "Dockerfile": "FROM python:3.11\nHEALTHCHECK CMD curl -f http://localhost:8000/healthz || exit 1",
        ".gitlab-ci.yml": """
stages:
  - test
  - sonar
  - build

test_job:
  stage: test
  script:
    - pytest --cov=src --cov-report=term
  coverage: '/TOTAL.+?([0-9]{1,3}%)/'

sonarqube_check:
  stage: sonar
  script:
    - sonar-scanner

docker_build:
  stage: build
  script:
    - docker build -t myapp .
""",
    }

    branches = [
        {"name": "main", "protected": True, "default": True},
        {"name": "develop", "protected": True, "default": False},
        {"name": "feature/login", "protected": False, "default": False},
    ]

    checker = DeployRequirementsChecker(
        project_name="core/user-service",
        repo_url="https://gitlab.example.com/core/user-service.git",
        files_map=files_map,
        branches=branches
    )

    report = checker.run_all_checks(has_maintainer_access=True)

    assert report.has_maintainer_access is True
    assert report.all_passed is True
    assert len(report.checks) == 9
    for c in report.checks:
        assert c.passed is True


def test_checker_missing_docs_and_git_flow():
    files_map = {
        "src/main.go": "package main",
        "Dockerfile": "FROM golang:1.21",
    }
    branches = [{"name": "main", "protected": True, "default": True}]

    checker = DeployRequirementsChecker(
        project_name="backend/payment",
        repo_url="https://gitlab.example.com/backend/payment.git",
        files_map=files_map,
        branches=branches
    )

    report = checker.run_all_checks(has_maintainer_access=True)

    assert report.all_passed is False
    
    # Check 1: Architecture
    check1 = next(c for c in report.checks if c.rule_id == 1)
    assert check1.status == CheckStatus.FAILED
    assert "doc/" in check1.remediation

    # Check 2: Git Flow
    check2 = next(c for c in report.checks if c.rule_id == 2)
    assert check2.status == CheckStatus.FAILED
    assert "develop" in check2.details


def test_checker_frontend_runtime_config():
    # Frontend repo without runtime config
    files_map = {
        "package.json": '{"name": "dashboard", "dependencies": {"react": "^18.2.0", "react-dom": "^18.2.0"}}',
        "src/App.tsx": "const api = 'https://api.domain.com';",
        "doc/architecture.md": "doc",
        "doc/scope.md": "scope",
        "Dockerfile": "FROM node:20\nHEALTHCHECK CMD curl http://localhost:3000",
        ".gitlab-ci.yml": "test:\n  stage: test\n  script: npm test -- --coverage\nsonar:\n  stage: sonar\n  script: sonar\nbuild:\n  stage: build\n  script: docker build",
    }
    branches = [{"name": "main"}, {"name": "develop"}]

    checker = DeployRequirementsChecker(
        project_name="frontend/dashboard",
        repo_url="https://gitlab.example.com/frontend/dashboard.git",
        files_map=files_map,
        branches=branches
    )

    report = checker.run_all_checks(has_maintainer_access=True)
    check8 = next(c for c in report.checks if c.rule_id == 8)
    assert check8.status == CheckStatus.FAILED

    # Now add runtime config
    files_map["public/config.json"] = '{"API_URL": "DYNAMIC"}'
    checker2 = DeployRequirementsChecker(
        project_name="frontend/dashboard",
        repo_url="https://gitlab.example.com/frontend/dashboard.git",
        files_map=files_map,
        branches=branches
    )
    report2 = checker2.run_all_checks(has_maintainer_access=True)
    check8_2 = next(c for c in report2.checks if c.rule_id == 8)
    assert check8_2.status == CheckStatus.PASSED
