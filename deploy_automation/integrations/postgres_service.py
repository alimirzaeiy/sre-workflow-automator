import re
import secrets
import string
import asyncio
from typing import Optional, Any
from deploy_automation.integrations.ssh_service import SSHService


class PostgresService:
    def __init__(self, ssh_service: Optional[SSHService] = None):
        self.ssh_service = ssh_service or SSHService()

    @staticmethod
    def generate_secure_password(length: int = 24) -> str:
        chars = string.ascii_letters + string.digits
        return "".join(secrets.choice(chars) for _ in range(length))

    async def detect_postgres_setup(self, host: str, user: Optional[str]) -> dict[str, Any]:
        """
        Detects whether PostgreSQL is running as a System Daemon or Docker container,
        and finds pg_hba.conf path.
        """
        script = """
        mode=""
        container_name=""
        hba_path=""

        # 1. Check System Daemon
        if command -v psql >/dev/null 2>&1 || [ -d /etc/postgresql ]; then
            if ps aux | grep -v grep | grep -qi "postgres"; then
                mode="daemon"
                hba_path=$(find /etc/postgresql /var/lib/postgresql -name "pg_hba.conf" 2>/dev/null | head -n 1)
            fi
        fi

        # 2. Check Docker Container in /srv
        if [ -z "$mode" ] || [ "$mode" != "daemon" ]; then
            if command -v docker >/dev/null 2>&1; then
                c_name=$(docker ps --format '{{.Names}}' | grep -i 'postgres' | head -n 1)
                if [ -n "$c_name" ]; then
                    mode="docker"
                    container_name="$c_name"
                    hba_path=$(find /srv -name "pg_hba.conf" 2>/dev/null | head -n 1)
                fi
            fi
        fi

        echo "MODE:$mode|CONTAINER:$container_name|HBA:$hba_path"
        """
        code, out, err = await self.ssh_service.execute_remote_cmd(host, user, script)

        if code != 0 and not out.strip():
            raise ConnectionError(
                f"SSH command failed on {user + '@' if user else ''}{host} (exit code {code}). "
                f"Error: {err.strip() or 'No error output'}"
            )

        info = {
            "mode": "daemon",
            "container_name": None,
            "hba_path": ""
        }

        for line in out.splitlines():
            if line.startswith("MODE:"):
                parts = dict(item.split(":", 1) for item in line.split("|") if ":" in item)
                if parts.get("MODE"):
                    info["mode"] = parts["MODE"]
                if parts.get("CONTAINER"):
                    info["container_name"] = parts["CONTAINER"]
                if parts.get("HBA"):
                    info["hba_path"] = parts["HBA"]
                break

        return info

    async def execute_sql_query(
        self,
        host: str,
        user: Optional[str],
        sql_cmd: str,
        pg_info: dict[str, Any]
    ) -> tuple[int, str, str]:
        mode = pg_info.get("mode", "daemon")
        container = pg_info.get("container_name")

        if mode == "daemon":
            remote_cmd = f"sudo -u postgres psql -c \"{sql_cmd}\""
        else:
            c = container or "postgres"
            remote_cmd = f"docker exec -i {c} psql -U postgres -c \"{sql_cmd}\""

        return await self.ssh_service.execute_remote_cmd(host, user, remote_cmd, is_modify=True)

    async def provision_database(
        self,
        host: str,
        user: Optional[str],
        dbname: str,
        dbuser: str,
        dbpass: str,
        app_internal_ip: str,
        pg_info: dict[str, Any],
        ssl_required: bool = False
    ) -> tuple[bool, str, str]:
        clean_db = re.sub(r"[^a-zA-Z0-9_]", "_", dbname).lower()
        clean_user = re.sub(r"[^a-zA-Z0-9_]", "_", dbuser).lower()

        create_user_sql = f"DO \\$\\$ BEGIN IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '{clean_user}') THEN CREATE ROLE {clean_user} WITH LOGIN ENCRYPTED PASSWORD '{dbpass}'; ELSE ALTER ROLE {clean_user} WITH ENCRYPTED PASSWORD '{dbpass}'; END IF; END \\$\\$;"
        await self.execute_sql_query(host, user, create_user_sql, pg_info)
        
        create_db_sql = f"SELECT 'CREATE DATABASE {clean_db} OWNER {clean_user}' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '{clean_db}')\\gexec"
        await self.execute_sql_query(host, user, create_db_sql, pg_info)

        grant_sql = f"GRANT ALL PRIVILEGES ON DATABASE {clean_db} TO {clean_user};"
        await self.execute_sql_query(host, user, grant_sql, pg_info)

        hba_path = pg_info.get("hba_path")
        hba_line = f"host    {clean_db}    {clean_user}    {app_internal_ip}/32    md5"
        if ssl_required:
            hba_line = f"hostssl {clean_db}    {clean_user}    {app_internal_ip}/32    md5"

        if hba_path:
            add_hba_script = f"""
            if ! grep -qs "{app_internal_ip}" "{hba_path}"; then
                echo "{hba_line}" >> "{hba_path}"
                echo "HBA_ADDED"
            fi
            """
            await self.ssh_service.execute_remote_cmd(host, user, add_hba_script, is_modify=True)
        else:
            if pg_info.get("mode") == "docker":
                c = pg_info.get("container_name") or "postgres"
                docker_hba_cmd = f"docker exec -i {c} bash -c 'echo \"{hba_line}\" >> /var/lib/postgresql/data/pg_hba.conf 2>/dev/null || true'"
                await self.ssh_service.execute_remote_cmd(host, user, docker_hba_cmd, is_modify=True)

        reload_sql = "SELECT pg_reload_conf();"
        await self.execute_sql_query(host, user, reload_sql, pg_info)

        ssl_mode_param = "?sslmode=require" if ssl_required else ""
        dsn = f"postgresql://{clean_user}:{dbpass}@{app_internal_ip}:5432/{clean_db}{ssl_mode_param}"
        msg = f"Database '{clean_db}' and user '{clean_user}' created successfully. Access for IP '{app_internal_ip}' configured in pg_hba.conf."
        return True, dsn, msg
