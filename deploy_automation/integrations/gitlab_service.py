import re
import json
import base64
import asyncio
import urllib.request
import urllib.parse
from typing import Optional, Any
from deploy_automation.config import settings


class GitLabProjectProxy:
    def __init__(self, project_data: dict, gl_service: "GitLabService"):
        self.data = project_data
        self.id = project_data.get("id")
        self.default_branch = project_data.get("default_branch", "main")
        self.gl_service = gl_service
        self.files = GitLabFilesProxy(self)

    async def repository_tree(self, recursive=True, all=True, ref="main"):
        return await self.gl_service.fetch_repo_tree(self.id, ref=ref, recursive=recursive)


class GitLabFilesProxy:
    def __init__(self, project_proxy: GitLabProjectProxy):
        self.project = project_proxy

    async def get(self, file_path: str, ref: str = "main"):
        return await self.project.gl_service.get_raw_file(self.project.id, file_path, ref=ref)

    async def create(self, data: dict):
        return await self.project.gl_service.create_file(self.project.id, data)


class GitLabRawFile:
    def __init__(self, content_str: str, file_path: str, project_proxy: GitLabProjectProxy):
        self._content = content_str
        self.file_path = file_path
        self.project = project_proxy
        self.content = content_str

    def decode(self):
        class _Decoded:
            def __init__(self, text):
                self._text = text
            def decode(self, *args, **kwargs):
                return self._text
        return _Decoded(self.content)

    async def save(self, branch: str = "main", commit_message: str = ""):
        await self.project.gl_service.update_file(self.project.id, self.file_path, self.content, branch, commit_message)


