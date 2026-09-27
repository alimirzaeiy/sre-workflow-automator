import re
from typing import Optional, Any
from deploy_automation.models import CheckItemResult, CheckStatus, ProjectReport


class DeployRequirementsChecker:
    """
    Evaluates 9 core organizational deployment readiness standards against repository files,
    branches, and CI/CD configurations.
    """

    def __init__(self, project_name: str, repo_url: str, files_map: dict[str, str], branches: list[dict[str, Any]]):
        self.project_name = project_name
        self.repo_url = repo_url
        self.files_map = files_map  # dict: {file_path: file_content}
        self.branches = branches    # list of branch info dicts

    def run_all_checks(self, has_maintainer_access: bool) -> ProjectReport:
        if not has_maintainer_access:
            return ProjectReport(
                project_name=self.project_name,
                repo_url=self.repo_url,
                has_maintainer_access=False,
                all_passed=False,
                checks=[],
                summary_text="Maintainer access in GitLab is missing for this repository.",
                comment_text="Please grant Maintainer access to this repository."
            )

        checks: list[CheckItemResult] = [
            self.check_1_architecture_doc(),
            self.check_2_git_flow(),
            self.check_3_scope_and_responsibilities_doc(),
            self.check_4_metrics_endpoint(),
            self.check_5_health_check(),
            self.check_6_sonarqube_ci(),
            self.check_7_test_coverage(),
            self.check_8_frontend_runtime_config(),
            self.check_9_dockerfile_and_cicd()
        ]

        # Determine overall pass
        critical_failed = [c for c in checks if c.status == CheckStatus.FAILED]
        all_passed = len(critical_failed) == 0

        # Build summary text
        summary_lines = []
        for c in checks:
            status_icon = "✅" if c.passed else ("⚠️" if c.status == CheckStatus.WARNING else ("⏭️" if c.status == CheckStatus.SKIPPED else "❌"))
            summary_lines.append(f"{status_icon} **{c.title}**: {c.details}")
            if not c.passed and c.remediation and c.status == CheckStatus.FAILED:
                summary_lines.append(f"   💡 *Remediation:* {c.remediation}")

        summary_text = "\n".join(summary_lines)

        # Build comment text
        comment_items = []
        for c in checks:
            if c.status == CheckStatus.FAILED:
                rem = f" (Remediation: {c.remediation})" if c.remediation else ""
                comment_items.append(f"- {c.title}: {c.details}{rem}")
            elif c.status == CheckStatus.WARNING:
                comment_items.append(f"- [Warning] {c.title}: {c.details}")

        if not comment_items:
            comment_text = "All deployment readiness standards have been verified and passed."
        else:
            comment_text = "\n".join(comment_items)

        return ProjectReport(
            project_name=self.project_name,
            repo_url=self.repo_url,
            has_maintainer_access=True,
            all_passed=all_passed,
            checks=checks,
            summary_text=summary_text,
            comment_text=comment_text
        )

    # 1. Macro-Architecture Document in doc/
    def check_1_architecture_doc(self) -> CheckItemResult:
        doc_files = [p for p in self.files_map.keys() if p.lower().startswith("doc/") or p.lower().startswith("docs/")]
        arch_files = [
            p for p in doc_files 
            if any(k in p.lower() for k in ["arch", "معماری", "architecture", "hld", "system_design", "system-design", "c4"])
        ]
        
        if arch_files:
            return CheckItemResult(
                rule_id=1,
                title="Macro-Architecture Document in doc/",
                status=CheckStatus.PASSED,
                passed=True,
                details=f"Architecture document found at `{arch_files[0]}`."
            )
        elif doc_files:
            return CheckItemResult(
                rule_id=1,
                title="Macro-Architecture Document in doc/",
                status=CheckStatus.FAILED,
                passed=False,
                details="Folder doc/ exists, but macro-architecture document (e.g. architecture.md or HLD) was not found.",
                remediation="Please create and add the macro-architecture document at `doc/architecture.md`."
            )
        else:
            return CheckItemResult(
                rule_id=1,
                title="Macro-Architecture Document in doc/",
                status=CheckStatus.FAILED,
                passed=False,
                details="`doc/` folder and macro-architecture document were not found in repository.",
                remediation="Create `doc/` directory and add `architecture.md` to it."
            )

    # 2. Git Flow Branching Strategy
    def check_2_git_flow(self) -> CheckItemResult:
        branch_names = [b.get("name", "") for b in self.branches]
        has_main_or_master = any(b in branch_names for b in ["main", "master"])
        has_develop = any(b in branch_names for b in ["develop", "dev"])

        if has_main_or_master and has_develop:
            return CheckItemResult(
                rule_id=2,
                title="Git Flow Branching Structure",
                status=CheckStatus.PASSED,
                passed=True,
                details="Standard Git Flow branches (main/master and develop) exist."
            )
        elif has_main_or_master and not has_develop:
            return CheckItemResult(
                rule_id=2,
                title="Git Flow Branching Structure",
                status=CheckStatus.FAILED,
                passed=False,
                details="`develop` branch not found according to Git Flow standard (only main branch exists).",
                remediation="Branch `develop` from the main branch and direct the development flow to it."
            )
        else:
            return CheckItemResult(
                rule_id=2,
                title="Git Flow Branching Structure",
                status=CheckStatus.FAILED,
                passed=False,
                details="Standard branch structure (main and develop) was not found.",
                remediation="Create standard `main` and `develop` branches."
            )

    # 3. Scope & Responsibilities Document in doc/
    def check_3_scope_and_responsibilities_doc(self) -> CheckItemResult:
        doc_files = [p for p in self.files_map.keys() if p.lower().startswith("doc/") or p.lower().startswith("docs/")]
        scope_files = [
            p for p in doc_files 
            if any(k in p.lower() for k in ["scope", "وظایف", "محدوده", "responsibilit", "srs", "requirements", "charter", "spec"])
        ]

        if scope_files:
            return CheckItemResult(
                rule_id=3,
                title="Scope & Responsibilities Document in doc/",
                status=CheckStatus.PASSED,
                passed=True,
                details=f"Scope/Responsibilities document found at `{scope_files[0]}`."
            )
        else:
            return CheckItemResult(
                rule_id=3,
                title="Scope & Responsibilities Document in doc/",
                status=CheckStatus.FAILED,
                passed=False,
                details="Service scope and responsibilities doc (e.g. doc/scope.md or doc/responsibilities.md) was not found.",
                remediation="Add service scope and responsibilities document at `doc/scope.md`."
            )

    # 4. Standard /metrics Endpoint
    def check_4_metrics_endpoint(self) -> CheckItemResult:
        # Search for metrics in files and configs
        has_metrics = False
        detected_in = []
        metrics_keywords = ["/metrics", "prometheus", "actuator/prometheus", "prom-client", "micrometer", "otel", "metrics_app"]

        for path, content in self.files_map.items():
            if not content:
                continue
            for kw in metrics_keywords:
                if kw in content.lower():
                    has_metrics = True
                    detected_in.append(path)
                    break
            if has_metrics and len(detected_in) >= 2:
                break

        if has_metrics:
            return CheckItemResult(
                rule_id=4,
                title="Standard /metrics Endpoint",
                status=CheckStatus.PASSED,
                passed=True,
                details=f"Metrics endpoint/configuration detected (in files: {', '.join(detected_in[:2])})."
            )
        else:
            return CheckItemResult(
                rule_id=4,
                title="Standard /metrics Endpoint",
                status=CheckStatus.FAILED,
                passed=False,
                details="`/metrics` endpoint or Prometheus metrics module was not found in project code.",
                remediation="Add `/metrics` endpoint to expose Prometheus metrics."
            )

    # 5. Health Check Endpoint & CLI
    def check_5_health_check(self) -> CheckItemResult:
        health_found = False
        docker_health = False
        details = []

        # Check Dockerfile HEALTHCHECK
        for path, content in self.files_map.items():
            if "dockerfile" in path.lower() and content:
                if "healthcheck" in content.lower():
                    docker_health = True
                    details.append("HEALTHCHECK directive in Dockerfile")

        # Check health endpoints in code
        health_keywords = ["/health", "/healthz", "/live", "/ready", "actuator/health", "health_check"]
        for path, content in self.files_map.items():
            if not content:
                continue
            for kw in health_keywords:
                if kw in content.lower():
                    health_found = True
                    details.append(f"Health endpoint in {path}")
                    break
            if health_found:
                break

        if health_found or docker_health:
            return CheckItemResult(
                rule_id=5,
                title="Health Check Endpoint & Mechanism",
                status=CheckStatus.PASSED,
                passed=True,
                details=f"Health check mechanism detected ({', '.join(details)})."
            )
        else:
            return CheckItemResult(
                rule_id=5,
                title="Health Check Endpoint & Mechanism",
                status=CheckStatus.FAILED,
                passed=False,
                details="Standard Health Check endpoint (e.g. /healthz) or container HEALTHCHECK directive not found.",
                remediation="Add `/health` or `/healthz` endpoint and Docker HEALTHCHECK instruction to service."
            )

    # 6. SonarQube CI/CD Integration
    def check_6_sonarqube_ci(self) -> CheckItemResult:
        ci_content = ""
        for path, content in self.files_map.items():
            if path in [".gitlab-ci.yml", ".gitlab-ci.yaml"]:
                ci_content = content
                break

        if not ci_content:
            return CheckItemResult(
                rule_id=6,
                title="SonarQube Integration in CI/CD",
                status=CheckStatus.FAILED,
                passed=False,
                details="Configuration file `.gitlab-ci.yml` was not found.",
                remediation="Create `.gitlab-ci.yml` according to standard template and add the SonarQube stage."
            )

        sonar_keywords = ["sonar", "sonarqube", "sonar-scanner", "sonar_host_url", "sonar-project.properties"]
        has_sonar = any(k in ci_content.lower() for k in sonar_keywords) or any("sonar-project.properties" in p for p in self.files_map.keys())

        if has_sonar:
            return CheckItemResult(
                rule_id=6,
                title="SonarQube Integration in CI/CD",
                status=CheckStatus.PASSED,
                passed=True,
                details="SonarQube analysis job detected in CI/CD pipeline."
            )
        else:
            return CheckItemResult(
                rule_id=6,
                title="SonarQube Integration in CI/CD",
                status=CheckStatus.FAILED,
                passed=False,
                details="SonarQube analysis is not defined in `.gitlab-ci.yml`.",
                remediation="Add `sonarqube` stage/job to your pipeline using organizational standard template."
            )

    # 7. Test Coverage Mechanism
    def check_7_test_coverage(self) -> CheckItemResult:
        ci_content = ""
        for path, content in self.files_map.items():
            if path in [".gitlab-ci.yml", ".gitlab-ci.yaml"]:
                ci_content = content
                break

        coverage_keywords = ["coverage:", "coverage", "cobertura", "jacoco", "pytest-cov", "lcov", "cbertura.xml", "nyc"]
        has_coverage = False

        if ci_content and any(k in ci_content.lower() for k in coverage_keywords):
            has_coverage = True

        # Also check package.json or pom.xml or pyproject.toml
        for path, content in self.files_map.items():
            if path in ["package.json", "pyproject.toml", "pom.xml", "build.gradle"] and content:
                if any(k in content.lower() for k in ["coverage", "jest", "pytest-cov", "jacoco"]):
                    has_coverage = True
                    break

        if has_coverage:
            return CheckItemResult(
                rule_id=7,
                title="Active Test Coverage Mechanism",
                status=CheckStatus.PASSED,
                passed=True,
                details="Test coverage generation and reporting detected in CI/CD / test scripts."
            )
        else:
            return CheckItemResult(
                rule_id=7,
                title="Active Test Coverage Mechanism",
                status=CheckStatus.FAILED,
                passed=False,
                details="Test coverage reporting is not configured in CI/CD or config files.",
                remediation="Add coverage flags to test commands and configure report in `.gitlab-ci.yml`."
            )

    # 8. Frontend Runtime Configuration
    def check_8_frontend_runtime_config(self) -> CheckItemResult:
        # Detect if frontend
        is_frontend = False
        pkg_content = self.files_map.get("package.json", "")
        if pkg_content:
            frontend_frameworks = ["react", "vue", "angular", "next", "nuxt", "svelte", "vite"]
            if any(f in pkg_content.lower() for f in frontend_frameworks):
                is_frontend = True

        if not is_frontend:
            return CheckItemResult(
                rule_id=8,
                title="Frontend Runtime Configuration",
                status=CheckStatus.SKIPPED,
                passed=True,
                details="Repository identified as backend / non-frontend (requirement not applicable)."
            )

        # For frontend, look for runtime config mechanisms
        runtime_keywords = [
            "runtime-config", "window.__env", "window.env", "config.json", 
            "env.js", "docker-entrypoint.sh", "subst", "envsubst"
        ]
        has_runtime = False
        for path, content in self.files_map.items():
            if not content:
                continue
            if any(k in content.lower() for k in runtime_keywords) or any(k in path.lower() for k in ["env.js", "config.json", "docker-entrypoint.sh"]):
                has_runtime = True
                break

        if has_runtime:
            return CheckItemResult(
                rule_id=8,
                title="Frontend Runtime Configuration",
                status=CheckStatus.PASSED,
                passed=True,
                details="Runtime environment variable mechanism (without rebuild) detected."
            )
        else:
            return CheckItemResult(
                rule_id=8,
                title="Frontend Runtime Configuration",
                status=CheckStatus.FAILED,
                passed=False,
                details="Frontend repository lacks runtime environment configuration (Runtime Configuration).",
                remediation="Do not hardcode variables at build-time; implement `window.__ENV__` or runtime `config.json`."
            )

    # 9. Dockerfile & GitLab CI/CD for Build and Test
    def check_9_dockerfile_and_cicd(self) -> CheckItemResult:
        has_dockerfile = any(p.lower().startswith("dockerfile") for p in self.files_map.keys())
        ci_content = ""
        for path, content in self.files_map.items():
            if path in [".gitlab-ci.yml", ".gitlab-ci.yaml"]:
                ci_content = content
                break

        has_cicd = bool(ci_content)
        has_build_job = False
        has_test_job = False

        if has_cicd:
            has_build_job = any(k in ci_content.lower() for k in ["build", "docker build", "kaniko"])
            has_test_job = any(k in ci_content.lower() for k in ["test", "pytest", "npm test", "mvn test", "go test"])

        if has_dockerfile and has_cicd and has_build_job and has_test_job:
            return CheckItemResult(
                rule_id=9,
                title="Standard Dockerfile and CI/CD Build/Test stages",
                status=CheckStatus.PASSED,
                passed=True,
                details="Dockerfile and automated Build/Test stages exist in `.gitlab-ci.yml`."
            )
        elif not has_dockerfile:
            return CheckItemResult(
                rule_id=9,
                title="Standard Dockerfile and CI/CD Build/Test stages",
                status=CheckStatus.FAILED,
                passed=False,
                details="Standard `Dockerfile` was not found in the repository.",
                remediation="Add standard project Dockerfile to repository root."
            )
        else:
            return CheckItemResult(
                rule_id=9,
                title="Standard Dockerfile and CI/CD Build/Test stages",
                status=CheckStatus.FAILED,
                passed=False,
                details="Automated Build or Test stages in `.gitlab-ci.yml` are incomplete.",
                remediation="Complete automated Build and Test jobs in `.gitlab-ci.yml`."
            )
