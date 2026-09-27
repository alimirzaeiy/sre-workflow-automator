import os
import sys
import json
import asyncio
import argparse
import re
from typing import Optional, Any
from deploy_automation.config import settings
from deploy_automation.database import (
    init_db,
    get_pending_handoff_sessions,
    update_session_status,
    get_review_session,
    create_review_session
)
from deploy_automation.integrations.ssh_service import SSHService
from deploy_automation.integrations.gitlab_service import GitLabService
from deploy_automation.integrations.clickup_service import ClickUpService
from deploy_automation.integrations.nginx_service import NginxService
from deploy_automation.integrations.postgres_service import PostgresService
from deploy_automation.integrations.ai_service import AIService
from deploy_automation.engine.compose_generator import DockerComposeGenerator
from deploy_automation.engine.cicd_generator import GitLabCICDGenerator
from deploy_automation.engine.task_state_detector import (
    TaskStateDetector,
    DeploymentMilestone,
    MILESTONE_LABELS,
    MILESTONE_ORDER
)
from deploy_automation.models import CheckStatus

DEPLOY_SERVER_STEPS = {
    1: "Server Connection & Runner Check",
    2: "Database Provisioning",
    3: "Environment Variables Setup",
    4: "Docker Compose Setup",
    5: "GitLab CI/CD Configuration & MR",
    6: "Pipeline Monitoring",
    7: "Nginx & Reverse Proxy Setup",
}

