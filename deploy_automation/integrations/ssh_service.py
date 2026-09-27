import os
import re
import asyncio
import subprocess
from typing import Optional, Any
from pathlib import Path


class SSHService:
    def __init__(self, ssh_config_path: str = "~/.ssh/config"):
        self.ssh_config_path = os.path.expanduser(ssh_config_path)

    def list_ssh_hosts(self) -> list[str]:
        """
        Parses ~/.ssh/config (including Includes) and returns a list of configured Host names.
        """
        import glob
        hosts = []
        parsed_files = set()

        def parse_file(path: str):
            path = os.path.abspath(os.path.expanduser(path))
            if path in parsed_files or not os.path.exists(path):
                return
            parsed_files.add(path)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("Host ") or line.startswith("host "):
                            parts = line.split()[1:]
                            for p in parts:
                                if "*" not in p and "?" not in p:
                                    if p not in hosts:
                                        hosts.append(p)
                        elif line.lower().startswith("include "):
                            inc_path = line.split(maxsplit=1)[1].strip()
                            inc_path = os.path.expanduser(inc_path)
                            if not os.path.isabs(inc_path):
                                inc_path = os.path.join(os.path.dirname(path), inc_path)
                            for matched_file in glob.glob(inc_path, recursive=True):
                                if os.path.isfile(matched_file):
                                    parse_file(matched_file)
            except Exception:
                pass

        parse_file(self.ssh_config_path)
        return hosts

    async def execute_remote_cmd(self, host: str, user: Optional[str], cmd: str, is_modify: bool = False) -> tuple[int, str, str]:
        """
        Executes a shell command on the remote server via SSH.
        If is_modify is True, prompts the user for confirmation before execution.
        Uses SSH multiplexing to avoid repeated passphrase prompts.
        """
        if is_modify:
            print("\n⚠️  The following command will modify the remote server:")
            print("-" * 60)
            print(cmd.strip())
            print("-" * 60)
            confirm = input("Proceed with execution? [Y/n]: ").strip().lower()
            if confirm not in ["", "y", "yes"]:
                print("❌ Execution aborted by user.")
                return 1, "", "Aborted by user"

        target = f"{user}@{host}" if user else host
        # Use ControlMaster to multiplex connections and avoid repeating passphrase prompts
        ssh_cmd = [
            "ssh", 
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ControlMaster=auto",
            "-o", "ControlPath=/tmp/ssh_mux_%h_%p_%r",
            "-o", "ControlPersist=10m",
            target, 
            cmd
        ]
        
        proc = await asyncio.create_subprocess_exec(
            *ssh_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode, stdout.decode("utf-8", errors="ignore"), stderr.decode("utf-8", errors="ignore")

    async def check_gitlab_runner_shell(self, host: str, user: Optional[str]) -> tuple[bool, Optional[str]]:
        """
        Verifies if GitLab Runner with 'shell' executor is configured and running on the target server.
        """
        check_script = """
        if command -v gitlab-runner >/dev/null 2>&1; then
            if [ -f /etc/gitlab-runner/config.toml ]; then
                if grep -qi 'executor = "shell"' /etc/gitlab-runner/config.toml || grep -qi 'executor = "shell"' ~/.gitlab-runner/config.toml 2>/dev/null; then
                    echo "RUNNER_SHELL_OK"
                    exit 0
                fi
            fi
            output=$(gitlab-runner list 2>&1)
            if echo "$output" | grep -qi "shell"; then
                echo "RUNNER_SHELL_OK"
                exit 0
            fi
        fi
        if ps aux | grep -v grep | grep -qi "gitlab-runner"; then
            echo "RUNNER_SHELL_OK"
            exit 0
        fi
        echo "RUNNER_NOT_FOUND"
        exit 1
        """
        code, out, err = await self.execute_remote_cmd(host, user, check_script)
        if "RUNNER_SHELL_OK" in out:
            return True, None
        return False, "Server does not have GitLab Runner with shell executor."

    async def get_gitlab_runner_info(self, host: str, user: Optional[str]) -> dict[str, Any]:
        """
        Inspects remote runner configuration (/etc/gitlab-runner/config.toml, ~/.gitlab-runner/config.toml,
        and `gitlab-runner list`) to extract runner names, tokens, and tags.
        """
        script = """
        if [ -f /etc/gitlab-runner/config.toml ]; then
            cat /etc/gitlab-runner/config.toml
        elif [ -f ~/.gitlab-runner/config.toml ]; then
            cat ~/.gitlab-runner/config.toml
        fi
        echo "===RUNNER_LIST_OUTPUT==="
        gitlab-runner list 2>&1
        """
        code, out, err = await self.execute_remote_cmd(host, user, script)
        runner_info = {
            "ids": [],
            "names": [],
            "tokens": [],
            "tags": [],
            "raw": out
        }
        
        ids = re.findall(r'id\s*=\s*(\d+)', out)
        runner_info["ids"] = [int(i) for i in dict.fromkeys(ids)]

        names = re.findall(r'name\s*=\s*["\']([^"\']+)["\']', out)
        runner_info["names"] = list(dict.fromkeys(names))

        tokens = re.findall(r'token\s*=\s*["\']([^"\']+)["\']', out)
        runner_info["tokens"] = list(dict.fromkeys(tokens))

        tag_blocks = re.findall(r'tags\s*=\s*\[(.*?)\]', out, re.DOTALL)
        tags = []
        for tb in tag_blocks:
            found = re.findall(r'["\']([^"\']+)["\']', tb)
            tags.extend(found)
        runner_info["tags"] = list(dict.fromkeys(tags))

        if "===RUNNER_LIST_OUTPUT===" in out:
            list_part = out.split("===RUNNER_LIST_OUTPUT===")[-1]
            for line in list_part.splitlines():
                line = line.strip()
                if "Executor=" in line:
                    r_name = line.split()[0]
                    if r_name not in runner_info["names"]:
                        runner_info["names"].append(r_name)

        return runner_info

    async def get_server_internal_ip(self, host: str, user: Optional[str]) -> Optional[str]:
        """
        Extracts server internal IP (starting with 10.10. or 10.100.) using `ip -br a` or `ip a`.
        """
        code, out, err = await self.execute_remote_cmd(host, user, "ip -br a 2>/dev/null || ip a 2>/dev/null")
        matches = re.findall(r"\b(10\.(?:10|100)\.\d{1,3}\.\d{1,3})\b", out)
        if matches:
            return matches[0]
        
        fallback_matches = re.findall(r"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b", out)
        if fallback_matches:
            return fallback_matches[0]
        return None

    async def find_available_port(self, host: str, user: Optional[str], preferred_start: int = 8080) -> int:
        """
        Finds a free port on the server among low non-standard port numbers.
        """
        code, out, err = await self.execute_remote_cmd(host, user, "ss -tuln 2>/dev/null || netstat -tuln 2>/dev/null")
        used_ports = set()
        for match in re.finditer(r":(\d+)\b", out):
            used_ports.add(int(match.group(1)))

        candidate = preferred_start
        while candidate in used_ports or candidate in [80, 443, 22, 21, 25, 3306, 5432, 6379, 27017, 9090, 9100]:
            candidate += 1
            if candidate > 9999:
                candidate = 3000

        return candidate

    async def scan_log_shipping_template(self, host: str, user: Optional[str]) -> dict[str, Any]:
        """
        Scans /srv for existing docker-compose.yml files to discover fluent-bit or fluentd logging configs.
        """
        script = """
        for f in /srv/*/docker-compose*.yml /srv/*/docker-compose*.yaml; do
            if [ -f "$f" ]; then
                if grep -qi "fluent" "$f"; then
                    cat "$f"
                    break
                fi
            fi
        done
        """
        code, out, err = await self.execute_remote_cmd(host, user, script)
        
        fluent_address = "localhost:24224"
        match_addr = re.search(r'fluentd-address:\s*["\']?([^"\'\s]+)', out, re.IGNORECASE)
        if match_addr:
            fluent_address = match_addr.group(1)

        return {
            "driver": "fluentd",
            "options": {
                "fluentd-address": fluent_address,
                "tag": "docker.{{.Name}}",
                "fluentd-async-connect": "true"
            }
        }

    async def setup_project_directories_and_env(
        self,
        host: str,
        user: Optional[str],
        project_dir: str,
        project_name: str,
        env_content: str,
        initial_tag: str = "main"
    ) -> bool:
        clean_dir = project_dir.strip("/")
        base_path = f"/srv/{clean_dir}"
        
        tag_var = re.sub(r"[^a-zA-Z0-9_]", "_", project_name).upper() + "_TAG"
        script = f"""
        mkdir -p {base_path}
        cat << 'EOFENV' > {base_path}/.env
{env_content}
{tag_var}={initial_tag}
EOFENV
        """
        code, out, err = await self.execute_remote_cmd(host, user, script, is_modify=True)
        return code == 0

    async def write_remote_compose(
        self,
        host: str,
        user: Optional[str],
        project_dir: str,
        compose_content: str
    ) -> bool:
        clean_dir = project_dir.strip("/")
        base_path = f"/srv/{clean_dir}"
        script = f"""
        cat << 'EOFCOMPOSE' > {base_path}/docker-compose.yml
{compose_content}
EOFCOMPOSE
        """
        code, out, err = await self.execute_remote_cmd(host, user, script, is_modify=True)
        return code == 0

    async def get_srv_tree(self, host: str, user: Optional[str]) -> str:
        script = "command -v tree >/dev/null 2>&1 && tree -L 2 /srv || find /srv -maxdepth 2 -type d 2>/dev/null | sort"
        code, out, err = await self.execute_remote_cmd(host, user, script)
        return out

    async def get_compose_config_json(self, host: str, user: Optional[str], project_dir: str) -> Optional[str]:
        clean_dir = project_dir.strip("/")
        script = f"cd /srv/{clean_dir} && (docker compose config --format json 2>/dev/null || docker-compose config --format json 2>/dev/null)"
        code, out, err = await self.execute_remote_cmd(host, user, script)
        if code == 0 and out.strip().startswith("{"):
            return out
        return None

    async def read_remote_file(self, host: str, user: Optional[str], file_path: str) -> Optional[str]:
        code, out, err = await self.execute_remote_cmd(host, user, f"cat {file_path} 2>/dev/null")
        if code == 0:
            return out
        return None

    async def append_service_to_compose(self, host: str, user: Optional[str], project_dir: str, new_service_block: str) -> bool:
        """
        Appends a new service block right after the 'services:' line in docker-compose.yml
        """
        clean_dir = project_dir.strip("/")
        base_path = f"/srv/{clean_dir}"
        
        # We will use awk to insert the new block after the line 'services:'
        # Escape single quotes in new_service_block
        escaped_block = new_service_block.replace("'", "'\\''")
        
        script = f"""
awk '/^services:/ {{print; print "{escaped_block}"; next}}1' {base_path}/docker-compose.yml > {base_path}/docker-compose.tmp.yml && mv {base_path}/docker-compose.tmp.yml {base_path}/docker-compose.yml
        """
        code, out, err = await self.execute_remote_cmd(host, user, script, is_modify=True)
        return code == 0
