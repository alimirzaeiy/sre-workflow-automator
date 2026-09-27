from enum import Enum
from typing import Optional, Any
from deploy_automation.models import ClickUpTaskInfo
from deploy_automation.integrations.gitlab_service import GitLabService
from deploy_automation.integrations.clickup_service import ClickUpService


class DeploymentMilestone(str, Enum):
    ACCESS_CHECK = "ACCESS_CHECK"                  # 1. Maintainer Access
    REQUIREMENTS_CHECK = "REQUIREMENTS_CHECK"      # 2. Deploy Requirements Passed
    GITLAB_CI = "GITLAB_CI"                        # 3. .gitlab-ci.yml written
    DOCKER_COMPOSE = "DOCKER_COMPOSE"              # 4. docker-compose.yml created
    ENV_DB_LOG = "ENV_DB_LOG"                      # 5. Envs, DB, and Log Shipping done
    DEPLOY_TESTED = "DEPLOY_TESTED"                # 6. Deploy tested & verified


MILESTONE_LABELS = {
    DeploymentMilestone.ACCESS_CHECK: "1. GitLab Maintainer Access Check",
    DeploymentMilestone.REQUIREMENTS_CHECK: "2. Deploy Readiness Requirements Check",
    DeploymentMilestone.GITLAB_CI: "3. GitLab CI/CD Configuration (.gitlab-ci.yml)",
    DeploymentMilestone.DOCKER_COMPOSE: "4. Docker Compose Setup (docker-compose.yml)",
    DeploymentMilestone.ENV_DB_LOG: "5. Environments, Database & Log Shipping",
    DeploymentMilestone.DEPLOY_TESTED: "6. Deploy Tested & Completed",
}

MILESTONE_ORDER = [
    DeploymentMilestone.ACCESS_CHECK,
    DeploymentMilestone.REQUIREMENTS_CHECK,
    DeploymentMilestone.GITLAB_CI,
    DeploymentMilestone.DOCKER_COMPOSE,
    DeploymentMilestone.ENV_DB_LOG,
    DeploymentMilestone.DEPLOY_TESTED,
]


class TaskStateDetector:
    def __init__(self, gitlab_service: Optional[GitLabService] = None, clickup_service: Optional[ClickUpService] = None):
        self.gitlab_service = gitlab_service or GitLabService()
        self.clickup_service = clickup_service or ClickUpService()

    async def detect_milestone(self, task: ClickUpTaskInfo) -> tuple[DeploymentMilestone, str]:
        """
        Analyzes task comments, GitLab permissions, repo files, and DB state to deduce
        the current milestone of the deployment task.
        """
        # 1. Check local DB state first
        try:
            from deploy_automation.database import AsyncSessionLocal, ReviewSession
            from sqlalchemy import select
            if AsyncSessionLocal:
                async with AsyncSessionLocal() as session:
                    result = await session.execute(
                        select(ReviewSession).where(ReviewSession.task_id == task.task_id).order_by(ReviewSession.created_at.desc())
                    )
                    db_rec = result.scalars().first()
                    if db_rec:
                        if db_rec.status == "DEPLOY_COMPLETED":
                            return DeploymentMilestone.DEPLOY_TESTED, "Local database record is marked as DEPLOY_COMPLETED."
                        elif db_rec.status == "HANDOFF_LAPTOP":
                            return DeploymentMilestone.DOCKER_COMPOSE, "Requirements approved and handed off to laptop for docker-compose & deployment."
        except Exception:
            pass

        # 2. Check GitLab Maintainer Access
        if not task.repo_url:
            return DeploymentMilestone.ACCESS_CHECK, "Repository URL is missing in ClickUp task fields."

        has_access, err = await self.gitlab_service.check_maintainer_access(task.repo_url)
        if not has_access:
            return DeploymentMilestone.ACCESS_CHECK, f"Missing Maintainer access on GitLab ({err})"

        # 3. Check Comments on ClickUp Task
        comments = await self.clickup_service.get_task_comments(task.task_id)
        last_comment_text = ""
        if comments:
            last_comment_text = comments[0].get("comment_text", "").lower()

        if "دسترسی به ریپو" in last_comment_text or "maintainer" in last_comment_text:
            if not self.clickup_service.is_recheck_comment(last_comment_text):
                return DeploymentMilestone.ACCESS_CHECK, "Latest comment requested repository access."

        if "الزامات دیپلوی به طور کامل رعایت نشده است" in last_comment_text or "requirements" in last_comment_text:
            if not self.clickup_service.is_recheck_comment(last_comment_text):
                return DeploymentMilestone.REQUIREMENTS_CHECK, "Latest comment reported unresolved deployment requirements."

        # 4. Check GitLab Files
        project = await self.gitlab_service.get_project(task.repo_url)
        if project:
            try:
                files_map = await self.gitlab_service.get_repository_files_map(project)
                ci_content = files_map.get(".gitlab-ci.yml", "") or files_map.get(".gitlab-ci.yaml", "")
                has_deploy_job = "deploy-main" in ci_content or "deploy-production" in ci_content
                has_compose = any("docker-compose" in p.lower() for p in files_map.keys())

                if has_deploy_job:
                    try:
                        branches_list = await self.gitlab_service.get_branches_and_protection(project)
                        branch_names = [b["name"] for b in branches_list]
                    except Exception:
                        branch_names = []
                    target_branch = "main" if "main" in branch_names else ("master" if "master" in branch_names else (project.default_branch or "main"))
                    latest_p = await self.gitlab_service.get_latest_pipeline(project.id, target_branch)
                    if latest_p and latest_p.get("status") == "success":
                        return DeploymentMilestone.DEPLOY_TESTED, f"Deploy pipeline #{latest_p.get('id')} on '{target_branch}' succeeded."
                    elif latest_p and latest_p.get("status") in ["running", "pending"]:
                        return DeploymentMilestone.DEPLOY_TESTED, f"Deploy pipeline #{latest_p.get('id')} on '{target_branch}' is running."
                    elif has_compose:
                        return DeploymentMilestone.ENV_DB_LOG, "CI/CD deploy jobs and docker-compose found in repo."
                    else:
                        return DeploymentMilestone.ENV_DB_LOG, "CI/CD deploy jobs configured in .gitlab-ci.yml."
                else:
                    return DeploymentMilestone.GITLAB_CI, "Requirements approved; next step is configuring .gitlab-ci.yml deploy jobs."
            except Exception:
                pass

        return DeploymentMilestone.REQUIREMENTS_CHECK, "GitLab access confirmed; ready for requirements audit."