class ComprehensiveLaptopDeployer:
    def __init__(self):
        self.ssh_service = SSHService()
        self.gitlab_service = GitLabService()
        self.clickup_service = ClickUpService()
        self.nginx_service = NginxService(self.ssh_service)
        self.postgres_service = PostgresService(self.ssh_service)
        self.detector = TaskStateDetector(self.gitlab_service, self.clickup_service)
        self.ai_service = AIService()

    async def select_server(self, prompt_text: str = "Select target server:") -> tuple[str, Optional[str]]:
        hosts = self.ssh_service.list_ssh_hosts()
        
        server_choice = input(f"\n{prompt_text} (number from ~/.ssh/config, host name, or user@host): ").strip()

        # Parse user@host format (e.g. root@10.1.25.8)
        ssh_user = None
        target_server = server_choice
        if "@" in server_choice:
            parts = server_choice.split("@", 1)
            ssh_user = parts[0].strip() or None
            target_server = parts[1].strip()
        elif server_choice.isdigit() and 1 <= int(server_choice) <= len(hosts):
            target_server = hosts[int(server_choice) - 1]

        if ssh_user is None:
            ssh_user_input = input(f"SSH Username for {target_server} (default: ubuntu/root or from config): ").strip()
            ssh_user = ssh_user_input if ssh_user_input else None

        # Validate SSH connectivity
        target_display = f"{ssh_user}@{target_server}" if ssh_user else target_server
        print(f"⏳ Testing SSH connection to {target_display}...")
        code, out, err = await self.ssh_service.execute_remote_cmd(target_server, ssh_user, "echo SSH_OK")
        if code != 0 or "SSH_OK" not in out:
            print(f"❌ SSH connection to {target_display} failed!")
            if err.strip():
                print(f"   Error: {err.strip()}")
            retry = input("Do you want to try a different user or host? [Y/n]: ").strip().lower()
            if retry not in ["n", "no"]:
                return await self.select_server(prompt_text)
            raise ConnectionError(f"Cannot connect to {target_display} via SSH.")
        print(f"✅ SSH connection to {target_display} established.")

        return target_server, ssh_user


    async def execute_postgres_flow(self, project_name: str, app_internal_ip: str) -> Optional[str]:
        """
        Postgres database & user provisioning workflow.
        """
        print("\n" + "=" * 60)
        print("🐘 PostgreSQL Database Provisioning Setup")
        print("=" * 60)

        pg_host, pg_user = await self.select_server("Which server hosts PostgreSQL?")
        
        print(f"⏳ Checking PostgreSQL installation on {pg_host}...")
        pg_info = await self.postgres_service.detect_postgres_setup(pg_host, pg_user)
        print(f"✅ Postgres Mode: {pg_info.get('mode')} (Container: {pg_info.get('container_name') or 'N/A'})")

        default_db = project_name.replace("-", "_").lower()
        dbname = input(f"Database name (default: {default_db}): ").strip() or default_db
        dbuser = input(f"Database username (default: {default_db}_user): ").strip() or f"{default_db}_user"
        
        auto_pass = self.postgres_service.generate_secure_password()
        dbpass = input(f"Database password (or press Enter for auto-generated secure password): ").strip() or auto_pass

        ssl_choice = input("Require SSL connection for Database? [y/N]: ").strip().lower()
        ssl_required = ssl_choice in ["y", "yes"]

        print(f"⏳ Creating database, user, and updating pg_hba.conf for IP {app_internal_ip}...")
        ok, dsn, msg = await self.postgres_service.provision_database(
            host=pg_host,
            user=pg_user,
            dbname=dbname,
            dbuser=dbuser,
            dbpass=dbpass,
            app_internal_ip=app_internal_ip,
            pg_info=pg_info,
            ssl_required=ssl_required
        )

        if ok:
            print("\n" + "✅ " * 15)
            print(msg)
            print(f"🔑 Database Connection DSN:\n{dsn}")
            print("✅ " * 15)
            return dsn
        else:
            print(f"❌ Database provisioning failed: {msg}")
            return None

    async def execute_nginx_flow(self, app_internal_ip: str, app_port: int) -> tuple[Optional[str], Optional[str]]:
        """
        Nginx reverse proxy, SSL certbot, and path routing workflow.
        """
        print("\n" + "=" * 60)
        print("🌐 Nginx Reverse Proxy & SSL (Certbot) Setup")
        print("=" * 60)

        nginx_host, nginx_user = await self.select_server("Which server hosts Nginx?")

        print(f"⏳ Detecting Nginx environment on {nginx_host}...")
        nginx_info = await self.nginx_service.detect_nginx_setup(nginx_host, nginx_user)
        print(f"✅ Nginx Mode: {nginx_info.get('mode')} (Config path: {nginx_info.get('conf_dir') or nginx_info.get('sites_available')})")

        domain = input("\nEnter Domain name / URL (e.g., api.domain.com): ").strip()
        path = input("Path prefix if applicable (e.g. /api/v1 or Enter for root /): ").strip() or "/"

        existing_conf_path, existing_conf_content = await self.nginx_service.find_domain_config(nginx_host, nginx_user, domain, nginx_info)
        has_ssl_cert = await self.nginx_service.check_ssl_cert_exists(nginx_host, nginx_user, domain, existing_conf_content=existing_conf_content)

        if existing_conf_path:
            print(f"💡 Found existing Nginx configuration for '{domain}': {existing_conf_path}")
            print(f"👉 New location '{path}' will be inserted into the existing SSL/443 server block.")

        cert_ok = has_ssl_cert
        if has_ssl_cert:
            print(f"✅ Existing SSL certificate detected for '{domain}'. Skipping Certbot issuance.")
        else:
            cert_method = input("\nCertbot Challenge method? [1: HTTP Challenge (default), 2: DNS Challenge]: ").strip()
            challenge_type = "dns" if cert_method == "2" else "http"

            print(f"⏳ Requesting SSL Certificate for {domain}...")
            cert_ok, cert_msg = await self.nginx_service.issue_ssl_certificate(
                host=nginx_host,
                user=nginx_user,
                domain=domain,
                challenge_type=challenge_type,
                nginx_mode=nginx_info.get("mode", "daemon")
            )
            print(f"📢 {cert_msg}")

        print(f"\n⏳ Applying Nginx reverse proxy configuration...")
        apply_ok, apply_msg = await self.nginx_service.apply_nginx_config(
            host=nginx_host,
            user=nginx_user,
            domain=domain,
            path=path,
            target_ip=app_internal_ip,
            target_port=app_port,
            nginx_info=nginx_info,
            ssl_enabled=cert_ok
        )

        if apply_ok:
            print("\n" + "🎉 " * 15)
            print(apply_msg)
            print(f"🌍 Public Route URL: https://{domain}{path} -> http://{app_internal_ip}:{app_port}")
            print("🎉 " * 15)
            return domain, path
        else:
            print(f"❌ {apply_msg}")
            return None, None

    async def create_and_merge_cicd_mrs(
        self,
        project_obj: Any,
        project_name: str,
        def_branch: str,
        target_deploy_branch: str,
        updated_ci: str,
        commit_message: str,
        mr_title: str
    ) -> bool:
        import time
        new_branch = f"fix/{project_name}-ci-{int(time.time())}"
        print(f"\n🌿 Creating branch '{new_branch}' from '{def_branch}'...")
        ok_br, br_res = await self.gitlab_service.create_branch(project_obj.id, new_branch, def_branch)
        if not ok_br:
            print(f"❌ Failed to create branch '{new_branch}': {br_res}")
            return False

        try:
            try:
                await project_obj.files.get(file_path=".gitlab-ci.yml", ref=new_branch)
                file_exists = True
            except Exception:
                file_exists = False

            if file_exists:
                await self.gitlab_service.update_file(
                    project_id=project_obj.id,
                    file_path=".gitlab-ci.yml",
                    content=updated_ci,
                    branch=new_branch,
                    commit_message=commit_message
                )
            else:
                await self.gitlab_service.create_file(
                    project_id=project_obj.id,
                    data={
                        "file_path": ".gitlab-ci.yml",
                        "branch": new_branch,
                        "content": updated_ci,
                        "commit_message": commit_message
                    }
                )
            print(f"✅ Committed changes to branch '{new_branch}'.")
        except Exception as e:
            print(f"❌ Failed to commit to branch '{new_branch}': {e}")
            return False

        # 1. Create Merge Request into default branch (e.g. dev)
        ok_mr, mr_data = await self.gitlab_service.create_merge_request(
            project_id=project_obj.id,
            source_branch=new_branch,
            target_branch=def_branch,
            title=mr_title
        )
        if not ok_mr:
            print(f"❌ Failed to create Merge Request: {mr_data}")
            return False

        mr_url = mr_data.get("web_url", "N/A")
        mr_iid = mr_data.get("iid")

        print("\n" + "🔗 " * 15)
        print(f"🚀 Merge Request [1/2] ({new_branch} -> {def_branch}) created successfully!")
        print(f"👉 MR URL: {mr_url}")
        print("🔗 " * 15)

        while True:
            m_resp = input(f"\nPress Enter once you have merged MR [1/2] into '{def_branch}' (or type 'cancel' to abort): ").strip().lower()
            if m_resp in ["cancel", "c"]:
                print("❌ Deployment aborted.")
                return False
            mr_state = await self.gitlab_service.check_merge_request_status(project_obj.id, mr_iid)
            if mr_state == "merged":
                print(f"✅ Merge Request [1/2] confirmed MERGED into '{def_branch}'!")
                break
            else:
                print(f"⚠️ MR status is currently '{mr_state}'. Please merge the MR on GitLab to continue.")

        # 2. If target_deploy_branch != def_branch, create MR [2/2] (e.g. dev -> main)
        if def_branch != target_deploy_branch:
            print(f"\n🌿 Creating second Merge Request ({def_branch} -> {target_deploy_branch})...")
            ok_mr2, mr2_data = await self.gitlab_service.get_or_create_merge_request(
                project_id=project_obj.id,
                source_branch=def_branch,
                target_branch=target_deploy_branch,
                title=f"Release {project_name} updates from {def_branch} to {target_deploy_branch}"
            )
            if not ok_mr2:
                print(f"❌ Failed to create Merge Request to {target_deploy_branch}: {mr2_data}")
                return False

            mr2_url = mr2_data.get("web_url", "N/A")
            mr2_iid = mr2_data.get("iid")

            print("\n" + "🔗 " * 15)
            print(f"🚀 Merge Request [2/2] ({def_branch} -> {target_deploy_branch}) is ready!")
            print(f"👉 MR URL: {mr2_url}")
            print("🔗 " * 15)

            while True:
                m_resp = input(f"\nPress Enter once you have merged MR [2/2] into '{target_deploy_branch}' (or type 'cancel' to abort): ").strip().lower()
                if m_resp in ["cancel", "c"]:
                    print("❌ Deployment aborted.")
                    return False
                mr2_state = await self.gitlab_service.check_merge_request_status(project_obj.id, mr2_iid)
                if mr2_state == "merged":
                    print(f"✅ Merge Request [2/2] confirmed MERGED into '{target_deploy_branch}'!")
                    break
                else:
                    print(f"⚠️ MR status is currently '{mr2_state}'. Please merge the MR on GitLab to continue.")

        return True

    async def execute_ai_pipeline_fix(
        self,
        project_obj: Any,
        task_info: Any,
        target_server: str,
        ssh_user: Optional[str],
        project_name: str,
        project_dir: str,
        failed_job: dict,
        error_trace: str,
        target_deploy_branch: str,
        current_pipeline_id: int
    ) -> tuple[bool, Optional[int]]:
        """
        Gathers context (.gitlab-ci.yml, docker-compose.yml, .env, and job trace),
        queries the AI, proposes server and/or CI changes, and executes them with user confirmation.
        """
        print("\n" + "=" * 60)
        print("🤖 AI Diagnosis & Automated Fix Assistant")
        print("=" * 60)

        # 1. Collect context
        print("⏳ Collecting context (.gitlab-ci.yml, docker-compose.yml, .env)...")
        current_ci = ""
        try:
            f_ci = await project_obj.files.get(file_path=".gitlab-ci.yml", ref=target_deploy_branch)
            current_ci = f_ci.decode().decode("utf-8")
        except Exception:
            try:
                def_b = project_obj.default_branch or "main"
                f_ci = await project_obj.files.get(file_path=".gitlab-ci.yml", ref=def_b)
                current_ci = f_ci.decode().decode("utf-8")
            except Exception:
                current_ci = ""

        docker_compose = await self.ssh_service.read_remote_file(
            target_server, ssh_user, f"/srv/{project_dir}/docker-compose.yml"
        ) or ""

        env_content = await self.ssh_service.read_remote_file(
            target_server, ssh_user, f"/srv/{project_dir}/.env"
        ) or ""

        print("🤖 Asking AI to diagnose error and propose fixes...")
        ai_res = self.ai_service.diagnose_and_propose_fix(
            project_name=project_name,
            repo_url=task_info.repo_url,
            failed_job_name=failed_job.get("name", "deploy"),
            error_trace=error_trace,
            gitlab_ci=current_ci,
            docker_compose=docker_compose,
            env_content=env_content
        )

        if not ai_res:
            print("⚠️ AI was unable to generate an automated diagnosis.")
            return False, None

        diagnosis = ai_res.get("diagnosis", "")
        fix_location = (ai_res.get("fix_location") or "GITLAB_CI").upper()
        server_changes = ai_res.get("server_changes") or {}
        gitlab_ci_changes = ai_res.get("gitlab_ci_changes") or {}

        print("\n" + "💡 " * 15)
        print(f"📋 AI Diagnosis:\n{diagnosis}")
        print(f"🎯 Fix Target: {fix_location}")
        print("💡 " * 15)

        # 2. Server Changes (if any)
        server_modified = False
        has_server_edits = (
            server_changes.get("docker_compose_changed")
            or server_changes.get("env_changed")
            or server_changes.get("commands")
        )
        if fix_location in ["SERVER", "BOTH"] or has_server_edits:
            print("\n🖥️  Proposed Server Changes:")
            if server_changes.get("docker_compose_changed") and server_changes.get("updated_docker_compose"):
                print(f"  - Update /srv/{project_dir}/docker-compose.yml")
            if server_changes.get("env_changed") and server_changes.get("updated_env"):
                print(f"  - Update /srv/{project_dir}/.env")
            if server_changes.get("commands"):
                print(f"  - Execute commands: {', '.join(server_changes.get('commands'))}")

            apply_srv = input(f"\nApply these changes to server '{target_server}'? [Y/n]: ").strip().lower()
            if apply_srv in ["", "y", "yes"]:
                if server_changes.get("docker_compose_changed") and server_changes.get("updated_docker_compose"):
                    await self.ssh_service.write_remote_compose(
                        target_server, ssh_user, project_dir, server_changes["updated_docker_compose"]
                    )
                    print("✅ Updated docker-compose.yml on server.")
                if server_changes.get("env_changed") and server_changes.get("updated_env"):
                    await self.ssh_service.write_project_env_file(
                        target_server, ssh_user, project_dir, server_changes["updated_env"]
                    )
                    print("✅ Updated .env on server.")
                if server_changes.get("commands"):
                    for cmd in server_changes["commands"]:
                        print(f"⏳ Running remote command: {cmd}")
                        code, out, err = await self.ssh_service.execute_remote_cmd(target_server, ssh_user, cmd)
                        if code != 0:
                            print(f"⚠️ Command error: {err.strip()}")
                        else:
                            print(f"✅ Command output: {out.strip()}")
                server_modified = True
            else:
                print("⏩ Skipped server changes.")

        # 3. If fix_location was purely SERVER (or only server was modified and no gitlab-ci changes):
        if fix_location == "SERVER" or (server_modified and not gitlab_ci_changes.get("changed")):
            retry_conf = input(f"\nRetry the failed job '{failed_job.get('name')}' now? [Y/n]: ").strip().lower()
            if retry_conf in ["", "y", "yes"]:
                print(f"🔄 Retrying job '{failed_job.get('name')}'...")
                await self.gitlab_service.retry_job(project_obj.id, failed_job.get("id"))
                await asyncio.sleep(4)
                return True, current_pipeline_id
            return False, None

        # 4. GitLab CI Changes
        if gitlab_ci_changes.get("changed") and gitlab_ci_changes.get("updated_gitlab_ci"):
            updated_ci = gitlab_ci_changes["updated_gitlab_ci"]
            print("\n" + "=" * 50)
            print("📄 Proposed .gitlab-ci.yml Preview:")
            print("=" * 50)
            print("\n".join(updated_ci.split("\n")[:40]))
            if len(updated_ci.split("\n")) > 40:
                print("... [truncated]")
            print("=" * 50)

            apply_ci = input(f"\nCreate branch and Merge Request with these .gitlab-ci.yml changes? [Y/n]: ").strip().lower()
            if apply_ci in ["", "y", "yes"]:
                def_branch = project_obj.default_branch or "main"
                mr_ok = await self.create_and_merge_cicd_mrs(
                    project_obj=project_obj,
                    project_name=project_name,
                    def_branch=def_branch,
                    target_deploy_branch=target_deploy_branch,
                    updated_ci=updated_ci,
                    commit_message=f"fix(ci): fix deploy job failure for {project_name}",
                    mr_title=f"Fix deploy job for {project_name}"
                )
                if not mr_ok:
                    return False, None

                # Wait for new pipeline on target_deploy_branch
                old_pipeline_id = current_pipeline_id
                print(f"\n🔍 Searching for newly triggered pipeline on '{target_deploy_branch}' (newer than #{old_pipeline_id})...")
                new_pipeline = None
                for _ in range(18):
                    latest = await self.gitlab_service.get_latest_pipeline(project_obj.id, target_deploy_branch)
                    if latest and latest.get("id") != old_pipeline_id:
                        new_pipeline = latest
                        break
                    await asyncio.sleep(4)

                if new_pipeline:
                    new_pid = new_pipeline.get("id")
                    print(f"🚀 Found new Pipeline #{new_pid} ({new_pipeline.get('web_url', '')})!")
                    return True, new_pid
                else:
                    manual_pid = input(f"⚠️ Could not automatically detect a new pipeline on '{target_deploy_branch}'. Enter new Pipeline ID manually: ").strip()
                    if manual_pid.isdigit():
                        return True, int(manual_pid)

        return False, None

    async def inspect_server_deployment_state(
        self,
        target_server: str,
        ssh_user: Optional[str],
        project_name: str,
        project_dir: str,
        task_info: Any
    ) -> tuple[int, dict[int, str]]:
        service_needs = task_info.service_needs or []
        statuses: dict[int, str] = {}
        inferred_step = 1

        # Step 1: Server & Runner Check
        has_runner, _ = await self.ssh_service.check_gitlab_runner_shell(target_server, ssh_user)
        if has_runner:
            statuses[1] = "[✅ Completed]"
        else:
            statuses[1] = "[❌ Runner not found]"
            for s in range(2, 8):
                statuses[s] = "[⬜ Pending]"
            return 1, statuses

        # Step 2: Database Setup
        has_db = any("database" in sn.lower() for sn in service_needs)
        db_completed = False
        if not has_db:
            statuses[2] = "[➖ Skipped (Not needed)]"
            db_completed = True
        else:
            code, out, _ = await self.ssh_service.execute_remote_cmd(
                target_server, ssh_user,
                f"[ -f /srv/{project_dir}/.env ] && grep -qi 'DATABASE_URL' /srv/{project_dir}/.env && echo 'FOUND'"
            )
            if "FOUND" in out:
                statuses[2] = "[✅ Completed]"
                db_completed = True
            else:
                statuses[2] = "[⏳ Pending]"
                if inferred_step == 1:
                    inferred_step = 2

        # Step 3: Environment Variables (.env) Setup
        env_completed = False
        code, out, _ = await self.ssh_service.execute_remote_cmd(
            target_server, ssh_user,
            f"[ -s /srv/{project_dir}/.env ] && echo 'FOUND'"
        )
        if "FOUND" in out:
            statuses[3] = "[✅ Completed]"
            env_completed = True
        else:
            statuses[3] = "[⏳ Pending]"
            if inferred_step == 1:
                inferred_step = 3

        # Step 4: Docker Compose Setup
        compose_completed = False
        code, out, _ = await self.ssh_service.execute_remote_cmd(
            target_server, ssh_user,
            f"[ -f /srv/{project_dir}/docker-compose.yml ] && grep -qi '{project_name}' /srv/{project_dir}/docker-compose.yml && echo 'FOUND'"
        )
        if "FOUND" in out:
            statuses[4] = "[✅ Completed]"
            compose_completed = True
        else:
            statuses[4] = "[⏳ Pending]"
            if inferred_step == 1:
                inferred_step = 4

        # Step 5: GitLab CI/CD (.gitlab-ci.yml)
        ci_completed = False
        project_obj = await self.gitlab_service.get_project(task_info.repo_url)
        if project_obj:
            def_branch = project_obj.default_branch or "main"
            try:
                f = await project_obj.files.get(file_path=".gitlab-ci.yml", ref=def_branch)
                ci_content = f.decode().decode("utf-8")
                if project_name in ci_content or f"/srv/{project_dir}" in ci_content:
                    statuses[5] = "[✅ Completed]"
                    ci_completed = True
                else:
                    statuses[5] = "[⏳ Pending]"
                    if inferred_step == 1:
                        inferred_step = 5
            except Exception:
                statuses[5] = "[⏳ Pending]"
                if inferred_step == 1:
                    inferred_step = 5
        else:
            statuses[5] = "[⏳ Pending]"
            if inferred_step == 1:
                inferred_step = 5

        # Step 6: Pipeline Monitoring
        pipeline_completed = False
        if project_obj and ci_completed:
            try:
                branches_list = await self.gitlab_service.get_branches_and_protection(project_obj)
                branch_names = [b["name"] for b in branches_list]
            except Exception:
                branch_names = []
            target_deploy_branch = "main" if "main" in branch_names else ("master" if "master" in branch_names else (project_obj.default_branch or "main"))
            pipeline = await self.gitlab_service.get_latest_pipeline(project_obj.id, target_deploy_branch)
            if pipeline and pipeline.get("status") == "success":
                statuses[6] = "[✅ Completed]"
                pipeline_completed = True
            else:
                statuses[6] = "[⏳ Pending]"
                if inferred_step == 1:
                    inferred_step = 6
        else:
            statuses[6] = "[⬜ Pending]"
            if inferred_step == 1 and ci_completed:
                inferred_step = 6

        # Step 7: Nginx Reverse Proxy
        has_public = any("public" in sn.lower() for sn in service_needs)
        if not has_public:
            statuses[7] = "[➖ Skipped (Not needed)]"
        else:
            statuses[7] = "[⬜ Pending]"
            if inferred_step == 1 and pipeline_completed:
                inferred_step = 7

        if inferred_step == 1 and env_completed and compose_completed:
            if not ci_completed:
                inferred_step = 5
            elif not pipeline_completed:
                inferred_step = 6
            elif has_public:
                inferred_step = 7

        return inferred_step, statuses

    async def run_change_env_flow(self):
        """
        Interactive flow for changing environment variables on a server
        based on a ClickUp Change task's Envs field.
        """
        print("\n" + "=" * 60)
        print("🔧 Change Environmental Variables")
        print("=" * 60)

        print("⏳ Fetching active Change tasks from ClickUp...")
        tasks = await self.clickup_service.get_active_change_tasks()

        if not tasks:
            print("❌ No active Change tasks found.")
            return

        print(f"\n📋 Active Change Tasks ({len(tasks)} found):")
        for i, t in enumerate(tasks, 1):
            assignees_str = ", ".join(t.assignees) if t.assignees else "—"
            env_str = f" | 🏞️ {t.environment}" if t.environment else ""
            print(f"  [{i}] {t.task_name}{env_str}")
            print(f"       Assignees: {assignees_str}")

        task_choice = input(f"\nSelect a task number [1-{len(tasks)}] (or 0 to cancel): ").strip()
        if not task_choice.isdigit() or int(task_choice) == 0:
            print("❌ Cancelled.")
            return
        idx = int(task_choice)
        if idx < 1 or idx > len(tasks):
            print("❌ Invalid selection.")
            return

        task = tasks[idx - 1]
        print(f"\n✅ Selected: {task.task_name}")

        # Clean assignees and watchers (keep only me)
        current_status = (task.status or "").lower()
        print("\n⏳ Cleaning up assignees and followers (keeping only me)...")
        confirm_clean = input("Remove other assignees and followers? [Y/n]: ").strip().lower()
        if confirm_clean in ["", "y", "yes"]:
            cleaned = await self.clickup_service.clean_assignees_and_watchers(
                task.task_id, reporter_id=task.reporter_id
            )
            print("✅ Cleaned." if cleaned else "⚠️ Could not clean assignees.")

        # Set task to in progress
        if current_status != "in progress":
            confirm_ip = input(f"\nUpdate task status to 'in progress'? [Y/n]: ").strip().lower()
            if confirm_ip in ["", "y", "yes"]:
                ok = await self.clickup_service.update_task_status(task.task_id, "in progress")
                print("✅ Task status updated to 'in progress'." if ok else "⚠️ Failed to update status.")

        # Select server
        target_server, ssh_user = await self.select_server("Select target server for env change")
        if not target_server:
            print("❌ No server selected.")
            return

        # Ask for env file path on server
        env_file_path = input("\nPath to .env file on server (e.g. /srv/api/.env): ").strip()
        if not env_file_path:
            print("❌ No path provided.")
            return

        # Read current env file from server
        print(f"\n⏳ Reading {env_file_path} from server...")
        current_content = await self.ssh_service.read_remote_file(target_server, ssh_user, env_file_path) or ""
        if not current_content:
            print(f"⚠️ File is empty or could not be read. Will create/append new content.")

        # Parse task's Envs field
        envs_raw = task.envs
        if not envs_raw:
            # Fallback: show all custom fields and let user pick
            print("\n⚠️ No 'Envs' field found in task. Custom fields available:")
            for k, v in (task.custom_fields or {}).items():
                if v:
                    print(f"  {k}: {str(v)[:80]}")
            envs_raw = input("\nPaste env vars manually (KEY=VALUE, one per line, END to finish):\n").strip()
            lines_manual = []
            while True:
                try:
                    line = input()
                except EOFError:
                    break
                if line.strip().upper() in ["END", "EOF"]:
                    break
                lines_manual.append(line)
            envs_raw = "\n".join(lines_manual)

        # Parse key=value pairs from envs_raw
        task_env_pairs = {}
        for raw_line in (envs_raw or "").splitlines():
            raw_line = raw_line.strip()
            if not raw_line or raw_line.startswith("#"):
                continue
            if "=" in raw_line:
                k, v = raw_line.split("=", 1)
                task_env_pairs[k.strip()] = v.strip()
            else:
                # Key only — ask for value
                val_input = input(f"  Value for '{raw_line}': ").strip()
                task_env_pairs[raw_line] = val_input

        if not task_env_pairs:
            print("❌ No key=value pairs found. Aborting.")
            return

        # Build new env file content with changes applied
        current_lines = current_content.splitlines()
        new_lines = list(current_lines)
        applied_keys = set()
        changes_summary = []

        for key, new_val in task_env_pairs.items():
            found = False
            for i, line in enumerate(new_lines):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "=" in stripped:
                    lk = stripped.split("=", 1)[0].strip()
                    if lk == key:
                        # Comment out old line and insert new one after it
                        new_lines[i] = f"# {line}  # commented by deploy-automation"
                        new_lines.insert(i + 1, f"{key}={new_val}")
                        changes_summary.append(f"  ✏️  UPDATED: {key}={new_val}  (old: {stripped.split('=',1)[1].strip()[:40]})")
                        applied_keys.add(key)
                        found = True
                        break
            if not found:
                new_lines.append(f"{key}={new_val}")
                changes_summary.append(f"  ➕ ADDED:   {key}={new_val}")
                applied_keys.add(key)

        new_content = "\n".join(new_lines)

        # Show diff/preview
        print("\n" + "=" * 60)
        print("📋 Proposed changes to env file:")
        print("=" * 60)
        for summary_line in changes_summary:
            print(summary_line)
        print("=" * 60)
        print("\n📄 Final file preview (last 30 lines):")
        preview_lines = new_content.splitlines()
        for pl in preview_lines[-30:]:
            print(f"  {pl}")
        print("=" * 60)

        confirm_apply = input("\nApply these changes? [Y/n]: ").strip().lower()
        if confirm_apply not in ["", "y", "yes"]:
            print("❌ Changes not applied.")
            return

        # Offer nano for manual editing
        use_nano = input("\nDo you want to manually edit the file with nano before saving? [y/N]: ").strip().lower()
        if use_nano in ["y", "yes"]:
            import tempfile, subprocess
            with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False, encoding="utf-8") as tmp:
                tmp.write(new_content)
                tmp_path = tmp.name
            subprocess.call(["nano", tmp_path])
            with open(tmp_path, "r", encoding="utf-8") as f:
                new_content = f.read()
            import os as _os
            _os.unlink(tmp_path)
            print("✅ Manual edits applied.")

        # Write file to server via SSH
        print(f"\n⏳ Writing changes to {env_file_path} on server...")
        import base64
        encoded = base64.b64encode(new_content.encode("utf-8")).decode("ascii")
        cmd = f"echo '{encoded}' | base64 -d > {env_file_path}"
        code, out, err = await self.ssh_service.execute_remote_cmd(target_server, ssh_user, cmd)
        if code == 0:
            print("✅ File updated successfully on server.")
        else:
            print(f"❌ Failed to write file: {err}")
            return

        # Post final comment and set ready to test
        reporter_display = f"@{task.reporter_username}" if task.reporter_username else ""
        final_comment = (
            "وقت به خیر\n"
            "تغییرات مورد نظر داده شد\n"
            "با دیپلوی بعدی اعمال می‌شود\n"
        )
        if reporter_display:
            final_comment += reporter_display

        print("\n" + "=" * 60)
        print("📝 Proposed ClickUp comment:")
        print(final_comment)
        print("=" * 60)
        confirm_comment = input("\nPost this comment and set task to 'ready to test'? [Y/n]: ").strip().lower()
        if confirm_comment in ["", "y", "yes"]:
            if task.reporter_id:
                posted = await self.clickup_service.post_tagged_comment(
                    task_id=task.task_id,
                    reporter_id=task.reporter_id,
                    reporter_username=task.reporter_username,
                    body_text="وقت به خیر\nتغییرات مورد نظر داده شد\nبا دیپلوی بعدی اعمال می‌شود"
                )
            else:
                posted = await self.clickup_service.post_comment(task.task_id, final_comment)

            if posted:
                print("✅ Comment posted.")
            else:
                print("⚠️ Failed to post comment.")

            ok = await self.clickup_service.update_task_status(task.task_id, "ready to test")
            print("✅ Task set to 'ready to test'." if ok else "⚠️ Failed to update task status.")
        else:
            print("⏩ Skipped comment and status update.")

    async def process_task_flow(self, task_info, start_milestone: DeploymentMilestone = DeploymentMilestone.ACCESS_CHECK):
        """
        Executes deployment steps starting from the chosen milestone.
        """
        project_name = self.gitlab_service.extract_project_path(task_info.repo_url).split("/")[-1] if task_info.repo_url else "app"
        clean_name = project_name.replace("-", "_").lower()
        service_needs = task_info.service_needs or []
        environment_type = task_info.environment or "Main"

        print("\n" + "=" * 60)
        print(f"🚀 Deploying Project: {project_name}")
        print(f"📌 Starting Milestone: {MILESTONE_LABELS[start_milestone]}")
        print("=" * 60)

        # Step 1: Check Access
        if start_milestone == DeploymentMilestone.ACCESS_CHECK:
            has_access, err = await self.gitlab_service.check_maintainer_access(task_info.repo_url)
            if not has_access:
                print(f"❌ Missing Maintainer access in GitLab: {err}")
                reporter_name = task_info.reporter_username or (f"User #{task_info.reporter_id}" if task_info.reporter_id else "Reporter")
                proposed_comment = f"وقت به خیر @{reporter_name}\nلطفا دسترسی Maintainer به این پروژه را روی GitLab به من بدهید 🙏\n🔗 ریپو: {task_info.repo_url}"
                print("\n" + "=" * 60)
                print("📝 Proposed ClickUp Comment (Missing Access):")
                print("=" * 60)
                print(proposed_comment)
                print("=" * 60)

                confirm = input("Post access request comment and update task status to 'waiting for customer'? [Y/n]: ").strip().lower()
                if confirm in ["", "y", "yes"]:
                    print("📝 Posting access request comment in ClickUp...")
                    ok = await self.clickup_service.post_maintainer_request_comment(
                        task_info.task_id,
                        task_info.reporter_id,
                        task_info.reporter_username,
                        repo_url=task_info.repo_url
                    )
                    if ok:
                        print("✅ Comment posted and task status updated to 'waiting for customer'.")
                        print("🧹 Cleaned up assignees and followers — kept only Me and reporter.")
                    else:
                        print("⚠️ Could not post comment to ClickUp.")
                else:
                    print("⏩ Skipped posting comment and changing status in ClickUp.")
                return
            print("✅ GitLab Maintainer access verified.")
            start_milestone = DeploymentMilestone.REQUIREMENTS_CHECK

        is_production = (environment_type or "").strip().lower() in ["production", "prod"]

        # Production: skip requirements check, create DB subtask instead
        if is_production and start_milestone == DeploymentMilestone.REQUIREMENTS_CHECK:
            print("\n🏭 Production environment detected.")
            print("⏩ Skipping deploy requirements check (applies to Main only).")

            # Ask for DB info and create subtask
            print("\n📦 For Production, database will be created by the infrastructure team.")
            db_name = input("  Database name (or press Enter to skip subtask creation): ").strip()
            if db_name:
                db_user = input("  Database username: ").strip() or db_name
                subtask_title = f"درخواست یوزر و دیتابیس برای سرویس {project_name}"
                subtask_comment = (
                    f"سلام\n"
                    f"وقت به خیر\n"
                    f"لطفا برای این سرویس دیتابیس {db_name} را با یوزر {db_user} ایجاد کنید\n"
                    f"با تشکر"
                )
                db_assignee_input = input("\nEnter ClickUp user name or ID to assign DB creation (or press Enter to leave unassigned): ").strip()
                assignee_ids = []
                if db_assignee_input:
                    if db_assignee_input.isdigit():
                        assignee_ids = [int(db_assignee_input)]
                    else:
                        db_user_obj = await self.clickup_service.find_user_by_name(db_assignee_input)
                        if db_user_obj and db_user_obj.get("id"):
                            assignee_ids = [int(db_user_obj["id"])]
                            print(f"  Assigned to: {db_user_obj.get('username')} ({db_user_obj.get('id')})")
                        else:
                            print(f"⚠️ Could not find user '{db_assignee_input}' in workspace.")

                print("\n" + "=" * 60)
                print(f"📝 Proposed ClickUp Subtask:")
                print(f"  Title   : {subtask_title}")
                print(f"  Assignee: {db_assignee_input or 'Unassigned'}")
                print(f"  Comment :")
                print(subtask_comment)
                print("=" * 60)

                confirm_sub = input("\nCreate this subtask in ClickUp? [Y/n]: ").strip().lower()
                if confirm_sub in ["", "y", "yes"]:
                    subtask_id = await self.clickup_service.create_subtask(
                        parent_task_id=task_info.task_id,
                        name=subtask_title,
                        assignee_ids=assignee_ids,
                        comment_text=subtask_comment,
                    )
                    if subtask_id:
                        print(f"✅ Subtask created successfully (ID: {subtask_id}).")
                    else:
                        print("⚠️ Failed to create subtask in ClickUp.")
                else:
                    print("⏩ Skipped subtask creation.")

            start_milestone = DeploymentMilestone.GITLAB_CI

        # Step 2: Check Deploy Requirements (Main/Dev/Stage only)
        if not is_production and start_milestone == DeploymentMilestone.REQUIREMENTS_CHECK:
            print("⏳ Auditing deploy readiness standards...")
            project = await self.gitlab_service.get_project(task_info.repo_url)
            if project:
                files_map = await self.gitlab_service.get_repository_files_map(project)
                branches = await self.gitlab_service.get_branches_and_protection(project)
                from deploy_automation.engine.checker import DeployRequirementsChecker
                checker = DeployRequirementsChecker(project_name, task_info.repo_url, files_map, branches)
                report = checker.run_all_checks(has_maintainer_access=True)
                print(report.summary_text)

                if not report.all_passed:
                    failed_items = [c for c in report.checks if not c.passed and c.status == CheckStatus.FAILED]

                    body_lines = [
                        "الزامات دیپلوی به طور کامل رعایت نشده است.",
                        "توضیحات به شرح زیر است:\n"
                    ]
                    for c in failed_items:
                        body_lines.append(f"❌ **{c.title}**: {c.details}")
                        if c.remediation:
                            body_lines.append(f"   💡 *راهکار:* {c.remediation}")
                        body_lines.append("")

                    body_lines.append("لطفا موارد فوق را بررسی و برطرف نموده و پس از انجام اطلاع دهید 🙏")
                    comment_body = "\n".join(body_lines).strip()
                    reporter_display = f"@{task_info.reporter_username}" if task_info.reporter_username else (f"User #{task_info.reporter_id}" if task_info.reporter_id else "Reporter")

                    while True:
                        print("\n" + "=" * 60)
                        print("📝 Proposed ClickUp Comment (Deploy Readiness Requirements Failed):")
                        print("=" * 60)
                        print(f"وقت به خیر {reporter_display}\n\n{comment_body}")
                        print("=" * 60)

                        print("\nOptions:")
                        print("  [Enter / 1] Confirm & post comment to ClickUp (status -> 'waiting for customer')")
                        print("  [2] Edit comment text")
                        print("  [3] Ignore requirements and proceed with deployment anyway")
                        print("  [0] Abort / Cancel")

                        req_choice = input("\nYour choice: ").strip()

                        if req_choice in ["", "1"]:
                            confirm_post = input("Post this comment to ClickUp and set status to 'waiting for customer'? [Y/n]: ").strip().lower()
                            if confirm_post in ["", "y", "yes"]:
                                print("📝 Posting comment to ClickUp...")
                                posted = await self.clickup_service.post_tagged_comment(
                                    task_id=task_info.task_id,
                                    reporter_id=task_info.reporter_id,
                                    reporter_username=task_info.reporter_username,
                                    body_text=comment_body
                                )
                                if posted:
                                    print("🔄 Updating task status to 'waiting for customer'...")
                                    await self.clickup_service.update_task_status(
                                        task_info.task_id,
                                        settings.CLICKUP_WAITING_STATUS,
                                        reporter_id=task_info.reporter_id
                                    )
                                    print("✅ Comment posted and status updated to 'waiting for customer'.")
                                    print("🧹 Assignees and watchers cleaned up (only Me & reporter retained).")
                                else:
                                    print("⚠️ Failed to post comment to ClickUp.")
                                return
                            else:
                                print("Action cancelled.")
                                continue

                        elif req_choice == "2":
                            print("\n✏️  Enter new comment body (type 'EOF' or 'END' on a separate line when finished, or Enter to abort edit):")
                            new_lines = []
                            while True:
                                try:
                                    line = input()
                                except EOFError:
                                    break
                                if line.strip() in ["EOF", "END"]:
                                    break
                                new_lines.append(line)

                            joined_new = "\n".join(new_lines).strip()
                            if joined_new:
                                comment_body = joined_new
                                print("✅ Comment updated. Review the updated comment above.")
                            else:
                                print("⚠️ No changes made to comment.")
                            continue

                        elif req_choice == "3":
                            print("⏩ Proceeding with deployment despite failed requirements...")
                            break

                        elif req_choice == "0":
                            print("❌ Deployment aborted.")
                            return
                else:
                    print("\n✅ All deployment readiness standards verified successfully!")

            start_milestone = DeploymentMilestone.GITLAB_CI

        # Update status to 'in progress' once requirements are verified / deployment begins
        current_status = getattr(task_info, "status", "").lower()
        if current_status != "in progress":
            confirm_status = input(f"\nDeploy readiness confirmed. Update ClickUp task status from '{task_info.status}' to 'in progress'? [Y/n]: ").strip().lower()
            if confirm_status in ["", "y", "yes"]:
                print("🔄 Updating ClickUp task status to 'in progress'...")
                await self.clickup_service.update_task_status(task_info.task_id, "in progress")
                task_info.status = "in progress"
                print("✅ Task status updated to 'in progress'.")
            else:
                print("⏩ Skipped updating task status to 'in progress'.")
        else:
            print(f"\nℹ️ ClickUp task status is already '{task_info.status}'.")

        # Step 3, 4, 5: Server setup, DB, Envs, Compose
        print("\n" + "=" * 60)
        print("🖥️ Server Deployment & Configuration")
        print("=" * 60)

        # Ask if user wants to start from beginning or resume
        start_from_beginning = input("\nDo you want to start server deployment from the beginning? [Y/n]: ").strip().lower()

        resume_step = 1
        target_server = None
        ssh_user = None
        project_dir = project_name

        if start_from_beginning in ["n", "no"]:
            print("\nSelect resume mode:")
            print("  [1] Auto-detect current stage on server (Default - Enter)")
            print("  [2] Select starting stage manually")
            mode_choice = input("Your choice [1/2]: ").strip()

            if mode_choice == "2":
                print("\n📋 Deployment Stages:")
                for step_num, step_title in DEPLOY_SERVER_STEPS.items():
                    print(f"  [{step_num}] {step_title}")
                sel = input(f"\nSelect step to resume from [1-{len(DEPLOY_SERVER_STEPS)}] (default: 1): ").strip()
                if sel.isdigit() and 1 <= int(sel) <= len(DEPLOY_SERVER_STEPS):
                    resume_step = int(sel)
                else:
                    resume_step = 1
            else:
                # Auto-detect mode
                target_server, ssh_user = await self.select_server("Select target server for inspection:")
                project_dir = input(f"Project directory name in /srv/ (default: {project_name}): ").strip() or project_name

                print(f"\n⏳ Inspecting server {target_server} and repository state for '{project_name}'...")
                detected_step, step_statuses = await self.inspect_server_deployment_state(
                    target_server=target_server,
                    ssh_user=ssh_user,
                    project_name=project_name,
                    project_dir=project_dir,
                    task_info=task_info
                )

                print(f"\n📋 Deployment Steps Inspection for '{project_name}' on {target_server}:")
                for step_num, step_title in DEPLOY_SERVER_STEPS.items():
                    status_str = step_statuses.get(step_num, "[⬜ Pending]")
                    print(f"  [{step_num}] {step_title:<45} {status_str}")

                print(f"\n🤖 Inferred Next Step: [{detected_step}] {DEPLOY_SERVER_STEPS[detected_step]}")
                step_choice = input(f"Press Enter to resume from step [{detected_step}], or enter a step number [1-{len(DEPLOY_SERVER_STEPS)}]: ").strip()
                if step_choice.isdigit() and 1 <= int(step_choice) <= len(DEPLOY_SERVER_STEPS):
                    resume_step = int(step_choice)
                else:
                    resume_step = detected_step

        # If target server was not selected yet, prompt now
        if not target_server:
            target_server, ssh_user = await self.select_server("Select target server for deployment:")

        # ==========================================
        # Step 1: Server Connection & Runner Check
        # ==========================================
        if resume_step <= 1:
            has_runner, runner_err = await self.ssh_service.check_gitlab_runner_shell(target_server, ssh_user)
            if not has_runner:
                print(f"\n❌ Error: {runner_err}")
                return
            print("✅ GitLab Runner (shell executor) confirmed on target server.")

            internal_ip = await self.ssh_service.get_server_internal_ip(target_server, ssh_user) or "10.10.0.1"

            print(f"\n📂 Existing directories in /srv/ on {target_server}:")
            tree_out = await self.ssh_service.get_srv_tree(target_server, ssh_user)
            print(tree_out)

            project_dir = input(f"\nProject directory name in /srv/ (default: {project_dir}): ").strip() or project_dir
        else:
            # Already past step 1 - just need internal_ip, no runner check
            internal_ip = await self.ssh_service.get_server_internal_ip(target_server, ssh_user) or "10.10.0.1"
            print(f"⏩ Skipping Step 1 (Server Connection & Runner Check). Internal IP: {internal_ip}")

        # ==========================================
        # Step 2: Database Provisioning
        # ==========================================
        db_dsn = None
        has_database = any("database" in sn.lower() for sn in service_needs)
        if is_production:
            print("⏩ Skipping Step 2 (Database Provisioning). Production DB is handled by infrastructure team.")
        elif has_database:
            if resume_step <= 2:
                db_dsn = await self.execute_postgres_flow(project_name, internal_ip)
            else:
                existing_env = await self.ssh_service.read_remote_file(
                    target_server, ssh_user, f"/srv/{project_dir}/.env"
                ) or ""
                m_dsn = re.search(r"DATABASE_URL=(.+)", existing_env)
                if m_dsn:
                    db_dsn = m_dsn.group(1).strip()
                    print(f"💡 Detected existing database DSN from server: {db_dsn}")

        # ==========================================
        # Step 3: Environment Variables Setup
        # ==========================================
        env_content = ""
        if resume_step <= 3:
            existing_env = await self.ssh_service.read_remote_file(
                target_server, ssh_user, f"/srv/{project_dir}/.env"
            ) or ""
            if existing_env:
                print(f"\n📄 Found existing environment file on server (/srv/{project_dir}/.env).")
                use_existing = input("Do you want to reuse the existing .env? [Y/n]: ").strip().lower()
                if use_existing not in ["n", "no"]:
                    env_content = existing_env

            if not env_content:
                print(f"\n📝 Please enter environment variables (.env). Type END on a new line when finished:")
                if db_dsn:
                    print(f"💡 Recommended DATABASE_URL:\nDATABASE_URL={db_dsn}")
                env_lines = []
                while True:
                    try:
                        line = input()
                        if line.strip() == "END":
                            break
                        env_lines.append(line)
                    except EOFError:
                        break
                env_content = "\n".join(env_lines).strip()

            # Edit .env logic
            import subprocess, tempfile, os
            edit_env = input(f"\nDo you want to manually edit the .env before saving? [y/N]: ").strip().lower()
            if edit_env in ["y", "yes"]:
                with tempfile.NamedTemporaryFile(mode='w+', suffix=".env", delete=False) as tf:
                    tf.write(env_content)
                    tf_name = tf.name

                editor = os.environ.get("EDITOR", "nano")
                subprocess.call([editor, tf_name])

                with open(tf_name, "r") as tf:
                    env_content = tf.read().strip()
                os.remove(tf_name)

            initial_tag = input(f"\nInitial Docker image tag (default: main): ").strip() or "main"

            await self.ssh_service.setup_project_directories_and_env(
                host=target_server,
                user=ssh_user,
                project_dir=project_dir,
                project_name=project_name,
                env_content=env_content,
                initial_tag=initial_tag
            )
            print("✅ Environment variables saved on server.")
        else:
            print(f"⏩ Skipping Step 3 (Environment Variables). Reusing /srv/{project_dir}/.env")

        # ==========================================
        # Step 4: Docker Compose Setup
        # ==========================================
        has_public_route = any("public" in sn.lower() for sn in service_needs)
        free_port = None
        container_port = 8080

        if resume_step <= 4:
            # Log shipping
            has_log_shipping = any("log" in sn.lower() for sn in service_needs)
            logging_config = None
            if has_log_shipping:
                print("\n⏳ Detecting log shipping template (fluent-bit / fluentd) in /srv containers...")
                logging_config = await self.ssh_service.scan_log_shipping_template(target_server, ssh_user)

            # Public route port
            if has_public_route:
                free_port = await self.ssh_service.find_available_port(target_server, ssh_user)
                project_obj = await self.gitlab_service.get_project(task_info.repo_url)
                if project_obj:
                    files_map = await self.gitlab_service.get_repository_files_map(project_obj)
                    df_content = files_map.get("Dockerfile", "")
                    container_port = DockerComposeGenerator.extract_container_port_from_dockerfile(df_content, 8080)

                print("\n" + "🌐 " * 15)
                print(f"📢 This deployment requires a public route:")
                print(f"👉 Target IP & Port: {internal_ip}:{free_port} (Container Port: {container_port})")
                print("🌐 " * 15)

            # Check if compose file exists to append or create
            existing_compose_json = await self.ssh_service.get_compose_config_json(target_server, ssh_user, project_dir)
            is_appending_service = False
            target_network = None
            target_ip = None

            if existing_compose_json:
                import json, ipaddress
                try:
                    compose_data = json.loads(existing_compose_json)
                    print(f"\n✅ Found existing docker-compose.yml in {project_dir}. Service will be appended.")
                    networks = compose_data.get("networks", {})
                    services = compose_data.get("services", {})

                    print("\n🌐 Defined Networks:")
                    for net_name in networks:
                        print(f"  - {net_name}")

                    print("\n📦 Existing Services:")
                    for srv_name, srv_data in services.items():
                        srv_nets = srv_data.get("networks", {})
                        if isinstance(srv_nets, dict):
                            for n_name, n_data in srv_nets.items():
                                if isinstance(n_data, dict) and "ipv4_address" in n_data:
                                    print(f"  - {srv_name} -> Network: {n_name} | Static IP: {n_data['ipv4_address']}")
                                else:
                                    print(f"  - {srv_name} -> Network: {n_name}")
                        elif isinstance(srv_nets, list):
                            print(f"  - {srv_name} -> Networks: {', '.join(srv_nets)}")

                    if networks:
                        net_choices = list(networks.keys())
                        print("\nWhich network do you want to attach the new service to?")
                        for i, net_name in enumerate(net_choices, 1):
                            print(f"  [{i}] {net_name}")

                        net_choice = input(f"Select network [1-{len(net_choices)}] (or press Enter to skip static IP): ").strip()
                        if net_choice.isdigit() and 1 <= int(net_choice) <= len(net_choices):
                            target_network = net_choices[int(net_choice) - 1]

                            used_ips = []
                            for srv_name, srv_data in services.items():
                                srv_nets = srv_data.get("networks", {})
                                if isinstance(srv_nets, dict) and target_network in srv_nets:
                                    ip = srv_nets[target_network].get("ipv4_address")
                                    if ip:
                                        try:
                                            used_ips.append(ipaddress.IPv4Address(ip))
                                        except Exception:
                                            pass

                            suggested_ip = ""
                            if used_ips:
                                try:
                                    suggested_ip = str(max(used_ips) + 1)
                                except Exception:
                                    pass

                            prompt_str = f"Enter Static IP for this service"
                            if suggested_ip:
                                prompt_str += f" (suggested: {suggested_ip})"

                            ip_input = input(f"{prompt_str}: ").strip()
                            if not ip_input and suggested_ip:
                                target_ip = suggested_ip
                            elif ip_input:
                                target_ip = ip_input

                    is_appending_service = True
                except Exception as e:
                    print(f"⚠️ Could not parse existing compose config: {e}")

            gitlab_host = urllib.parse.urlparse(settings.GITLAB_URL).netloc or "gitlab.example.com"
            registry_image = f"{gitlab_host}:5050/{self.gitlab_service.extract_project_path(task_info.repo_url)}"
            if is_appending_service:
                service_block = DockerComposeGenerator.generate_service_block(
                    project_name=project_name,
                    registry_image=registry_image,
                    container_port=container_port,
                    bound_ip=internal_ip,
                    external_port=free_port,
                    has_public_route=has_public_route,
                    has_log_shipping=has_log_shipping,
                    logging_config=logging_config,
                    target_network=target_network,
                    target_ip=target_ip
                )
                await self.ssh_service.append_service_to_compose(target_server, ssh_user, project_dir, service_block)
                print("✅ Service successfully appended to existing docker-compose.yml")
            else:
                compose_str = DockerComposeGenerator.generate_compose(
                    project_name=project_name,
                    registry_image=registry_image,
                    container_port=container_port,
                    bound_ip=internal_ip,
                    external_port=free_port,
                    has_public_route=has_public_route,
                    has_log_shipping=has_log_shipping,
                    logging_config=logging_config
                )
                await self.ssh_service.write_remote_compose(target_server, ssh_user, project_dir, compose_str)
                print("✅ File docker-compose.yml created on server.")
        else:
            print(f"⏩ Skipping Step 4 (Docker Compose). Reusing /srv/{project_dir}/docker-compose.yml")
            existing_compose = await self.ssh_service.read_remote_file(
                target_server, ssh_user, f"/srv/{project_dir}/docker-compose.yml"
            ) or ""
            port_match = re.search(r'["\'](?:(\d+\.\d+\.\d+\.\d+):)?(\d+):(\d+)["\']', existing_compose)
            if port_match:
                if port_match.group(1):
                    internal_ip = port_match.group(1)
                free_port = int(port_match.group(2))
                container_port = int(port_match.group(3))
                has_public_route = True
                print(f"💡 Detected existing service port mapping: {internal_ip}:{free_port} -> {container_port}")

        # ==========================================
        # Step 5: GitLab CI/CD Configuration & MR
        # ==========================================
        project_obj = await self.gitlab_service.get_project(task_info.repo_url)
        last_pipeline_id = None

        if resume_step <= 5 and project_obj:
            def_branch = project_obj.default_branch or "main"
            try:
                f = await project_obj.files.get(file_path=".gitlab-ci.yml", ref=def_branch)
                current_ci = f.decode().decode("utf-8")
            except Exception:
                current_ci = ""

            # 1. Detect Runner & Tags on Target Server & GitLab API
            print(f"\n🔍 Inspecting GitLab Runner and tags for {target_server}...")
            detected_tags = []
            runner_info = await self.ssh_service.get_gitlab_runner_info(target_server, ssh_user)
            server_runner_names = runner_info.get("names", [])
            server_runner_ids = runner_info.get("ids", [])

            # Direct ID lookup from config.toml
            for r_id in server_runner_ids:
                try:
                    details = await self.gitlab_service.get_runner_details(r_id)
                    if details and details.get("tag_list"):
                        for t in details["tag_list"]:
                            if t not in detected_tags:
                                detected_tags.append(t)
                except Exception:
                    pass

            # If not found by ID, match from project runners list
            if not detected_tags:
                gl_runners = await self.gitlab_service.get_project_runners(project_obj.id)
                lower_names = [n.lower() for n in server_runner_names]
                for r in gl_runners:
                    r_desc = (r.get("description") or "").lower()
                    r_name = (r.get("name") or "").lower()
                    r_ip = (r.get("ip_address") or "").lower()
                    if (
                        any(s in r_desc or s in r_name for s in lower_names)
                        or (internal_ip and internal_ip == r_ip)
                        or target_server.lower() in r_desc
                        or target_server.lower() in r_name
                    ):
                        details = await self.gitlab_service.get_runner_details(r["id"])
                        if details and details.get("tag_list"):
                            for t in details["tag_list"]:
                                if t not in detected_tags:
                                    detected_tags.append(t)

            # Check config.toml tags
            if runner_info.get("tags"):
                for t in runner_info["tags"]:
                    if t not in detected_tags:
                        detected_tags.append(t)

            # Filter out runner executor types (e.g. 'shell', 'docker') which are not custom runner tags
            EXCLUDED_TAGS = {"shell", "docker", "kubernetes", "ssh", "virtualbox", "parallels", "custom"}
            detected_tags = [t for t in detected_tags if t.strip().lower() not in EXCLUDED_TAGS]

            # Fallback to runner name from server config if no tag was found
            if not detected_tags:
                if server_runner_names:
                    detected_tags = [server_runner_names[0]]
                else:
                    detected_tags = [target_server.split(".")[0]]

            runner_display = f"{', '.join(server_runner_names)}" if server_runner_names else target_server
            if server_runner_ids:
                runner_display += f" (ID: {', '.join(map(str, server_runner_ids))})"

            print(f"💡 Detected Runner: {runner_display}")
            print(f"🏷️  Detected Runner Tags: {', '.join(detected_tags)}")

            tags_input = input(f"Enter runner tag(s) to use in .gitlab-ci.yml (comma-separated, default: {', '.join(detected_tags)}): ").strip()
            if tags_input:
                chosen_tags = [t.strip() for t in tags_input.split(",") if t.strip()]
            else:
                chosen_tags = detected_tags

            updated_ci = GitLabCICDGenerator.merge_into_gitlab_ci(
                current_ci=current_ci,
                project_name=project_name,
                project_dir=project_dir,
                environment_type=environment_type,
                runner_tags=chosen_tags
            )

            print("\n" + "=" * 50)
            print("📄 Generated Deploy Job Configuration Preview:")
            print("=" * 50)
            print(GitLabCICDGenerator.generate_deploy_jobs(
                project_name=project_name,
                project_dir=project_dir,
                environment_type=environment_type,
                runner_tags=chosen_tags
            ))
            print("=" * 50)

            # Record latest pipeline ID on default branch before merging MR
            prev_pipeline = await self.gitlab_service.get_latest_pipeline(project_obj.id, def_branch)
            if prev_pipeline:
                last_pipeline_id = prev_pipeline.get("id")

            # Check if def_branch already has the expected configuration
            need_mr1 = (updated_ci.strip() != current_ci.strip())
            if not need_mr1:
                print(f"⏩ Deploy job configuration is already up to date on '{def_branch}'. Skipping MR [1/2].")
            else:
                # Create branch and commit
                import time
                new_branch = f"deploy/{project_name}-ci-{int(time.time())}"
                print(f"\n🌿 Creating branch '{new_branch}' from '{def_branch}'...")
                ok_br, br_res = await self.gitlab_service.create_branch(project_obj.id, new_branch, def_branch)
                if not ok_br:
                    print(f"❌ Failed to create branch '{new_branch}': {br_res}")
                    return

                try:
                    if current_ci:
                        await self.gitlab_service.update_file(
                            project_id=project_obj.id,
                            file_path=".gitlab-ci.yml",
                            content=updated_ci,
                            branch=new_branch,
                            commit_message=f"ci: add automated deploy job for {project_name}"
                        )
                    else:
                        await self.gitlab_service.create_file(
                            project_id=project_obj.id,
                            data={
                                "file_path": ".gitlab-ci.yml",
                                "branch": new_branch,
                                "content": updated_ci,
                                "commit_message": f"ci: add automated deploy job for {project_name}"
                            }
                        )
                    print(f"✅ Committed deploy job configuration to branch '{new_branch}'.")
                except Exception as e:
                    print(f"❌ Failed to commit to branch '{new_branch}': {e}")
                    return

                # 1. Create Merge Request into default branch (e.g. dev)
                ok_mr, mr_data = await self.gitlab_service.create_merge_request(
                    project_id=project_obj.id,
                    source_branch=new_branch,
                    target_branch=def_branch,
                    title=f"Configure automated deploy job for {project_name}"
                )
                if not ok_mr:
                    print(f"❌ Failed to create Merge Request: {mr_data}")
                    return

                mr_url = mr_data.get("web_url", "N/A")
                mr_iid = mr_data.get("iid")

                print("\n" + "🔗 " * 15)
                print(f"🚀 Merge Request [1/2] ({new_branch} -> {def_branch}) created successfully!")
                print(f"👉 MR URL: {mr_url}")
                print("🔗 " * 15)

                # Wait for manual merge of MR 1
                while True:
                    m_resp = input(f"\nPress Enter once you have merged MR [1/2] into '{def_branch}' (or type 'cancel' to abort): ").strip().lower()
                    if m_resp in ["cancel", "c"]:
                        print("❌ Deployment aborted.")
                        return
                    mr_state = await self.gitlab_service.check_merge_request_status(project_obj.id, mr_iid)
                    if mr_state == "merged":
                        print(f"✅ Merge Request [1/2] confirmed MERGED into '{def_branch}'!")
                        break
                    else:
                        print(f"⚠️ MR status is currently '{mr_state}'. Please merge the MR on GitLab to continue.")

            # 2. Check if main branch exists and is different from def_branch (e.g. dev -> main)
            branches_list = await self.gitlab_service.get_branches_and_protection(project_obj)
            branch_names = [b["name"] for b in branches_list]
            main_branch = "main" if "main" in branch_names else ("master" if "master" in branch_names else def_branch)

            if def_branch != main_branch:
                # Check if main_branch already has the deploy job configured
                main_ci = ""
                try:
                    f_main = await project_obj.files.get(file_path=".gitlab-ci.yml", ref=main_branch)
                    main_ci = f_main.decode().decode("utf-8")
                except Exception:
                    main_ci = ""

                expected_deploy_job = GitLabCICDGenerator.generate_deploy_jobs(
                    project_name=project_name,
                    project_dir=project_dir,
                    environment_type=environment_type,
                    runner_tags=chosen_tags
                ).strip()

                need_mr2 = True
                if expected_deploy_job in main_ci or GitLabCICDGenerator.merge_into_gitlab_ci(main_ci, project_name, project_dir, environment_type, chosen_tags).strip() == main_ci.strip():
                    need_mr2 = False
                    print(f"⏩ Deploy job is already present and up to date on '{main_branch}'. Skipping MR [2/2].")

                if need_mr2:
                    print(f"\n🌿 Creating second Merge Request ({def_branch} -> {main_branch})...")
                    ok_mr2, mr2_data = await self.gitlab_service.get_or_create_merge_request(
                        project_id=project_obj.id,
                        source_branch=def_branch,
                        target_branch=main_branch,
                        title=f"Release {project_name} deploy configuration from {def_branch} to {main_branch}"
                    )
                    if not ok_mr2:
                        print(f"❌ Failed to create Merge Request to {main_branch}: {mr2_data}")
                        return

                    mr2_url = mr2_data.get("web_url", "N/A")
                    mr2_iid = mr2_data.get("iid")

                    print("\n" + "🔗 " * 15)
                    print(f"🚀 Merge Request [2/2] ({def_branch} -> {main_branch}) is ready!")
                    print(f"👉 MR URL: {mr2_url}")
                    print("🔗 " * 15)

                    # Record latest pipeline ID on main_branch before merge
                    prev_main_pipeline = await self.gitlab_service.get_latest_pipeline(project_obj.id, main_branch)
                    if prev_main_pipeline:
                        last_pipeline_id = prev_main_pipeline.get("id")

                    while True:
                        m_resp = input(f"\nPress Enter once you have merged MR [2/2] into '{main_branch}' (or type 'cancel' to abort): ").strip().lower()
                        if m_resp in ["cancel", "c"]:
                            print("❌ Deployment aborted.")
                            return
                        mr2_state = await self.gitlab_service.check_merge_request_status(project_obj.id, mr2_iid)
                        if mr2_state == "merged":
                            print(f"✅ Merge Request [2/2] confirmed MERGED into '{main_branch}'!")
                            break
                        else:
                            print(f"⚠️ MR status is currently '{mr2_state}'. Please merge the MR on GitLab to continue.")
        elif resume_step > 5:
            print(f"⏩ Skipping Step 5 (GitLab CI/CD Configuration).")

        # ==========================================
        # Step 6: Pipeline Monitoring
        # ==========================================
        if resume_step <= 6 and project_obj:
            branches_list = await self.gitlab_service.get_branches_and_protection(project_obj)
            branch_names = [b["name"] for b in branches_list]
            target_deploy_branch = "main" if "main" in branch_names else ("master" if "master" in branch_names else (project_obj.default_branch or "main"))

            print(f"\n⏳ Monitoring pipeline execution for branch '{target_deploy_branch}'...")

            pipeline = None
            if last_pipeline_id:
                print(f"Waiting for new pipeline to trigger on '{target_deploy_branch}' (after pipeline #{last_pipeline_id})...")
                for _ in range(12):
                    p = await self.gitlab_service.get_latest_pipeline(project_obj.id, target_deploy_branch)
                    if p and p.get("id") != last_pipeline_id:
                        pipeline = p
                        break
                    await asyncio.sleep(5)

            if not pipeline:
                pipeline = await self.gitlab_service.get_latest_pipeline(project_obj.id, target_deploy_branch)

            if not pipeline:
                print("⚠️ No pipeline found. Proceeding...")
            else:
                p_id = pipeline.get("id")
                print(f"🚀 Tracking Pipeline #{p_id} ({pipeline.get('web_url', '')})...")
                while True:
                    p_info = await self.gitlab_service.get_pipeline(project_obj.id, p_id)
                    if not p_info:
                        break
                    status = p_info.get("status")

                    jobs = await self.gitlab_service.get_pipeline_jobs(project_obj.id, p_id)
                    jobs_summary = ", ".join(f"{j.get('name')}: {j.get('status')}" for j in jobs) if jobs else "initializing"

                    if status == "success":
                        print(f"✅ Pipeline #{p_id} succeeded! Jobs: [{jobs_summary}]")
                        break
                    elif status in ["failed", "canceled"]:
                        print(f"❌ Pipeline #{p_id} failed!")
                        failed_jobs = [j for j in jobs if j.get("status") == "failed"]
                        if failed_jobs:
                            fj = failed_jobs[0]
                            print(f"\n⚠️ Job '{fj.get('name')}' failed. Fetching logs...")
                            trace = await self.gitlab_service.get_job_trace(project_obj.id, fj.get("id"))
                            print("=" * 40)
                            print("\n".join(str(trace).split("\n")[-30:]))
                            print("=" * 40)

                            print("\nHow would you like to resolve this?")
                            print("  [1] Retry the job")
                            print("  [2] Automated AI Fix (Analyze, apply server/CI changes, MR & retry)")
                            print("  [3] Manual fix (Pause & wait for new merge)")
                            print("  [0] Abort")
                            c = input("Your choice: ").strip()

                            if c == "1":
                                print("🔄 Retrying job...")
                                await self.gitlab_service.retry_job(project_obj.id, fj.get("id"))
                                await asyncio.sleep(5)
                                continue
                            elif c == "2":
                                fix_ok, new_pid = await self.execute_ai_pipeline_fix(
                                    project_obj=project_obj,
                                    task_info=task_info,
                                    target_server=target_server,
                                    ssh_user=ssh_user,
                                    project_name=project_name,
                                    project_dir=project_dir,
                                    failed_job=fj,
                                    error_trace=str(trace),
                                    target_deploy_branch=target_deploy_branch,
                                    current_pipeline_id=p_id
                                )
                                if fix_ok and new_pid:
                                    p_id = new_pid
                                    continue
                                else:
                                    print("⚠️ AI fix was cancelled or not applied. You can retry, fix manually, or re-run AI.")
                                    continue
                            elif c == "3":
                                print("\n⏸️ Script paused. Please fix the issue manually, commit, and merge to main.")
                                input("Press Enter when the new merge is complete to monitor the new pipeline... ")

                                old_pipeline_id = p_id
                                print(f"\n🔍 Searching for newly triggered pipeline on '{target_deploy_branch}' (newer than #{old_pipeline_id})...")
                                new_pipeline = None
                                for _ in range(18):
                                    latest = await self.gitlab_service.get_latest_pipeline(project_obj.id, target_deploy_branch)
                                    if latest and latest.get("id") != old_pipeline_id:
                                        new_pipeline = latest
                                        break
                                    await asyncio.sleep(4)

                                if new_pipeline:
                                    p_id = new_pipeline.get("id")
                                    print(f"🚀 Found new Pipeline #{p_id} ({new_pipeline.get('web_url', '')})!")
                                else:
                                    latest = await self.gitlab_service.get_latest_pipeline(project_obj.id, target_deploy_branch)
                                    if latest and latest.get("id") != old_pipeline_id:
                                        p_id = latest.get("id")
                                        print(f"🚀 Found new Pipeline #{p_id} ({latest.get('web_url', '')})!")
                                    else:
                                        manual_pid = input(f"⚠️ Could not automatically detect a new pipeline on '{target_deploy_branch}'. Enter new Pipeline ID manually (or press Enter to recheck #{old_pipeline_id}): ").strip()
                                        if manual_pid.isdigit():
                                            p_id = int(manual_pid)
                                continue
                            else:
                                print("❌ Deployment aborted.")
                                return
                        else:
                            print("⚠️ Pipeline failed but no failed jobs found. Exiting monitoring.")
                            break
                    else:
                        print(f"⏳ Pipeline #{p_id} is {status}... [{jobs_summary}] waiting 10s")
                        await asyncio.sleep(10)

        # ==========================================
        # Step 7: Nginx & Reverse Proxy Setup
        # ==========================================
        public_url = None
        if resume_step <= 7:
            if has_public_route:
                while True:
                    if not free_port:
                        print("\n🌐 This service has 'public route' requested, but port was not detected automatically.")
                        port_input = input(f"Enter host target port mapped to container (or press Enter if unsure/skip): ").strip()
                        if port_input.isdigit():
                            free_port = int(port_input)

                    if free_port:
                        want_nginx = input(f"\nDo you want to configure Nginx & SSL for this service ({internal_ip}:{free_port})? [Y/n]: ").strip().lower()
                        if want_nginx in ["", "y", "yes"]:
                            nginx_res = await self.execute_nginx_flow(internal_ip, free_port)
                            if nginx_res and nginx_res[0]:
                                d, p = nginx_res
                                public_url = f"https://{d}{p if p and p != '/' else ''}"
                                break
                            else:
                                print("❌ Nginx setup failed or was aborted.")
                        else:
                            print("⚠️ Skipping Nginx configuration by user choice.")
                    else:
                        print("⚠️ Skipping Nginx configuration because no target port was provided.")

                    if not public_url:
                        retry = input("\n⚠️ Nginx configuration is REQUIRED for this service (public route). Retry, Ignore, or Abort? [R/i/a]: ").strip().lower()
                        if retry in ["a", "abort"]:
                            print("❌ Deployment aborted by user.")
                            return
                        elif retry in ["i", "ignore"]:
                            print("⚠️ Ignoring missing Nginx configuration. Proceeding to finalize...")
                            break
                        else:
                            free_port = None
                            continue
            else:
                want_nginx = input("\nDo you want to configure Nginx & SSL for this service? [y/N]: ").strip().lower()
                if want_nginx in ["y", "yes"]:
                    if not free_port:
                        port_input = input("Enter target port on server: ").strip()
                        if port_input.isdigit():
                            free_port = int(port_input)
                    if free_port:
                        nginx_res = await self.execute_nginx_flow(internal_ip, free_port)
                        if nginx_res:
                            d, p = nginx_res
                            if d:
                                public_url = f"https://{d}{p if p and p != '/' else ''}"

        # ==========================================
        # Finalization: ClickUp Comment & Status Update
        # ==========================================
        print("\n🎉 Deployment tasks completed successfully.")

        final_msg = "دیپلوی انجام شد و "
        if public_url:
            final_msg += f"از آدرس زیر قابل دسترسی است:\n{public_url}\n"
        elif has_public_route and free_port:
            final_msg += f"از آدرس زیر قابل دسترسی است:\nhttp://{internal_ip}:{free_port}\n"
        else:
            final_msg += "به صورت داخلی در سرور قابل دسترسی است.\n"
        final_msg += "لطفا تست بفرمایید و نتیجه را اعلام کنید"

        print(f"\n📝 Proposed final comment for ClickUp:\n-------------------------------------------------\n{final_msg}\n-------------------------------------------------")
        confirm_post = input("Post this comment and update task status? [Y/n]: ").strip().lower()
        if confirm_post in ["", "y", "yes"]:
            print(f"📝 Posting final comment to ClickUp...")
            await self.clickup_service.post_comment(task_info.task_id, final_msg)
            print("🔄 Updating task status to 'ready to test'...")
            await self.clickup_service.update_task_status(task_info.task_id, "ready to test")
        else:
            print("⚠️ Skipped posting comment and updating task status.")

        print("\n" + "🎉 " * 20)
        print(f"✅ Initial deployment setup for project '{project_name}' completed successfully!")
        print("🎉 " * 20)

    async def run_task_queue_flow(self):
        """
        Fetches tasks from ClickUp in statuses (new, in progress, waiting for customer),
        ordered from oldest to newest, detects state and resumes flow.
        """
        await init_db()
        print("\n⏳ Fetching active deployment tasks from ClickUp (ordered newest to oldest)...")
        tasks = await self.clickup_service.get_active_deploy_tasks()

        if not tasks:
            print("📭 No active tasks found with statuses: new, in progress, or waiting for customer.")
            return

        print(f"\n📋 Found {len(tasks)} active deployment tasks:")
        for idx, t in enumerate(tasks, 1):
            assignees_str = ", ".join(t.assignees) if t.assignees else "None"
            print(f"  [{idx}] {t.task_name} | Assignees: {assignees_str} | Repo: {t.repo_url or 'N/A'} | Status: {t.status}")

        while True:
            task_choice = input(f"\nSelect a task number to process [1-{len(tasks)}] (or 0 to exit): ").strip()
            if not task_choice:
                continue
            if not task_choice.isdigit():
                print("❌ Invalid input.")
                continue
            
            c_val = int(task_choice)
            if c_val == 0:
                break
                
            if c_val < 1 or c_val > len(tasks):
                print("❌ Task number out of range.")
                continue

            task = tasks[c_val - 1]
            idx = c_val

            print("\n" + "=" * 60)
            print(f"📌 Processing Task [{idx}/{len(tasks)}]: {task.task_name}")
            print(f"🔗 Repository: {task.repo_url}")
            print(f"🏷️ ClickUp Status: {task.status}")

            if not task.repo_url:
                print("⚠️ Missing repository URL; skipping task.")
                continue

            detected_milestone, reason = await self.detector.detect_milestone(task)
            print(f"\n🤖 Inferred Milestone: [{MILESTONE_LABELS[detected_milestone]}]")
            print(f"💡 Reason: {reason}")

            print("\nOptions:")
            print(f"  [Enter / 1] Accept and resume from: {MILESTONE_LABELS[detected_milestone]}")
            for m_idx, m in enumerate(MILESTONE_ORDER, 2):
                print(f"  [{m_idx}] Change starting milestone to: {MILESTONE_LABELS[m]}")
            print(f"  [0] Cancel this task")

            choice = input("\nYour choice: ").strip()
            if choice == "0":
                continue

            chosen_milestone = detected_milestone
            if choice.isdigit():
                m_val = int(choice)
                if 2 <= m_val <= len(MILESTONE_ORDER) + 1:
                    chosen_milestone = MILESTONE_ORDER[m_val - 2]

            await self.process_task_flow(task, chosen_milestone)


