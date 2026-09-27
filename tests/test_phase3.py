import unittest
from deploy_automation.integrations.nginx_service import NginxService
from deploy_automation.integrations.postgres_service import PostgresService
from deploy_automation.engine.task_state_detector import DeploymentMilestone, MILESTONE_LABELS


class TestPhase3Modules(unittest.TestCase):
    def test_nginx_location_block(self):
        service = NginxService()
        loc = service.generate_location_block("/api/v1", "10.10.1.50", 8080)
        self.assertIn("location /api/v1", loc)
        self.assertIn("proxy_pass http://10.10.1.50:8080;", loc)
        self.assertIn("proxy_set_header Host $host;", loc)

    def test_nginx_full_server_block(self):
        service = NginxService()
        conf = service.generate_full_server_block("app.example.com", "/v2", "10.100.2.10", 3000, ssl_enabled=True)
        self.assertIn("server_name app.example.com;", conf)
        self.assertIn("listen 443 ssl http2;", conf)
        self.assertIn("/etc/letsencrypt/live/app.example.com/fullchain.pem", conf)
        self.assertIn("location /v2", conf)
        self.assertIn("proxy_pass http://10.100.2.10:3000;", conf)

    def test_postgres_password_generation(self):
        pw = PostgresService.generate_secure_password(32)
        self.assertEqual(len(pw), 32)
        self.assertTrue(any(c.isdigit() for c in pw))
        self.assertTrue(any(c.isalpha() for c in pw))

    def test_milestones_definition(self):
        self.assertEqual(len(MILESTONE_LABELS), 6)
        self.assertIn(DeploymentMilestone.ACCESS_CHECK, MILESTONE_LABELS)
        self.assertIn(DeploymentMilestone.ENV_DB_LOG, MILESTONE_LABELS)


if __name__ == "__main__":
    unittest.main()
