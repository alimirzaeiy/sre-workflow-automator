import unittest
from deploy_automation.engine.compose_generator import DockerComposeGenerator
from deploy_automation.engine.cicd_generator import GitLabCICDGenerator


class TestComposeAndCICD(unittest.TestCase):
    def test_extract_container_port(self):
        dockerfile = """
FROM node:20-alpine
WORKDIR /app
COPY . .
EXPOSE 3000
CMD ["npm", "start"]
"""
        port = DockerComposeGenerator.extract_container_port_from_dockerfile(dockerfile)
        self.assertEqual(port, 3000)

    def test_generate_compose_with_public_route_and_logging(self):
        compose = DockerComposeGenerator.generate_compose(
            project_name="payment-service",
            registry_image="registry.example.com/core/payment-service",
            container_port=8080,
            bound_ip="10.10.1.25",
            external_port=8085,
            has_public_route=True,
            has_log_shipping=True,
            logging_config={"driver": "fluentd", "options": {"fluentd-address": "127.0.0.1:24224"}}
        )

        self.assertIn("image: registry.example.com/core/payment-service:${PAYMENT_SERVICE_TAG:-main}", compose)
        self.assertIn("10.10.1.25:8085:8080", compose)
        self.assertIn("driver: \"fluentd\"", compose)
        self.assertIn("fluentd-address: \"127.0.0.1:24224\"", compose)
        self.assertIn("./.env", compose)

    def test_generate_cicd_main_job(self):
        ci = GitLabCICDGenerator.generate_deploy_jobs(
            project_name="user-service",
            project_dir="user-service",
            environment_type="Main"
        )
        self.assertIn("deploy-main:", ci)
        self.assertIn("docker compose pull", ci)
        self.assertIn("docker compose up -d --force-recreate user-service", ci)
        self.assertIn("only:\n    - main", ci)

    def test_generate_cicd_production_job(self):
        ci = GitLabCICDGenerator.generate_deploy_jobs(
            project_name="user-service",
            project_dir="user-service",
            environment_type="Production"
        )
        self.assertIn("deploy-production:", ci)
        self.assertIn('sed -i "s/^USER_SERVICE_TAG=.*/USER_SERVICE_TAG=$CI_COMMIT_TAG/" .env', ci)
        self.assertIn("only:\n    - tags", ci)

    def test_merge_into_gitlab_ci(self):
        base_ci = """
stages:
  - test
  - build

test_job:
  stage: test
  script: pytest
"""
        updated = GitLabCICDGenerator.merge_into_gitlab_ci(base_ci, "auth", "auth-app", "Main")
        self.assertIn("- deploy", updated)
        self.assertIn("deploy-main:", updated)


if __name__ == "__main__":
    unittest.main()
