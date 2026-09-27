import unittest
from deploy_automation.engine.checker import DeployRequirementsChecker
from deploy_automation.models import CheckStatus, ProjectReport, CheckItemResult
from deploy_automation.integrations.gitlab_service import GitLabService
from deploy_automation.integrations.clickup_service import ClickUpService
from deploy_automation.utils.text_helpers import format_clickup_final_comment, format_telegram_review_message


class TestDeployAutomation(unittest.TestCase):
    def test_checker_all_passed(self):
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
        ]

        checker = DeployRequirementsChecker(
            project_name="core/user-service",
            repo_url="https://gitlab.example.com/core/user-service.git",
            files_map=files_map,
            branches=branches
        )

        report = checker.run_all_checks(has_maintainer_access=True)

        self.assertTrue(report.has_maintainer_access)
        self.assertTrue(report.all_passed)
        self.assertEqual(len(report.checks), 9)
        for c in report.checks:
            self.assertTrue(c.passed)

    def test_checker_missing_docs_and_git_flow(self):
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

        self.assertFalse(report.all_passed)
        
        # Check 1: Architecture
        check1 = next(c for c in report.checks if c.rule_id == 1)
        self.assertEqual(check1.status, CheckStatus.FAILED)
        self.assertIn("doc/", check1.remediation)

        # Check 2: Git Flow
        check2 = next(c for c in report.checks if c.rule_id == 2)
        self.assertEqual(check2.status, CheckStatus.FAILED)
        self.assertIn("develop", check2.details)

    def test_extract_project_path(self):
        service = GitLabService(token="test")
        self.assertEqual(service.extract_project_path("https://gitlab.example.com/group/subgroup/project.git"), "group/subgroup/project")
        self.assertEqual(service.extract_project_path("git@gitlab.example.com:core/auth-service.git"), "core/auth-service")

    def test_recheck_comment_detection(self):
        service = ClickUpService(api_token="test")
        self.assertTrue(service.is_recheck_comment("سلام، الزامات دیپلوی رعایت شد لطفا بررسی کنید"))
        self.assertTrue(service.is_recheck_comment("موارد مطرح شده برطرف شد"))
        self.assertFalse(service.is_recheck_comment("این یک پیام عادی است"))

    def test_format_texts(self):
        comment = format_clickup_final_comment("خطای تست", "sre_lead")
        self.assertIn("@sre_lead", comment)
        self.assertIn("الزامات دیپلوی به طور کامل رعایت نشده است:", comment)

    def test_cicd_generator_tags_and_replace(self):
        from deploy_automation.engine.cicd_generator import GitLabCICDGenerator
        
        # Test with custom runner tags
        jobs = GitLabCICDGenerator.generate_deploy_jobs("notification", "notification", "Main", runner_tags=["production-runner", "shell"])
        self.assertIn("- production-runner", jobs)
        self.assertIn("- shell", jobs)
        self.assertIn("docker compose pull notification", jobs)

        # Test merge and replace existing job
        initial_ci = """stages:
  - build
deploy-main:
  stage: deploy
  script:
    - echo old
  tags:
    - old-tag
"""
        merged = GitLabCICDGenerator.merge_into_gitlab_ci(initial_ci, "notification", "notification", "Main", runner_tags=["production-runner"])
        self.assertIn("- production-runner", merged)
        self.assertNotIn("- old-tag", merged)
        self.assertIn("deploy-main:", merged)
        self.assertIn("- deploy", merged)


if __name__ == "__main__":
    unittest.main()
