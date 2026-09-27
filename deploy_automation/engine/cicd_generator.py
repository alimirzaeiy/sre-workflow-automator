import re
from typing import Optional


class GitLabCICDGenerator:
    """
    Generates and injects deployment jobs (deploy-main, deploy-production)
    into .gitlab-ci.yml for automated runner execution on target servers.
    """

    @staticmethod
    def generate_deploy_jobs(
        project_name: str,
        project_dir: str,
        environment_type: str = "Main",
        runner_tags: Optional[list[str]] = None
    ) -> str:
        clean_name = re.sub(r"[^a-zA-Z0-9_-]", "-", project_name).lower()
        clean_dir = project_dir.strip("/")
        tag_var = re.sub(r"[^a-zA-Z0-9_]", "_", project_name).upper() + "_TAG"

        is_main = "main" in environment_type.lower()
        is_prod = "prod" in environment_type.lower()

        tags = runner_tags or ["shell"]
        tags_yaml = "\n".join(f"    - {t.strip()}" for t in tags if t.strip())
        if not tags_yaml:
            tags_yaml = "    - shell"

        jobs = []

        if is_main:
            deploy_main = f"""deploy-main:
  stage: deploy
  only:
    - main
  script:
    - docker login -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD $CI_REGISTRY
    - cd /srv/{clean_dir}
    - if docker compose version >/dev/null 2>&1; then docker compose pull {clean_name} && docker compose up -d --force-recreate {clean_name}; else docker-compose pull {clean_name} && docker-compose up -d --force-recreate {clean_name}; fi
  tags:
{tags_yaml}
"""
            jobs.append(deploy_main)

        if is_prod:
            deploy_prod = f"""deploy-production:
  stage: deploy
  only:
    - tags
  script:
    - docker login -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD $CI_REGISTRY
    - cd /srv/{clean_dir}
    - sed -i "s/^{tag_var}=.*/{tag_var}=$CI_COMMIT_TAG/" .env
    - if docker compose version >/dev/null 2>&1; then docker compose pull {clean_name} && docker compose up -d --force-recreate {clean_name}; else docker-compose pull {clean_name} && docker-compose up -d --force-recreate {clean_name}; fi
  tags:
{tags_yaml}
"""
            jobs.append(deploy_prod)

        return "\n".join(jobs)

    @staticmethod
    def merge_into_gitlab_ci(
        current_ci: str = "",
        project_name: str = "",
        project_dir: str = "",
        environment_type: str = "Main",
        runner_tags: Optional[list[str]] = None,
        current_ci_yaml: Optional[str] = None,
        **kwargs
    ) -> str:
        ci_content = current_ci_yaml if current_ci_yaml is not None else current_ci
        """
        Merges generated deploy jobs into existing .gitlab-ci.yml content.
        Ensures 'deploy' stage is present in stages list.
        Replaces any previously generated deploy-main / deploy-production jobs.
        """
        new_jobs = GitLabCICDGenerator.generate_deploy_jobs(
            project_name=project_name,
            project_dir=project_dir,
            environment_type=environment_type,
            runner_tags=runner_tags
        )

        if not ci_content or not ci_content.strip():
            return f"""stages:
  - test
  - build
  - deploy

{new_jobs}
"""

        updated_yaml = ci_content

        has_stages = bool(re.search(r"^\s*stages:\s*$", updated_yaml, re.MULTILINE))
        stages_has_deploy = bool(re.search(r"^\s*stages:\s*\n(?:[ \t]*-[ \t]*[^\n]+\n)*?[ \t]*-[ \t]*deploy\b", updated_yaml, re.MULTILINE))

        if has_stages and not stages_has_deploy:
            updated_yaml = re.sub(
                r"(^\s*stages:\s*\n(?:[ \t]*-[ \t]*[^\n]+\n)*)",
                r"\1  - deploy\n",
                updated_yaml,
                count=1,
                flags=re.MULTILINE
            )
        elif not has_stages:
            updated_yaml = f"stages:\n  - deploy\n\n" + updated_yaml

        # Remove existing deploy-main and deploy-production jobs if present
        is_main = "main" in environment_type.lower()
        is_prod = "prod" in environment_type.lower()

        if is_main:
            updated_yaml = re.sub(r"(?m)^deploy-main:\s*\n(?:(?:\s+.*|\s*)\n)*", "", updated_yaml)
        if is_prod:
            updated_yaml = re.sub(r"(?m)^deploy-production:\s*\n(?:(?:\s+.*|\s*)\n)*", "", updated_yaml)

        updated_yaml = updated_yaml.rstrip() + "\n\n" + new_jobs.strip() + "\n"
        return updated_yaml