class GitLabService:
    def __init__(self, url: str = settings.GITLAB_URL, token: str = settings.GITLAB_TOKEN):
        self.url = url.rstrip("/")
        self.token = token
        self.api_base = f"{self.url}/api/v4"
        self.headers = {
            "PRIVATE-TOKEN": self.token,
            "Content-Type": "application/json",
            "User-Agent": "DeployAutomation/1.0"
        }

    async def _http_req(self, method: str, path: str, params: Optional[dict] = None, data: Optional[dict] = None) -> tuple[int, Any]:
        def _sync_req():
            full_url = f"{self.api_base}/{path.lstrip('/')}"
            if params:
                query_str = urllib.parse.urlencode(params, doseq=True)
                full_url = f"{full_url}?{query_str}"

            body_bytes = None
            if data is not None:
                body_bytes = json.dumps(data).encode("utf-8")

            req = urllib.request.Request(full_url, data=body_bytes, headers=self.headers, method=method.upper())
            try:
                with urllib.request.urlopen(req, timeout=15) as response:
                    status = response.status
                    resp_body = response.read().decode("utf-8", errors="ignore")
                    try:
                        return status, json.loads(resp_body)
                    except Exception:
                        return status, resp_body
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="ignore")
                try:
                    return e.code, json.loads(err_body)
                except Exception:
                    return e.code, err_body
            except Exception as ex:
                return 500, str(ex)

        return await asyncio.to_thread(_sync_req)

    def extract_project_path(self, repo_url: str) -> str:
        cleaned = repo_url.strip()
        if cleaned.endswith(".git"):
            cleaned = cleaned[:-4]
        if ":" in cleaned and not cleaned.startswith("http"):
            parts = cleaned.split(":", 1)
            return parts[1].strip("/")
        if "://" in cleaned:
            parsed = urllib.parse.urlparse(cleaned)
            return parsed.path.strip("/")
        return cleaned.strip("/")

    async def get_current_user(self) -> Optional[dict]:
        status, data = await self._http_req("GET", "/user")
        if status == 200 and isinstance(data, dict):
            return data
        return None

    async def get_project(self, repo_identifier: str) -> Optional[GitLabProjectProxy]:
        project_path = self.extract_project_path(repo_identifier)
        encoded_path = urllib.parse.quote(project_path, safe="")
        status, data = await self._http_req("GET", f"/projects/{encoded_path}")
        if status == 200 and isinstance(data, dict):
            return GitLabProjectProxy(data, self)
        return None

    async def check_maintainer_access(self, repo_identifier: str) -> tuple[bool, Optional[str]]:
        user_data = await self.get_current_user()
        if not user_data:
            return False, "GitLab token authentication failed or user could not be retrieved."

        if user_data.get("is_admin"):
            return True, None

        user_id = user_data.get("id")
        project = await self.get_project(repo_identifier)
        if not project:
            return False, f"Project '{repo_identifier}' not found in GitLab or insufficient permissions."

        # Check members_all
        status, members = await self._http_req("GET", f"/projects/{project.id}/members/all/{user_id}")
        if status == 200 and isinstance(members, dict):
            lvl = members.get("access_level", 0)
            if lvl >= settings.GITLAB_MIN_ACCESS_LEVEL:
                return True, None
            return False, f"Access level is {lvl}, minimum Maintainer (40) required."

        # Check permissions object
        perms = project.data.get("permissions") or {}
        p_acc = (perms.get("project_access") or {}).get("access_level", 0)
        g_acc = (perms.get("group_access") or {}).get("access_level", 0)
        if max(p_acc, g_acc) >= settings.GITLAB_MIN_ACCESS_LEVEL:
            return True, None

        return False, "Maintainer access not found for this project."

    async def fetch_repo_tree(self, project_id: int, ref: str = "main", recursive: bool = True) -> list[dict]:
        params = {"ref": ref, "recursive": str(recursive).lower(), "per_page": 100}
        status, tree = await self._http_req("GET", f"/projects/{project_id}/repository/tree", params=params)
        if status == 200 and isinstance(tree, list):
            return tree
        return []

    async def get_raw_file(self, project_id: int, file_path: str, ref: str = "main") -> GitLabRawFile:
        enc_file = urllib.parse.quote(file_path, safe="")
        params = {"ref": ref}
        status, data = await self._http_req("GET", f"/projects/{project_id}/repository/files/{enc_file}", params=params)
        if status == 200 and isinstance(data, dict):
            raw_b64 = data.get("content", "")
            try:
                decoded_str = base64.b64decode(raw_b64).decode("utf-8", errors="ignore")
            except Exception:
                decoded_str = raw_b64
            proxy = GitLabProjectProxy({"id": project_id}, self)
            return GitLabRawFile(decoded_str, file_path, proxy)
        raise Exception(f"File {file_path} not found in repository")

    async def create_file(self, project_id: int, data: dict):
        enc_file = urllib.parse.quote(data.get("file_path", ""), safe="")
        payload = {
            "branch": data.get("branch", "main"),
            "content": data.get("content", ""),
            "commit_message": data.get("commit_message", "Auto update")
        }
        status, res = await self._http_req("POST", f"/projects/{project_id}/repository/files/{enc_file}", data=payload)
        if status not in [200, 201]:
            raise Exception(f"GitLab create_file failed (HTTP {status}): {res}")
        return True

    async def update_file(self, project_id: int, file_path: str, content: str, branch: str = "main", commit_message: str = ""):
        enc_file = urllib.parse.quote(file_path, safe="")
        payload = {
            "branch": branch,
            "content": content,
            "commit_message": commit_message or "Auto update"
        }
        status, res = await self._http_req("PUT", f"/projects/{project_id}/repository/files/{enc_file}", data=payload)
        if status not in [200, 201]:
            raise Exception(f"GitLab update_file failed (HTTP {status}): {res}")
        return True

    async def get_project_runners(self, project_id: int) -> list[dict]:
        status, runners = await self._http_req("GET", f"/projects/{project_id}/runners?all=true")
        if status == 200 and isinstance(runners, list):
            return runners
        return []

    async def get_runner_details(self, runner_id: int) -> Optional[dict]:
        status, runner = await self._http_req("GET", f"/runners/{runner_id}")
        if status == 200 and isinstance(runner, dict):
            return runner
        return None

    async def get_repository_files_map(self, project: Any, ref: str = "main") -> dict[str, str]:
        files_content: dict[str, str] = {}
        target_ref = ref if ref else getattr(project, "default_branch", "main")
        project_id = project.id if hasattr(project, "id") else project.get("id")

        tree = await self.fetch_repo_tree(project_id, ref=target_ref, recursive=True)
        file_paths = [item["path"] for item in tree if item.get("type") == "blob"]

        important_patterns = [
            r"^doc/.*",
            r"^docs/.*",
            r"^\.gitlab-ci\.ya?ml$",
            r"^Dockerfile.*",
            r"^docker-compose.*",
            r"^package\.json$",
            r"^pom\.xml$",
            r"^go\.mod$",
            r"^requirements\.txt$",
            r"^src/.*metrics.*",
            r"^src/.*health.*",
            r"^.*health.*",
            r"^.*metric.*",
        ]

        for path in file_paths:
            is_important = any(re.search(pat, path, re.IGNORECASE) for pat in important_patterns)
            if is_important:
                try:
                    f = await self.get_raw_file(project_id, path, ref=target_ref)
                    files_content[path] = f.content
                except Exception:
                    files_content[path] = ""
            else:
                files_content[path] = ""

        return files_content

    async def get_branches_and_protection(self, project: Any) -> list[dict[str, Any]]:
        project_id = project.id if hasattr(project, "id") else project.get("id")
        status, branches = await self._http_req("GET", f"/projects/{project_id}/repository/branches")
        results = []
        if status == 200 and isinstance(branches, list):
            for b in branches:
                results.append({
                    "name": b.get("name"),
                    "protected": b.get("protected", False),
                    "default": b.get("default", False)
                })
        return results

    async def create_branch(self, project_id: int, branch_name: str, ref: str):
        payload = {"branch": branch_name, "ref": ref}
        status, res = await self._http_req("POST", f"/projects/{project_id}/repository/branches", data=payload)
        return status in [200, 201], res

    async def create_merge_request(self, project_id: int, source_branch: str, target_branch: str, title: str):
        payload = {
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": title
        }
        status, res = await self._http_req("POST", f"/projects/{project_id}/merge_requests", data=payload)
        return status in [200, 201], res

    async def get_or_create_merge_request(self, project_id: int, source_branch: str, target_branch: str, title: str) -> tuple[bool, dict]:
        status, mrs = await self._http_req("GET", f"/projects/{project_id}/merge_requests", params={
            "state": "opened",
            "source_branch": source_branch,
            "target_branch": target_branch
        })
        if status == 200 and isinstance(mrs, list) and len(mrs) > 0:
            return True, mrs[0]
        return await self.create_merge_request(project_id, source_branch, target_branch, title)

    async def check_merge_request_status(self, project_id: int, mr_iid: int):
        status, res = await self._http_req("GET", f"/projects/{project_id}/merge_requests/{mr_iid}")
        if status == 200 and isinstance(res, dict):
            return res.get("state")
        return None


    async def get_pipeline(self, project_id: int, pipeline_id: int):
        status, res = await self._http_req("GET", f"/projects/{project_id}/pipelines/{pipeline_id}")
        if status == 200 and isinstance(res, dict):
            return res
        return None

    async def get_latest_pipeline(self, project_id: int, ref: str):
        status, res = await self._http_req("GET", f"/projects/{project_id}/pipelines?ref={ref}&order_by=id&sort=desc&per_page=1")
        if status == 200 and isinstance(res, list) and len(res) > 0:
            return res[0]
        return None

    async def get_pipeline_jobs(self, project_id: int, pipeline_id: int):
        status, res = await self._http_req("GET", f"/projects/{project_id}/pipelines/{pipeline_id}/jobs")
        if status == 200 and isinstance(res, list):
            return res
        return []

    async def retry_job(self, project_id: int, job_id: int):
        status, res = await self._http_req("POST", f"/projects/{project_id}/jobs/{job_id}/retry")
        return status in [200, 201]

    async def get_job_trace(self, project_id: int, job_id: int):
        status, res = await self._http_req("GET", f"/projects/{project_id}/jobs/{job_id}/trace")
        if status == 200:
            return res
        return ""
