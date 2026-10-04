# deploy_automation/engine — Business Logic Engines

> [!IMPORTANT]
> **Live Graph Rule**: If you read or edit any file in `engine/`, update this `GEMINI.md` in the same commit if: a check was added/removed from `checker.py`, a new milestone was added to `DeploymentMilestone`, a new generator method was added, or decision logic changed.

## Purpose

Pure business logic layer. No HTTP calls, no database writes (except `task_state_detector.py` which reads the DB). Stateless functions and classes that can be unit-tested independently.

## Files

### `checker.py` — `DeployRequirementsChecker`

Evaluates **9 organizational Git Flow deployment standards** against GitLab repo files.

**Constructor:**
```python
DeployRequirementsChecker(
    project_name: str,
    repo_url: str,
    files_map: dict[str, str],   # {file_path: file_content_str}
    branches: list[dict]          # branch info dicts from GitLab API
)
```

**Main method:**
```python
report: ProjectReport = checker.run_all_checks(has_maintainer_access=True)
```

**9 checks (in order):**
| # | Method | Standard |
|---|---|---|
| 1 | `check_1_architecture_doc()` | Architecture/design doc exists |
| 2 | `check_2_git_flow()` | Git Flow branches (develop, main, release) present |
| 3 | `check_3_scope_and_responsibilities_doc()` | Scope/responsibilities doc |
| 4 | `check_4_metrics_endpoint()` | `/metrics` or Prometheus endpoint |
| 5 | `check_5_health_check()` | Health check endpoint (`/health`, `/healthz`) |
| 6 | `check_6_sonarqube_ci()` | SonarQube in CI pipeline |
| 7 | `check_7_test_coverage()` | Test coverage configuration |
| 8 | `check_8_frontend_runtime_config()` | Frontend runtime config (not baked-in) |
| 9 | `check_9_dockerfile_and_cicd()` | Dockerfile + CI/CD pipeline exists |

**Output:** `ProjectReport` with `checks: list[CheckItemResult]`, `all_passed: bool`, `summary_text: str`, `comment_text: str`.

---

### `decision_engine.py` — `SREDecisionEngine`

Deterministic rule-based engine for assigning new SRE tasks to team members.

**Constructor:**
```python
SREDecisionEngine(rules_config_path: Optional[str] = None)
# Loads rules from JSON file or uses built-in defaults
```

**Key methods:**
```python
assign_task(task: dict, members: list[dict], member_workloads: dict[int, list]) -> tuple[dict, str]
# Returns (assigned_member, reason_text_in_Persian)

is_change_env_task(task: dict) -> bool
# Returns True if task is specifically an env-var change task
```

**Domain skill rules** (default, can be overridden via JSON):
- `redis`, `database`, `k8s_infra`, `devops_deploy`, `network_os`
- Keywords matched against task title/tags/list name (case-insensitive)

**Modes** (via `settings.DECISION_MODE`):
- `rule_engine` — pure deterministic (default, fastest)
- `ai` — delegates to `AIServiceProvider`
- `hybrid` — rules first, AI as fallback

---

### `task_state_detector.py` — `TaskStateDetector`

Infers the current **deployment milestone** of a ClickUp task by checking DB state, GitLab repo, and task comments.

**Enum `DeploymentMilestone`:**
```python
ACCESS_CHECK       # 1. Maintainer Access
REQUIREMENTS_CHECK # 2. Deploy Requirements Passed
GITLAB_CI          # 3. .gitlab-ci.yml written
DOCKER_COMPOSE     # 4. docker-compose.yml created
ENV_DB_LOG         # 5. Envs, DB, Log Shipping
DEPLOY_TESTED      # 6. Deploy tested & done
```

**Main method:**
```python
milestone, reason = await detector.detect_milestone(task: ClickUpTaskInfo)
```

Priority order: DB record → GitLab file check → task comment analysis.

---

### `cicd_generator.py` — `GitLabCICDGenerator`

Generates `.gitlab-ci.yml` deploy job snippets.

```python
yaml_str = GitLabCICDGenerator.generate_deploy_jobs(
    project_name, project_dir,
    environment_type="Main",   # or "Production" or "Main,Production"
    runner_tags=["shell"]
)
```

Output: YAML with `deploy-main` and/or `deploy-production` jobs using `docker compose pull && up`.

---

### `compose_generator.py` — `DockerComposeGenerator`

Generates `docker-compose.yml` service blocks.

```python
yaml_str = DockerComposeGenerator.generate_service_block(
    project_name, image_url, port_mapping, env_vars, ...
)
```

---

### `nginx_parser.py` — `NginxConfigParser`

Parses and generates Nginx server block configurations.

```python
config_str = NginxConfigParser.generate_server_block(
    domain, upstream_port, ssl=True, path_routing="/api/v1"
)
existing_config = NginxConfigParser.parse_existing(raw_config_text)
```

## Dependencies

| Module | Imports |
|---|---|
| `checker.py` | `deploy_automation.models` (CheckItemResult, CheckStatus, ProjectReport) |
| `decision_engine.py` | `deploy_automation.config.settings` |
| `task_state_detector.py` | `deploy_automation.models.ClickUpTaskInfo`, `deploy_automation.integrations.gitlab_service.GitLabService`, `deploy_automation.integrations.clickup_service.ClickUpService`, `deploy_automation.database` (lazy import) |
| `cicd_generator.py` | stdlib only (`re`) |
| `compose_generator.py` | stdlib only |
| `nginx_parser.py` | stdlib only (`re`) |

## Agent Notes

- All engine classes are **pure or near-pure** — good candidates for unit tests.
- `DeployRequirementsChecker` receives pre-fetched file contents — it does NOT make network calls.
- `TaskStateDetector` is the only engine file that touches the DB (lazy import to avoid circular deps).
- `SREDecisionEngine` reason text is in Persian — do not translate it.
