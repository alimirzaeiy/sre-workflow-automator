import re
import asyncio
from typing import Optional, Any
from deploy_automation.integrations.ssh_service import SSHService
from deploy_automation.engine.nginx_parser import modify_or_insert_location


class NginxService:
    def __init__(self, ssh_service: Optional[SSHService] = None):
        self.ssh_service = ssh_service or SSHService()

    async def detect_nginx_setup(self, host: str, user: Optional[str]) -> dict[str, Any]:
        """
        Detects whether Nginx is running as a System Daemon or Docker container on the target server,
        and identifies configuration paths.
        """
        script = """
        result="unknown"
        mode=""
        conf_dir=""
        sites_available=""
        sites_enabled=""
        container_name=""

        # 1. Check System Daemon
        if command -v nginx >/dev/null 2>&1 || [ -d /etc/nginx ]; then
            if ps aux | grep -v grep | grep -qi "nginx: master"; then
                mode="daemon"
                if [ -d /etc/nginx/conf.d ]; then
                    conf_dir="/etc/nginx/conf.d"
                fi
                if [ -d /etc/nginx/sites-available ]; then
                    sites_available="/etc/nginx/sites-available"
                    sites_enabled="/etc/nginx/sites-enabled"
                fi
            fi
        fi

        # 2. Check Docker Container in /srv
        if [ -z "$mode" ] || [ "$mode" != "daemon" ]; then
            if command -v docker >/dev/null 2>&1; then
                c_name=$(docker ps --format '{{.Names}}' | grep -i 'nginx' | head -n 1)
                if [ -n "$c_name" ]; then
                    mode="docker"
                    container_name="$c_name"
                    for d in /srv/*/nginx/conf.d /srv/*/*/nginx/conf.d /srv/nginx/conf.d /srv/general/nginx/conf.d; do
                        if [ -d "$d" ]; then
                            conf_dir="$d"
                            break
                        fi
                    done
                fi
            fi
        fi

        echo "MODE:$mode|CONF_DIR:$conf_dir|SITES_AVAIL:$sites_available|SITES_ENAB:$sites_enabled|CONTAINER:$container_name"
        """
        code, out, err = await self.ssh_service.execute_remote_cmd(host, user, script)
        info = {
            "mode": "daemon",
            "conf_dir": "/etc/nginx/conf.d",
            "sites_available": "/etc/nginx/sites-available",
            "sites_enabled": "/etc/nginx/sites-enabled",
            "container_name": None
        }

        for line in out.splitlines():
            if line.startswith("MODE:"):
                parts = dict(item.split(":", 1) for item in line.split("|") if ":" in item)
                if parts.get("MODE"):
                    info["mode"] = parts["MODE"]
                if parts.get("CONF_DIR"):
                    info["conf_dir"] = parts["CONF_DIR"]
                if parts.get("SITES_AVAIL"):
                    info["sites_available"] = parts["SITES_AVAIL"]
                if parts.get("SITES_ENAB"):
                    info["sites_enabled"] = parts["SITES_ENAB"]
                if parts.get("CONTAINER"):
                    info["container_name"] = parts["CONTAINER"]
                break

        return info

    def generate_location_block(self, path: str, target_ip: str, target_port: int) -> str:
        clean_path = "/" + path.strip("/") if path.strip("/") else "/"
        return f"""
    location {clean_path} {{
        proxy_pass http://{target_ip}:{target_port};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }}
"""

    def generate_full_server_block(self, domain: str, path: str, target_ip: str, target_port: int, ssl_enabled: bool = True) -> str:
        location_block = self.generate_location_block(path, target_ip, target_port)
        
        if ssl_enabled:
            return f"""server {{
    listen 80;
    server_name {domain};
    return 301 https://$host$request_uri;
}}

server {{
    listen 443 ssl http2;
    server_name {domain};

    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

{location_block}
}}
"""
        else:
            return f"""server {{
    listen 80;
    server_name {domain};

{location_block}
}}
"""

    async def find_domain_config(self, host: str, user: Optional[str], domain: str, nginx_info: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        """
        Searches for an existing nginx configuration file containing the domain (server_name).
        Checks standard dirs: /etc/nginx/sites-enabled, /etc/nginx/sites-available, /etc/nginx/conf.d,
        and the conf_dir from nginx_info.
        Returns (conf_file_path, conf_content) or (None, None) if not found.
        """
        conf_dir = nginx_info.get("conf_dir") or "/etc/nginx/conf.d"
        sites_enab = nginx_info.get("sites_enabled") or "/etc/nginx/sites-enabled"
        sites_avail = nginx_info.get("sites_available") or "/etc/nginx/sites-available"

        search_dirs = [sites_enab, sites_avail, conf_dir]
        unique_dirs = list(dict.fromkeys([d for d in search_dirs if d]))
        dirs_str = " ".join(f'"{d}"' for d in unique_dirs)

        search_script = f"""
        domain="{domain}"
        found=""
        for d in {dirs_str}; do
            if [ -d "$d" ]; then
                # 1. Direct file name match (avoid backups)
                for ext in ".conf" ""; do
                    fname="$d/$domain$ext"
                    if [ -f "$fname" ]; then
                        found="$fname"
                        break 2
                    fi
                done
                
                # 2. Grep server_name in files inside directory (excluding backups)
                matches=$(grep -rlE "server_name[[:space:]]+([^;]*[[:space:]])?${{domain}}([[:space:]]|;)" "$d" 2>/dev/null | grep -vE '\.save$|\.bak$|\.swp$|\.old$')
                if [ -n "$matches" ]; then
                    # Try to find a .conf file first
                    conf_match=$(echo "$matches" | grep '\.conf$' | head -n 1)
                    if [ -n "$conf_match" ]; then
                        found="$conf_match"
                        break
                    else
                        found=$(echo "$matches" | head -n 1)
                        break
                    fi
                fi
            fi
        done

        if [ -n "$found" ]; then
            echo "FOUND_CONF:$found"
            echo "---CONTENT_START---"
            cat "$found"
        else
            echo "NOT_FOUND"
        fi
        """
        code, out, _ = await self.ssh_service.execute_remote_cmd(host, user, search_script)
        if "FOUND_CONF:" in out:
            lines = out.splitlines()
            conf_path = ""
            content_lines = []
            capture = False
            for line in lines:
                if line.startswith("FOUND_CONF:"):
                    conf_path = line.split("FOUND_CONF:", 1)[1].strip()
                elif line == "---CONTENT_START---":
                    capture = True
                elif capture:
                    content_lines.append(line)
            return conf_path, "\n".join(content_lines)
        return None, None

    async def check_ssl_cert_exists(self, host: str, user: Optional[str], domain: str, existing_conf_content: Optional[str] = None) -> bool:
        """
        Checks if Let's Encrypt certificates or custom SSL certs already exist on the server
        for the given domain or are already referenced in its existing configuration.
        """
        # 1. If existing config is provided, check if it already configures ssl_certificate
        if existing_conf_content:
            cert_matches = re.findall(r'ssl_certificate\s+([^;]+);', existing_conf_content)
            for cert_path in cert_matches:
                clean_path = cert_path.strip().strip('"').strip("'")
                # Test if the referenced certificate file exists on server
                code, out, _ = await self.ssh_service.execute_remote_cmd(host, user, f"[ -f '{clean_path}' ] && echo 'EXISTS'")
                if "EXISTS" in out:
                    return True

        # 2. Check standard certbot live directory for this domain or parent domain
        check_script = f"""
        d="{domain}"
        if [ -f "/etc/letsencrypt/live/$d/fullchain.pem" ] && [ -f "/etc/letsencrypt/live/$d/privkey.pem" ]; then
            echo 'CERT_EXISTS'
            exit 0
        fi
        # Check wildcard or base domain (e.g. app.example.com for main.app.example.com)
        base="${{d#*.}}"
        if [ -n "$base" ] && [ "$base" != "$d" ]; then
            if [ -f "/etc/letsencrypt/live/$base/fullchain.pem" ] && [ -f "/etc/letsencrypt/live/$base/privkey.pem" ]; then
                echo 'CERT_EXISTS'
                exit 0
            fi
        fi
        echo 'CERT_NOT_FOUND'
        """
        code, out, _ = await self.ssh_service.execute_remote_cmd(host, user, check_script)
        return "CERT_EXISTS" in out

    async def issue_ssl_certificate(
        self,
        host: str,
        user: Optional[str],
        domain: str,
        challenge_type: str = "http",
        nginx_mode: str = "daemon"
    ) -> tuple[bool, str]:
        """
        Runs certbot on the server to acquire Let's Encrypt SSL certificate if not already present.
        """
        # Check if already present
        if await self.check_ssl_cert_exists(host, user, domain):
            return True, f"SSL certificate already exists for {domain} in /etc/letsencrypt/live/{domain}/. Skipping issuance."

        if challenge_type.lower() == "dns":
            cmd = f"certbot certonly --manual --preferred-challenges dns -d {domain} --agree-tos --no-eff-email -m admin@{domain}"
            return False, f"For DNS challenge, please execute this command on the server and add the TXT record:\n{cmd}"
        else:
            if nginx_mode == "daemon":
                cmd = f"certbot certonly --nginx -d {domain} --non-interactive --agree-tos --register-unsafely-without-email || certbot certonly --standalone -d {domain} --non-interactive --agree-tos --register-unsafely-without-email"
            else:
                cmd = f"certbot certonly --webroot -w /var/www/certbot -d {domain} --non-interactive --agree-tos --register-unsafely-without-email || certbot certonly --standalone -d {domain} --non-interactive --agree-tos --register-unsafely-without-email"

            code, out, err = await self.ssh_service.execute_remote_cmd(host, user, cmd, is_modify=True)
            if code == 0:
                return True, "SSL Certificate obtained successfully."
            return False, f"Certbot error:\n{err or out}"

    async def apply_nginx_config(
        self,
        host: str,
        user: Optional[str],
        domain: str,
        path: str,
        target_ip: str,
        target_port: int,
        nginx_info: dict[str, Any],
        ssl_enabled: bool = True
    ) -> tuple[bool, str]:
        conf_dir = nginx_info.get("conf_dir") or "/etc/nginx/conf.d"
        sites_avail = nginx_info.get("sites_available")
        sites_enab = nginx_info.get("sites_enabled")
        mode = nginx_info.get("mode", "daemon")
        container = nginx_info.get("container_name")

        clean_path = "/" + path.strip("/") if path.strip("/") else "/"
        new_location = self.generate_location_block(clean_path, target_ip, target_port)

        # 1. Always create backup of main /etc/nginx/nginx.conf first if it exists
        main_backup_script = """
        if [ -f /etc/nginx/nginx.conf ]; then
            ts=$(date +%Y%m%d_%H%M%S)
            cp /etc/nginx/nginx.conf /etc/nginx/nginx.conf.bak_$ts
            echo "MAIN_BACKUP_DONE:/etc/nginx/nginx.conf.bak_$ts"
        fi
        """
        _, out_mbak, _ = await self.ssh_service.execute_remote_cmd(host, user, main_backup_script, is_modify=False)
        for l in out_mbak.splitlines():
            if "MAIN_BACKUP_DONE:" in l:
                print(f"🛡️ Main Nginx config backup created: {l.split('MAIN_BACKUP_DONE:', 1)[1].strip()}")

        # 2. Search for existing configuration file for this domain
        existing_conf_path, existing_content = await self.find_domain_config(host, user, domain, nginx_info)
        domain_backup_path = None

        if existing_conf_path:
            target_conf = existing_conf_path

            # 3. Create timestamped backup of the domain configuration file
            backup_script = f"""
            ts=$(date +%Y%m%d_%H%M%S)
            cp "{target_conf}" "{target_conf}.bak_$ts"
            echo "BACKUP_DONE:{target_conf}.bak_$ts"
            """
            code_bak, out_bak, _ = await self.ssh_service.execute_remote_cmd(host, user, backup_script, is_modify=False)
            for l in out_bak.splitlines():
                if "BACKUP_DONE:" in l:
                    domain_backup_path = l.split("BACKUP_DONE:", 1)[1].strip()
                    print(f"🛡️ Domain config backup created: {domain_backup_path}")

            # 4. Use safe AST parser (handles comments, quotes, nested blocks, true 443 listen check)
            success_mod, new_content, mod_msg = modify_or_insert_location(
                content=existing_content,
                domain=domain,
                location_path=clean_path,
                new_location_block=new_location
            )

            if not success_mod:
                print(f"❌ Failed to parse and modify configuration safely: {mod_msg}")
                return False, f"Nginx parse error: {mod_msg}"

            print(f"📝 {mod_msg}")

            print("\n👀 Preview of new Nginx configuration:\n" + "=" * 60)
            print(new_content.strip())
            print("=" * 60)

            confirm = input("Proceed with applying this configuration? [Y/n]: ").strip().lower()
            if confirm not in ["", "y", "yes"]:
                print("❌ Configuration update aborted by user.")
                return False, "Aborted by user"

            # Write updated content safely to remote file
            # Use python to write content without shell escaping or backreference issues
            import base64
            b64_content = base64.b64encode(new_content.encode("utf-8")).decode("ascii")
            write_script = f"""
            python3 -c "
import base64
raw = base64.b64decode('{b64_content}').decode('utf-8')
with open('{target_conf}', 'w', encoding='utf-8') as f:
    f.write(raw)
print('WRITE_SUCCESS')
"
            """
            await self.ssh_service.execute_remote_cmd(host, user, write_script, is_modify=False)

        else:
            # Brand new domain configuration
            if sites_avail and sites_enab:
                target_conf = f"{sites_avail}/{domain}.conf"
                symlink_cmd = f"ln -sf {target_conf} {sites_enab}/{domain}.conf"
            else:
                target_conf = f"{conf_dir}/{domain}.conf"
                symlink_cmd = ""

            conf_content = self.generate_full_server_block(domain, path, target_ip, target_port, ssl_enabled)
            
            print("\n👀 Preview of new Nginx configuration:\n" + "=" * 60)
            print(conf_content.strip())
            print("=" * 60)

            confirm = input("Proceed with applying this configuration? [Y/n]: ").strip().lower()
            if confirm not in ["", "y", "yes"]:
                print("❌ Configuration update aborted by user.")
                return False, "Aborted by user"

            write_script = f"""
            mkdir -p $(dirname {target_conf})
            cat << 'EOFNGINX' > {target_conf}
{conf_content}
EOFNGINX
            {symlink_cmd}
            """
            await self.ssh_service.execute_remote_cmd(host, user, write_script, is_modify=False)

        # 5. Strict Validation and Conditional Reload
        # Only reload if nginx -t succeeds!
        if mode == "daemon":
            test_cmd = "nginx -t"
            reload_cmd = "systemctl reload nginx || nginx -s reload"
        else:
            c = container or "nginx"
            test_cmd = f"docker exec {c} nginx -t"
            reload_cmd = f"docker exec {c} nginx -s reload"

        print("🔍 Validating Nginx configuration syntax (nginx -t)...")
        code, out, err = await self.ssh_service.execute_remote_cmd(host, user, test_cmd, is_modify=False)
        if code == 0:
            print("✅ Nginx syntax is valid. Reloading Nginx service...")
            await self.ssh_service.execute_remote_cmd(host, user, reload_cmd, is_modify=False)
            return True, f"Nginx configuration successfully applied and reloaded ({domain}{clean_path} -> {target_ip}:{target_port})."
        else:
            # Rollback if backup was made
            print(f"❌ Nginx validation failed (exit code {code})!")
            if domain_backup_path and existing_conf_path:
                print(f"🔄 Rolling back {target_conf} to backup {domain_backup_path}...")
                await self.ssh_service.execute_remote_cmd(host, user, f"cp '{domain_backup_path}' '{target_conf}'", is_modify=False)
                # re-check syntax after rollback
                await self.ssh_service.execute_remote_cmd(host, user, test_cmd, is_modify=False)
                print("🛡️ Configuration rolled back to original state.")
            elif not existing_conf_path and target_conf:
                # Remove newly created file that broke config
                print(f"🔄 Removing invalid newly created config {target_conf}...")
                await self.ssh_service.execute_remote_cmd(host, user, f"rm -f '{target_conf}'", is_modify=False)

            return False, f"Nginx validation failed (nginx -t):\n{err or out}"