async def main():
    deployer = ComprehensiveLaptopDeployer()
    await init_db()

    print("\n" + "=" * 60)
    print("🚀 Deploy Automation Platform CLI")
    print("=" * 60)
    print("1. 📋 Process ClickUp Task Queue (Oldest to newest with auto-detected milestone)")
    print("2. 🚀 Deploy Pending Tasks (Waiting for Laptop Handoff)")
    print("3. 🐘 Standalone PostgreSQL Database Setup")
    print("4. 🌐 Standalone Nginx / SSL Setup")
    print("5. 🔧 Change Environmental Variables")
    print("6. 🚪 Exit")

    choice = input("\nPlease select an option: ").strip()

    if choice == "1":
        await deployer.run_task_queue_flow()
    elif choice == "2":
        pending = await get_pending_handoff_sessions()
        if pending:
            s = pending[0]
            task_info = await deployer.clickup_service.get_task(s.task_id)
            if task_info:
                await deployer.process_task_flow(task_info, DeploymentMilestone.DOCKER_COMPOSE)
        else:
            print("No pending handoff tasks found.")
    elif choice == "3":
        srv, usr = await deployer.select_server("Which server needs database access IP logged?")
        ip = await deployer.ssh_service.get_server_internal_ip(srv, usr) or "10.10.0.1"
        p_name = input("Project name: ").strip() or "app"
        await deployer.execute_postgres_flow(p_name, ip)
    elif choice == "4":
        ip = input("Application server internal IP (10.10.x / 10.100.x): ").strip() or "10.10.0.1"
        port = int(input("Application container port: ").strip() or "8080")
        await deployer.execute_nginx_flow(ip, port)
    elif choice == "5":
        await deployer.run_change_env_flow()



if __name__ == "__main__":
    asyncio.run(main())
