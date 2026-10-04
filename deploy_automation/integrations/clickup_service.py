import os
import re
import json
import time
import asyncio
import urllib.request
import urllib.parse
from typing import Optional, Any
from deploy_automation.config import settings
from deploy_automation.models import ClickUpTaskInfo


class ClickUpService:
    def __init__(self, api_token: Optional[str] = None, proxy: Optional[str] = None):
        self.api_token = api_token if api_token is not None else settings.CLICKUP_API_TOKEN
        self.base_url = settings.CLICKUP_API_BASE.rstrip("/")
        self._cached_deploy_list_id: Optional[str] = None
        self._cached_user_id: Optional[int] = None
        self.proxy = proxy or os.getenv("PROXY") or getattr(settings, "PROXY", None) or os.getenv("HTTPS_PROXY") or getattr(settings, "HTTPS_PROXY", None) or os.getenv("HTTP_PROXY") or getattr(settings, "HTTP_PROXY", None) or os.getenv("ALL_PROXY") or getattr(settings, "ALL_PROXY", None)
        self._opener = None
        if self.proxy:
            proxy_handler = urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})
            self._opener = urllib.request.build_opener(proxy_handler)

    @property
    def headers(self) -> dict:
        return {
            "Authorization": self.api_token or settings.CLICKUP_API_TOKEN,
            "Content-Type": "application/json",
            "User-Agent": "DeployAutomation/1.0"
        }

    async def _http_request(self, method: str, url: str, params: Optional[dict] = None, data: Optional[dict] = None) -> tuple[int, Any]:
        def _sync_req():
            full_url = url
            if params:
                query_str = urllib.parse.urlencode(params, doseq=True)
                full_url = f"{url}?{query_str}"
            
            body_bytes = None
            if data is not None:
                body_bytes = json.dumps(data).encode("utf-8")

            max_retries = 3
            for attempt in range(max_retries):
                req = urllib.request.Request(full_url, data=body_bytes, headers=self.headers, method=method.upper())
                try:
                    open_fn = self._opener.open if self._opener else urllib.request.urlopen
                    with open_fn(req, timeout=15) as response:
                        status = response.status
                        resp_body = response.read().decode("utf-8", errors="ignore")
                        try:
                            parsed_json = json.loads(resp_body)
                        except Exception:
                            parsed_json = resp_body
                        return status, parsed_json
                except urllib.error.HTTPError as e:
                    if e.code == 429 and attempt < max_retries - 1:
                        # Respect Retry-After header if provided, otherwise exponential backoff
                        retry_after = e.headers.get("Retry-After")
                        sleep_time = float(retry_after) if retry_after else (1.5 * (attempt + 1))
                        time.sleep(sleep_time)
                        continue

                    err_body = e.read().decode("utf-8", errors="ignore")
                    try:
                        err_json = json.loads(err_body)
                    except Exception:
                        err_json = err_body
                    return e.code, err_json
                except Exception as ex:
                    return 500, str(ex)

            return 500, "Max retries exceeded"

        return await asyncio.to_thread(_sync_req)

    async def find_deploy_list_id(self, team_id: Optional[str] = None) -> Optional[str]:
        """
        Locates the Deploy list under SRE space -> Form folder.
        """
        if self._cached_deploy_list_id:
            return self._cached_deploy_list_id

        target_team = team_id or settings.CLICKUP_TEAM_ID
        if not target_team:
            return None

        # 1. Fetch Spaces
        status, spaces_data = await self._http_request("GET", f"{self.base_url}/team/{target_team}/space")
        if status != 200 or not isinstance(spaces_data, dict):
            return None

        target_space_name = settings.CLICKUP_SRE_SPACE_NAME.lower()
        target_list_name = settings.CLICKUP_DEPLOY_FORM_NAME.lower()

        for sp in spaces_data.get("spaces", []):
            sp_name = sp.get("name", "").lower()
            sp_id = sp.get("id")
            
            if target_space_name in sp_name or "sre" in sp_name:
                # Check folders in space
                _, folders_data = await self._http_request("GET", f"{self.base_url}/space/{sp_id}/folder")
                if isinstance(folders_data, dict):
                    for fld in folders_data.get("folders", []):
                        fld_name = fld.get("name", "").lower()
                        is_form_folder = "form" in fld_name or "فرم" in fld_name
                        for lst in fld.get("lists", []):
                            lst_name = lst.get("name", "").lower()
                            if is_form_folder and (target_list_name in lst_name or "deploy" in lst_name):
                                self._cached_deploy_list_id = str(lst.get("id"))
                                return self._cached_deploy_list_id
                            elif lst_name == target_list_name or lst_name == "deploy":
                                self._cached_deploy_list_id = str(lst.get("id"))
                                return self._cached_deploy_list_id

                # Check folderless lists in space
                _, lists_data = await self._http_request("GET", f"{self.base_url}/space/{sp_id}/list")
                if isinstance(lists_data, dict):
                    for lst in lists_data.get("lists", []):
                        lst_name = lst.get("name", "").lower()
                        if lst_name == target_list_name or lst_name == "deploy":
                            self._cached_deploy_list_id = str(lst.get("id"))
                            return self._cached_deploy_list_id

        return None

    async def get_task(self, task_id: str) -> Optional[ClickUpTaskInfo]:
        url = f"{self.base_url}/task/{task_id}"
        status, data = await self._http_request("GET", url, params={"include_subtasks": "false"})
        if status != 200 or not isinstance(data, dict):
            return None
        return self.parse_task_data(data)

    def parse_task_data(self, data: dict[str, Any]) -> ClickUpTaskInfo:
        task_id = data.get("id", "")
        creator = data.get("creator") or {}
        reporter_id = creator.get("id")
        reporter_username = creator.get("username")
        reporter_email = creator.get("email")

        custom_fields = data.get("custom_fields") or []
        repo_url = None
        service_needs = []
        environment = None
        custom_field_dict = {}
        envs_raw = None

        target_repo_field_names = [
            settings.CLICKUP_REPO_FIELD_NAME.lower(),
            "repo",
            "repository",
            "git repo",
            "gitlab repo",
            "آدرس ریپو",
            "ریپازیتوری",
            "ریپو"
        ]

        for cf in custom_fields:
            cf_name = cf.get("name", "").strip().lower()
            val = cf.get("value")
            type_config = cf.get("type_config") or {}
            custom_field_dict[cf_name] = val
            
            # Repo field
            if cf_name in target_repo_field_names and val:
                if isinstance(val, str):
                    repo_url = val.strip()
                elif isinstance(val, dict) and "url" in val:
                    repo_url = val["url"].strip()

            # Service needs field (labels/multi-select)
            if "service need" in cf_name or "service_need" in cf_name or "نیازهای سرویس" in cf_name:
                if isinstance(val, list):
                    options = type_config.get("options") or []
                    opt_map = {opt.get("id"): opt.get("label", opt.get("name", "")) for opt in options if "id" in opt}
                    for v in val:
                        if isinstance(v, str):
                            service_needs.append(opt_map.get(v, v))
                        elif isinstance(v, dict):
                            service_needs.append(v.get("label", v.get("name", "")))
                        elif isinstance(v, int) and v < len(options):
                            service_needs.append(options[v].get("label", options[v].get("name", "")))
                elif isinstance(val, str):
                    service_needs.append(val)

            # Environment field (dropdown/select)
            is_env_field = (
                cf_name in ["environment", "🏞️ environment", "محیط", "env", "در کدوم محیط این تسک باید اجرا بشه ؟"]
                or (cf.get("type") == "drop_down" and ("environment" in cf_name or "محیط" in cf_name))
            )
            if is_env_field and cf.get("type") != "checkbox" and val is not None:
                options = type_config.get("options") or []
                if isinstance(val, int):
                    matched_opt = None
                    for opt in options:
                        if opt.get("orderindex") == val:
                            matched_opt = opt
                            break
                    if not matched_opt and val < len(options):
                        matched_opt = options[val]
                    if matched_opt:
                        environment = matched_opt.get("name", matched_opt.get("label", ""))
                elif isinstance(val, str):
                    matched = False
                    for opt in options:
                        if opt.get("id") == val:
                            environment = opt.get("name", opt.get("label", ""))
                            matched = True
                            break
                    if not matched:
                        environment = val

            # Envs field (key=value pairs for env var changes)
            if cf_name in ["envs", "env vars", "env variables", "متغیرهای محیطی"]:
                if isinstance(val, str) and val.strip():
                    envs_raw = val.strip()
                elif isinstance(val, list):
                    envs_raw = "\n".join(str(v) for v in val if v)

        # Fallback environment detection from task title if missing or invalid
        task_title = data.get("name", "")
        if not environment or environment.lower() in ["true", "false"]:
            title_upper = task_title.upper()
            if "PROD" in title_upper or "PRODUCTION" in title_upper:
                environment = "Production"
            elif "MAIN" in title_upper or "MASTER" in title_upper:
                environment = "Main"
            elif "DEV" in title_upper:
                environment = "Dev"
            elif "STAGE" in title_upper:
                environment = "Stage"
            else:
                environment = "Main"

        if not repo_url:
            desc = data.get("description") or ""
            match = re.search(r"(https?://[^\s]+git[^\s]*|git@[^\s]+)", desc)
            if match:
                repo_url = match.group(0).strip()

        space_info = data.get("space") or {}
        list_info = data.get("list") or {}
        status_info = data.get("status") or {}
        
        assignees_list = data.get("assignees") or []
        assignees = [a.get("username", a.get("email", "Unknown")) for a in assignees_list]

        return ClickUpTaskInfo(
            task_id=task_id,
            task_name=data.get("name", ""),
            repo_url=repo_url,
            reporter_id=reporter_id,
            reporter_username=reporter_username,
            reporter_email=reporter_email,
            status=status_info.get("status"),
            space_name=space_info.get("name"),
            list_name=list_info.get("name"),
            service_needs=service_needs,
            environment=environment,
            custom_fields=custom_field_dict,
            assignees=assignees,
            envs=envs_raw
        )

    async def get_active_deploy_tasks(self, team_id: Optional[str] = None) -> list[ClickUpTaskInfo]:
        """
        Fetches tasks from the SRE -> Form -> Deploy list directly,
        or falls back to team query if list not resolved.
        """
        target_team = team_id or settings.CLICKUP_TEAM_ID
        if not target_team:
            return []

        # 1. Try to query the exact Deploy list
        deploy_list_id = await self.find_deploy_list_id(target_team)
        if deploy_list_id:
            url = f"{self.base_url}/list/{deploy_list_id}/task"
            params = {
                "include_closed": "false",
                "order_by": "created",
                "reverse": "true",
                "subtasks": "false"
            }
            status, data = await self._http_request("GET", url, params=params)
            if status == 200 and isinstance(data, dict):
                tasks = []
                for t in data.get("tasks", []):
                    st = t.get("status", {}).get("status", "").lower()
                    if st not in ["closed", "done", "complete", "completed", "ready to test"]:
                        tasks.append(self.parse_task_data(t))
                if tasks:
                    return tasks

        # 2. Fallback to team level task query without restrictive status filter
        url = f"{self.base_url}/team/{target_team}/task"
        params = {
            "order_by": "created",
            "reverse": "true",
            "include_closed": "false"
        }

        status, data = await self._http_request("GET", url, params=params)
        if status != 200 or not isinstance(data, dict):
            return []

        tasks = []
        for t in data.get("tasks", []):
            st = t.get("status", {}).get("status", "").lower()
            if st in ["closed", "done", "complete", "completed", "ready to test"]:
                continue
            task_info = self.parse_task_data(t)
            if task_info.repo_url or (task_info.space_name and "sre" in task_info.space_name.lower()) or (task_info.list_name and "deploy" in task_info.list_name.lower()):
                tasks.append(task_info)

        return tasks

    async def get_comment_replies(self, comment_id: str) -> list[dict[str, Any]]:
        """Fetch threaded replies for a specific comment."""
        url = f"{self.base_url}/comment/{comment_id}/reply"
        status, data = await self._http_request("GET", url)
        if status != 200 or not isinstance(data, dict):
            return []
        return data.get("comments", []) or []

    async def get_task_comments(self, task_id: str, include_replies: bool = True) -> list[dict[str, Any]]:
        url = f"{self.base_url}/task/{task_id}/comment"
        status, data = await self._http_request("GET", url)
        if status != 200 or not isinstance(data, dict):
            return []
        comments = data.get("comments", []) or []
        if not include_replies or not comments:
            return comments

        # Fetch threaded replies for comments in parallel
        async def _fetch_with_replies(comment: dict[str, Any]) -> list[dict[str, Any]]:
            cid = comment.get("id")
            items = [comment]
            if cid:
                try:
                    replies = await self.get_comment_replies(cid)
                    for r in replies:
                        r["parent_id"] = cid
                        items.append(r)
                except Exception:
                    pass
            return items

        results = await asyncio.gather(*[_fetch_with_replies(c) for c in comments], return_exceptions=True)
        all_comments: list[dict[str, Any]] = []
        for res in results:
            if isinstance(res, list):
                all_comments.extend(res)

        # Sort chronologically by date
        def _get_ts(item: dict[str, Any]) -> int:
            d = item.get("date")
            try:
                return int(d) if d is not None else 0
            except (ValueError, TypeError):
                return 0

        all_comments.sort(key=_get_ts)
        return all_comments

    async def post_comment(self, task_id: str, comment_text: str, notify_all: bool = False) -> bool:
        url = f"{self.base_url}/task/{task_id}/comment"
        payload = {
            "comment_text": comment_text,
            "notify_all": notify_all
        }
        status, _ = await self._http_request("POST", url, data=payload)
        return status in [200, 201]

    async def post_maintainer_request_comment(self, task_id: str, reporter_id: Optional[int], reporter_username: Optional[str], repo_url: Optional[str] = None) -> bool:
        url = f"{self.base_url}/task/{task_id}/comment"

        # Build rich-text comment array with proper @mention tag
        comment_parts: list[dict[str, Any]] = [
            {"text": "وقت به خیر "},
        ]

        if reporter_id:
            # Proper ClickUp mention (renders as clickable @name tag)
            comment_parts.append({
                "type": "tag",
                "user": {"id": reporter_id}
            })
        elif reporter_username:
            comment_parts.append({"text": f"@{reporter_username}"})

        comment_parts.append({
            "text": "\nلطفا دسترسی Maintainer به این پروژه را روی GitLab به من بدهید 🙏"
        })

        if repo_url:
            comment_parts.append({"text": f"\n🔗 ریپو: {repo_url}"})

        payload: dict[str, Any] = {
            "comment": comment_parts,
            "notify_all": True
        }
        status, _ = await self._http_request("POST", url, data=payload)
        comment_ok = status in [200, 201]
        await self.update_task_status(task_id, settings.CLICKUP_WAITING_STATUS, reporter_id=reporter_id)
        return comment_ok

    async def post_tagged_comment(
        self,
        task_id: str,
        reporter_id: Optional[int],
        reporter_username: Optional[str],
        body_text: str,
        notify_all: bool = True
    ) -> bool:
        """
        Posts a rich-text comment to ClickUp with the reporter properly tagged at the beginning.
        """
        url = f"{self.base_url}/task/{task_id}/comment"

        comment_parts: list[dict[str, Any]] = [
            {"text": "وقت به خیر "}
        ]

        if reporter_id:
            comment_parts.append({
                "type": "tag",
                "user": {"id": int(reporter_id)}
            })
        elif reporter_username:
            comment_parts.append({"text": f"@{reporter_username}"})

        comment_parts.append({
            "text": f"\n{body_text.strip()}"
        })

        payload: dict[str, Any] = {
            "comment": comment_parts,
            "notify_all": notify_all
        }
        status, _ = await self._http_request("POST", url, data=payload)
        return status in [200, 201]

    async def get_current_user_id(self) -> Optional[int]:
        if self._cached_user_id:
            return self._cached_user_id
        url = f"{self.base_url}/user"
        status, data = await self._http_request("GET", url)
        if status == 200 and isinstance(data, dict):
            u = data.get("user") or {}
            uid = u.get("id")
            if uid:
                self._cached_user_id = int(uid)
                return self._cached_user_id
        return None

    async def clean_assignees_and_watchers(self, task_id: str, reporter_id: Optional[int] = None) -> bool:
        """
        When task moves to 'waiting for customer':
        - Keep only Me in assignees (remove all other assignees).
        - Keep only Me and Reporter in watchers (remove all other watchers).
        """
        try:
            my_user_id = await self.get_current_user_id()
            url = f"{self.base_url}/task/{task_id}"
            status, data = await self._http_request("GET", url, params={"include_subtasks": "false"})
            if status != 200 or not isinstance(data, dict):
                return False

            if reporter_id is None:
                creator = data.get("creator") or {}
                c_id = creator.get("id")
                if c_id:
                    reporter_id = int(c_id)

            # 1. Assignees cleanup: keep only Me
            assignees = data.get("assignees") or []
            assignee_ids = [int(a.get("id")) for a in assignees if a.get("id") is not None]
            rem_assignees = [uid for uid in assignee_ids if my_user_id is None or uid != my_user_id]
            add_assignees = [my_user_id] if my_user_id and my_user_id not in assignee_ids else []

            # 2. Watchers cleanup: keep only Me and reporter
            watchers = data.get("watchers") or []
            watcher_ids = [int(w.get("id")) for w in watchers if w.get("id") is not None]
            allowed_watcher_ids = set()
            if my_user_id:
                allowed_watcher_ids.add(my_user_id)
            if reporter_id:
                allowed_watcher_ids.add(int(reporter_id))

            rem_watchers = [uid for uid in watcher_ids if uid not in allowed_watcher_ids]

            update_payload: dict[str, Any] = {}
            if rem_assignees or add_assignees:
                assignee_payload: dict[str, list[int]] = {}
                if add_assignees:
                    assignee_payload["add"] = add_assignees
                if rem_assignees:
                    assignee_payload["rem"] = rem_assignees
                update_payload["assignees"] = assignee_payload

            if rem_watchers:
                update_payload["watchers"] = {
                    "rem": rem_watchers
                }

            if update_payload:
                put_status, put_resp = await self._http_request("PUT", url, data=update_payload)
                return put_status == 200

            return True
        except Exception as e:
            print(f"⚠️ Error cleaning assignees and watchers for task {task_id}: {e}")
            return False

    async def update_task_status(self, task_id: str, new_status: str = settings.CLICKUP_WAITING_STATUS, reporter_id: Optional[int] = None) -> bool:
        url = f"{self.base_url}/task/{task_id}"
        payload = {
            "status": new_status
        }
        status, _ = await self._http_request("PUT", url, data=payload)
        success = status == 200

        if success and new_status.lower() == settings.CLICKUP_WAITING_STATUS.lower():
            await self.clean_assignees_and_watchers(task_id, reporter_id=reporter_id)

        return success

    async def assign_user_to_task(self, task_id: str, user_id: int, reporter_id: Optional[int] = None) -> bool:
        """Assigns a specific user to a task, removes other assignees, and removes unwanted watchers/followers."""
        try:
            url = f"{self.base_url}/task/{task_id}"
            status, data = await self._http_request("GET", url, params={"include_subtasks": "false"})
            rem_assignees = []
            rem_watchers = []
            if status == 200 and isinstance(data, dict):
                current_assignees = data.get("assignees") or []
                rem_assignees = [int(a.get("id")) for a in current_assignees if a.get("id") is not None and int(a.get("id")) != user_id]

                if reporter_id is None:
                    creator = data.get("creator") or {}
                    c_id = creator.get("id")
                    if c_id:
                        reporter_id = int(c_id)

                watchers = data.get("watchers") or []
                watcher_ids = [int(w.get("id")) for w in watchers if w.get("id") is not None]
                allowed_watchers = {user_id}
                if reporter_id:
                    allowed_watchers.add(int(reporter_id))
                rem_watchers = [wid for wid in watcher_ids if wid not in allowed_watchers]

            payload: dict[str, Any] = {
                "assignees": {
                    "add": [user_id]
                }
            }
            if rem_assignees:
                payload["assignees"]["rem"] = rem_assignees
            if rem_watchers:
                payload["watchers"] = {"rem": rem_watchers}

            put_status, _ = await self._http_request("PUT", url, data=payload)
            return put_status == 200
        except Exception as e:
            print(f"⚠️ Error assigning user {user_id} to task {task_id}: {e}")
            return False

    async def remove_user_from_task_assignees(self, task_id: str, user_id: int) -> bool:
        """Removes a specific user from a task's assignees list via ClickUp PUT API."""
        try:
            url = f"{self.base_url}/task/{task_id}"
            payload: dict[str, Any] = {
                "assignees": {
                    "rem": [user_id]
                }
            }
            put_status, _ = await self._http_request("PUT", url, data=payload)
            return put_status == 200
        except Exception as e:
            logger.warning(f"Error removing user {user_id} from task {task_id} assignees: {e}")
            return False


    async def clean_watchers_keep_user_and_reporter(self, task_id: str, user_id: int, reporter_id: Optional[int] = None) -> bool:
        """
        When task is moved to in-progress or assigned, keep only assigned user and reporter in watchers.
        All other watchers/followers are removed.
        """
        try:
            url = f"{self.base_url}/task/{task_id}"
            status, data = await self._http_request("GET", url, params={"include_subtasks": "false"})
            if status != 200 or not isinstance(data, dict):
                return False

            if reporter_id is None:
                creator = data.get("creator") or {}
                c_id = creator.get("id")
                if c_id:
                    reporter_id = int(c_id)

            watchers = data.get("watchers") or []
            watcher_ids = [int(w.get("id")) for w in watchers if w.get("id") is not None]
            allowed = {user_id}
            if reporter_id:
                allowed.add(int(reporter_id))

            rem_watchers = [wid for wid in watcher_ids if wid not in allowed]
            if rem_watchers:
                put_status, _ = await self._http_request("PUT", url, data={"watchers": {"rem": rem_watchers}})
                return put_status == 200
            return True
        except Exception as e:
            print(f"⚠️ Error cleaning watchers for task {task_id}: {e}")
            return False

    async def find_change_list_id(self, team_id: Optional[str] = None) -> Optional[str]:
        """Locates the Change list under SRE space -> Form folder."""
        if hasattr(self, "_cached_change_list_id") and self._cached_change_list_id:
            return self._cached_change_list_id

        target_team = team_id or settings.CLICKUP_TEAM_ID
        if not target_team:
            return None

        status, spaces_data = await self._http_request("GET", f"{self.base_url}/team/{target_team}/space")
        if status != 200 or not isinstance(spaces_data, dict):
            return None

        target_space_name = settings.CLICKUP_SRE_SPACE_NAME.lower()

        for sp in spaces_data.get("spaces", []):
            sp_name = sp.get("name", "").lower()
            sp_id = sp.get("id")

            if target_space_name in sp_name or "sre" in sp_name:
                _, folders_data = await self._http_request("GET", f"{self.base_url}/space/{sp_id}/folder")
                if isinstance(folders_data, dict):
                    for fld in folders_data.get("folders", []):
                        fld_name = fld.get("name", "").lower()
                        is_form_folder = "form" in fld_name or "فرم" in fld_name
                        for lst in fld.get("lists", []):
                            lst_name = lst.get("name", "").lower()
                            if is_form_folder and ("change" in lst_name or "چنج" in lst_name):
                                self._cached_change_list_id = str(lst.get("id"))
                                return self._cached_change_list_id
                            elif lst_name in ["change", "چنج"]:
                                self._cached_change_list_id = str(lst.get("id"))
                                return self._cached_change_list_id

                _, lists_data = await self._http_request("GET", f"{self.base_url}/space/{sp_id}/list")
                if isinstance(lists_data, dict):
                    for lst in lists_data.get("lists", []):
                        lst_name = lst.get("name", "").lower()
                        if lst_name in ["change", "چنج"]:
                            self._cached_change_list_id = str(lst.get("id"))
                            return self._cached_change_list_id

        return None

    async def get_active_change_tasks(self, team_id: Optional[str] = None) -> list[ClickUpTaskInfo]:
        """Fetches tasks from the SRE -> Form -> Change list."""
        target_team = team_id or settings.CLICKUP_TEAM_ID
        if not target_team:
            return []

        change_list_id = await self.find_change_list_id(target_team)
        if change_list_id:
            url = f"{self.base_url}/list/{change_list_id}/task"
            params = {
                "include_closed": "false",
                "order_by": "created",
                "reverse": "true",
                "subtasks": "false"
            }
            status, data = await self._http_request("GET", url, params=params)
            if status == 200 and isinstance(data, dict):
                tasks = []
                for t in data.get("tasks", []):
                    st = t.get("status", {}).get("status", "").lower()
                    if st not in ["closed", "done", "complete", "completed", "ready to test"]:
                        tasks.append(self.parse_task_data(t))
                return tasks

        return []

    async def find_user_by_name(self, name: str, team_id: Optional[str] = None) -> Optional[dict]:
        """Find a workspace member by name (case-insensitive partial match)."""
        target_team = team_id or settings.CLICKUP_TEAM_ID
        if not target_team:
            return None

        status, data = await self._http_request("GET", f"{self.base_url}/team/{target_team}/member")
        if status != 200 or not isinstance(data, dict):
            return None

        search = name.lower()
        for member in data.get("members", []):
            u = member.get("user", member)
            full_name = (u.get("username", "") or "").lower()
            email = (u.get("email", "") or "").lower()
            if search in full_name or search in email:
                return {"id": u.get("id"), "username": u.get("username"), "email": u.get("email")}

        return None

    async def create_subtask(
        self,
        parent_task_id: str,
        name: str,
        assignee_ids: Optional[list[int]] = None,
        comment_text: Optional[str] = None,
        list_id: Optional[str] = None,
    ) -> Optional[str]:
        """Create a subtask under the given parent task. Returns the new subtask ID."""
        # Determine list: use parent task's list if not provided
        if not list_id:
            status, parent_data = await self._http_request(
                "GET", f"{self.base_url}/task/{parent_task_id}",
                params={"include_subtasks": "false"}
            )
            if status == 200 and isinstance(parent_data, dict):
                list_id = str((parent_data.get("list") or {}).get("id", ""))

        if not list_id:
            return None

        payload: dict[str, Any] = {
            "name": name,
            "parent": parent_task_id,
        }
        if assignee_ids:
            payload["assignees"] = assignee_ids

        status, data = await self._http_request("POST", f"{self.base_url}/list/{list_id}/task", data=payload)
        if status not in [200, 201] or not isinstance(data, dict):
            return None

        subtask_id = data.get("id")

        if subtask_id and comment_text:
            await self.post_comment(subtask_id, comment_text)

        return subtask_id

    def is_recheck_comment(self, comment_text: str) -> bool:
        normalized = comment_text.lower().strip()
        for kw in settings.RECHECK_KEYWORDS:
            if kw.lower() in normalized:
                return True
        return False

    async def get_current_user_profile(self) -> Optional[dict[str, Any]]:
        """Returns the authenticated ClickUp user profile information."""
        url = f"{self.base_url}/user"
        status, data = await self._http_request("GET", url)
        if status == 200 and isinstance(data, dict):
            u = data.get("user") or {}
            return {
                "id": u.get("id"),
                "username": u.get("username", "کاربر"),
                "email": u.get("email", ""),
                "color": u.get("color", "#3b82f6"),
                "profile_picture": u.get("profilePicture"),
                "initials": u.get("initials") or (u.get("username", "U")[:2].upper() if u.get("username") else "U")
            }
        return None

    def format_task_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Serializes raw ClickUp task data into a clean structure for the frontend."""
        task_id = str(data.get("id", ""))
        name = data.get("name", "")
        desc = data.get("description", "") or data.get("text_content", "") or ""
        status_info = data.get("status") or {}
        status_name = status_info.get("status", "open")
        status_color = status_info.get("color", "#87909e")
        priority_info = data.get("priority") or {}
        priority_label = priority_info.get("priority") if isinstance(priority_info, dict) else None
        priority_color = priority_info.get("color") if isinstance(priority_info, dict) else None

        list_info = data.get("list") or {}
        folder_info = data.get("folder") or {}
        space_info = data.get("space") or {}
        list_name = list_info.get("name", "")
        folder_name = folder_info.get("name", "")
        space_name = space_info.get("name", "")

        # Form name corresponds to the specific list under Form folder (e.g. Deploy, Issue, Change, Access, etc.)
        form_name = list_name if list_name else folder_name

        assignees_raw = data.get("assignees") or []
        assignees = []
        for a in assignees_raw:
            assignees.append({
                "id": a.get("id"),
                "username": a.get("username", "Unknown"),
                "email": a.get("email", ""),
                "color": a.get("color", "#6366f1"),
                "initials": a.get("initials") or (a.get("username", "U")[:2].upper() if a.get("username") else "U"),
                "profile_picture": a.get("profilePicture")
            })

        creator = data.get("creator") or {}
        creator_username = creator.get("username", "Unknown")
        creator_info = {
            "id": creator.get("id"),
            "username": creator_username,
            "email": creator.get("email", ""),
            "color": creator.get("color", "#6366f1"),
            "initials": creator.get("initials") or (creator_username[:2].upper() if creator_username else "U"),
            "profile_picture": creator.get("profilePicture")
        }

        # Parse custom fields
        custom_fields_raw = data.get("custom_fields") or []
        parsed_custom_fields = []
        repo_url = None
        environment = None
        service_needs = []

        for cf in custom_fields_raw:
            cf_name = cf.get("name", "")
            cf_val = cf.get("value")
            cf_type = cf.get("type", "")
            type_config = cf.get("type_config") or {}

            display_val = None
            if cf_val is not None:
                if cf_type == "drop_down" and isinstance(type_config.get("options"), list):
                    options = type_config.get("options", [])
                    if isinstance(cf_val, int):
                        for opt in options:
                            if opt.get("orderindex") == cf_val:
                                display_val = opt.get("name", opt.get("label", ""))
                                break
                        if not display_val and cf_val < len(options):
                            display_val = options[cf_val].get("name", "")
                    elif isinstance(cf_val, str):
                        for opt in options:
                            if opt.get("id") == cf_val:
                                display_val = opt.get("name", opt.get("label", ""))
                                break
                    if not display_val:
                        display_val = str(cf_val)
                elif cf_type == "labels" and isinstance(cf_val, list):
                    options = type_config.get("options", [])
                    opt_map = {opt.get("id"): opt.get("label", opt.get("name", "")) for opt in options if "id" in opt}
                    display_val = [opt_map.get(v, v) for v in cf_val]
                elif cf_type == "users" and isinstance(cf_val, list):
                    user_names = [u.get("username") for u in cf_val if isinstance(u, dict) and u.get("username")]
                    display_val = "، ".join(user_names) if user_names else "کاربر"
                elif cf_name.strip().lower() == "task age" or (cf_type == "formula" and "age" in cf_name.lower()):
                    # ClickUp's formula field DAYS(TODAY(), TASK_CREATED) often returns inverted or buggy negative values.
                    # Compute real task age accurately from task date_created
                    cr_ts = data.get("date_created")
                    if cr_ts:
                        try:
                            import time
                            age_days = int((time.time() - (int(cr_ts) / 1000.0)) / 86400.0)
                            display_val = f"{age_days} روز"
                        except Exception:
                            display_val = str(cf_val)
                    else:
                        display_val = str(cf_val)
                elif isinstance(cf_val, dict) and "url" in cf_val:
                    display_val = cf_val["url"]
                else:
                    display_val = cf_val

                cf_lower = cf_name.lower()
                if ("repo" in cf_lower or "ریپو" in cf_lower or "gitlab" in cf_lower) and not repo_url:
                    if isinstance(display_val, str) and display_val.strip():
                        repo_url = display_val.strip()

                if ("env" in cf_lower or "محیط" in cf_lower) and not environment:
                    if isinstance(display_val, str) and display_val.strip():
                        environment = display_val.strip()

                if "service" in cf_lower or "نیاز" in cf_lower:
                    if isinstance(display_val, list):
                        service_needs = display_val
                    elif isinstance(display_val, str):
                        service_needs = [display_val]

            parsed_custom_fields.append({
                "id": cf.get("id"),
                "name": cf_name,
                "type": cf_type,
                "value": cf_val,
                "display_value": display_val
            })

        if not repo_url and desc:
            match = re.search(r"(https?://[^\s]+git[^\s]*|git@[^\s]+)", desc)
            if match:
                repo_url = match.group(0).strip()

        return {
            "id": task_id,
            "name": name,
            "description": desc,
            "status": status_name,
            "status_color": status_color,
            "priority": priority_label,
            "priority_color": priority_color,
            "form_name": form_name,
            "list_name": list_name,
            "folder_name": folder_name,
            "space_name": space_name,
            "assignees": assignees,
            "creator": creator_info,
            "date_created": data.get("date_created"),
            "date_updated": data.get("date_updated"),
            "date_closed": data.get("date_closed"),
            "date_done": data.get("date_done"),
            "due_date": data.get("due_date"),
            "url": data.get("url") or f"https://app.clickup.com/t/{task_id}",
            "repo_url": repo_url,
            "environment": environment,
            "service_needs": service_needs,
            "custom_fields": parsed_custom_fields
        }

    async def get_user_assigned_tasks(self, user_id: Optional[int] = None) -> list[dict[str, Any]]:
        """Fetches all open tasks assigned to the current/given user."""
        target_uid = user_id or await self.get_current_user_id()
        target_team = settings.CLICKUP_TEAM_ID
        if not target_team or not target_uid:
            return []

        url = f"{self.base_url}/team/{target_team}/task"
        params = {
            "assignees[]": str(target_uid),
            "include_closed": "false",
            "subtasks": "true",
            "order_by": "updated",
            "reverse": "true"
        }
        status, res = await self._http_request("GET", url, params=params)
        if status != 200 or not isinstance(res, dict):
            return []

        tasks = []
        excluded_statuses = {"closed", "done", "complete", "completed", "ready to test"}
        for raw in res.get("tasks", []):
            st = (raw.get("status", {}).get("status") or "").lower().strip()
            if st in excluded_statuses:
                continue
            tasks.append(self.format_task_dict(raw))

        return tasks

    async def get_sre_form_tasks(
        self,
        team_id: Optional[str] = None,
        include_closed: bool = False,
        updated_gt: Optional[int] = None
    ) -> list[dict[str, Any]]:
        """
        Fetches tasks from the SRE space Form folder / form response lists.
        If include_closed is True, closed/done tasks are included.
        """
        target_team = team_id or settings.CLICKUP_TEAM_ID
        if not target_team:
            return []

        # 1. Locate SRE space
        sre_space_id = None
        status, spaces_data = await self._http_request("GET", f"{self.base_url}/team/{target_team}/space")
        if status == 200 and isinstance(spaces_data, dict):
            for sp in spaces_data.get("spaces", []):
                sp_name = sp.get("name", "").lower()
                if "sre" in sp_name:
                    sre_space_id = str(sp.get("id"))
                    break

        if not sre_space_id:
            return []

        # 2. Locate form lists in SRE space
        form_list_ids = []
        _, folders_data = await self._http_request("GET", f"{self.base_url}/space/{sre_space_id}/folder")
        if isinstance(folders_data, dict):
            for fld in folders_data.get("folders", []):
                fld_name = fld.get("name", "").lower()
                is_form_folder = "form" in fld_name or "فرم" in fld_name
                for lst in fld.get("lists", []):
                    lst_name = lst.get("name", "").lower()
                    if is_form_folder or "form" in lst_name or "deploy" in lst_name or "issue" in lst_name:
                        form_list_ids.append(str(lst.get("id")))

        _, lists_data = await self._http_request("GET", f"{self.base_url}/space/{sre_space_id}/list")
        if isinstance(lists_data, dict):
            for lst in lists_data.get("lists", []):
                lst_name = lst.get("name", "").lower()
                if "form" in lst_name or "deploy" in lst_name or "issue" in lst_name:
                    form_list_ids.append(str(lst.get("id")))

        all_tasks = []
        seen_ids = set()
        excluded_statuses = set() if include_closed else {"closed", "done", "complete", "completed", "ready to test"}

        async def fetch_list_tasks(lid: str):
            url = f"{self.base_url}/list/{lid}/task"
            params: dict[str, Any] = {
                "include_closed": "true" if include_closed else "false",
                "subtasks": "true",
                "order_by": "updated" if include_closed else "created",
                "reverse": "false" if include_closed else "true"
            }
            if updated_gt is not None:
                params["date_updated_gt"] = str(updated_gt)
            s, d = await self._http_request("GET", url, params=params)
            if s == 200 and isinstance(d, dict):
                return d.get("tasks", [])
            return []

        if form_list_ids:
            tasks_results = await asyncio.gather(*(fetch_list_tasks(lid) for lid in form_list_ids))
            for raw_list in tasks_results:
                for raw in raw_list:
                    tid = raw.get("id")
                    if tid and tid not in seen_ids:
                        seen_ids.add(tid)
                        st = (raw.get("status", {}).get("status") or "").lower().strip()
                        if st not in excluded_statuses:
                            all_tasks.append(self.format_task_dict(raw))

        return all_tasks

    async def get_task_details(self, task_id: str) -> Optional[dict[str, Any]]:
        """Returns full task details along with comments formatted for modal view."""
        status, data = await self._http_request("GET", f"{self.base_url}/task/{task_id}", params={"include_subtasks": "true"})
        if status != 200 or not isinstance(data, dict):
            return None

        formatted = self.format_task_dict(data)

        # Fetch comments
        comments_raw = await self.get_task_comments(task_id)
        formatted_comments = []
        for c in comments_raw:
            u = c.get("user") or {}
            formatted_comments.append({
                "id": c.get("id"),
                "parent_id": c.get("parent_id"),
                "text": c.get("comment_text", ""),
                "date": c.get("date"),
                "user": {
                    "id": u.get("id"),
                    "username": u.get("username", "Unknown"),
                    "email": u.get("email", ""),
                    "initials": u.get("initials") or (u.get("username", "U")[:2].upper() if u.get("username") else "U"),
                    "profile_picture": u.get("profilePicture")
                }
            })

        formatted["comments"] = formatted_comments
        formatted["comments_count"] = len(formatted_comments)
        valid_dates = [int(c["date"]) for c in formatted_comments if c.get("date")]
        if valid_dates:
            formatted["latest_comment_date"] = max(valid_dates)
        return formatted

    async def get_sre_analytics_data(self, team_id: Optional[str] = None) -> dict[str, Any]:
        """
        Collects and computes comprehensive analytics metrics across SRE tasks:
        1. Assignee distribution (donut chart)
        2. Average days tasks stayed in "New" state grouped by form (line/bar chart)
        3. Total task count per state (line/area chart)
        4. Tasks completed / ready to test in past 7 days day-by-day (bar/line chart)
        5. Team activity heatmap across dates (GitLab contribution style, filterable by member)
        """
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        now_ts = now.timestamp()
        target_team = team_id or settings.CLICKUP_TEAM_ID
        if not target_team:
            return {}

        # 1. Locate SRE Space
        sre_space_id = None
        status, spaces_data = await self._http_request("GET", f"{self.base_url}/team/{target_team}/space")
        if status == 200 and isinstance(spaces_data, dict):
            for sp in spaces_data.get("spaces", []):
                if "sre" in sp.get("name", "").lower():
                    sre_space_id = str(sp.get("id"))
                    break

        # 2. Fetch Open Form Tasks (standard SRE form lists)
        open_tasks = await self.get_sre_form_tasks(team_id=target_team)

        # Dynamic target team members from settings / environment
        configured_members = []
        try:
            if settings.SRE_TEAM_MEMBERS:
                configured_members = json.loads(settings.SRE_TEAM_MEMBERS)
        except Exception:
            configured_members = []

        TARGET_MEMBERS = [
            (m.get("name") or "").lower().strip()
            for m in configured_members
            if m.get("name")
        ]
        TARGET_MEMBER_IDS = [
            str(m.get("clickup_id"))
            for m in configured_members
            if m.get("clickup_id")
        ]

        def normalize_name(name: str) -> str:
            n = (name or "").lower().strip()
            return n

        def is_target_member(name: str) -> bool:
            norm = normalize_name(name)
            for tm in TARGET_MEMBERS:
                tm_norm = normalize_name(tm)
                if tm_norm in norm or norm in tm_norm:
                    return True
            return False

        # 3. Concurrently fetch closed & updated tasks for each target member
        # (Order by updated descending to immediately capture recently closed/moved tasks)
        one_year_ago_ms = (now_ts - 375 * 86400.0) * 1000.0

        async def fetch_member_history(uid: str):
            member_tasks = []
            for page in range(8):
                p = {
                    "assignees[]": [uid],
                    "include_closed": "true",
                    "subtasks": "true",
                    "order_by": "updated",
                    "reverse": "false",
                    "page": page
                }
                u = f"{self.base_url}/team/{target_team}/task"
                st_code, d = await self._http_request("GET", u, params=p)
                if st_code == 200 and isinstance(d, dict):
                    batch = d.get("tasks", [])
                    if not batch:
                        break
                    member_tasks.extend(batch)
                    oldest_up = int(batch[-1].get("date_updated") or batch[-1].get("date_created") or 0)
                    if oldest_up and oldest_up < one_year_ago_ms:
                        break
                else:
                    break
            return member_tasks

        # Also fetch recently closed tasks from all SRE form lists (covers unassigned closed tasks as well)
        fourteen_days_ago_ms = int((now_ts - 14 * 86400.0) * 1000.0)

        results = await asyncio.gather(
            *(fetch_member_history(uid) for uid in TARGET_MEMBER_IDS),
            self.get_sre_form_tasks(team_id=target_team, include_closed=True, updated_gt=fourteen_days_ago_ms),
            return_exceptions=True
        )

        all_unique_tasks = {str(t["id"]): t for t in open_tasks}
        for res_list in results:
            if isinstance(res_list, list):
                for rt in res_list:
                    tid = str(rt.get("id"))
                    if tid not in all_unique_tasks:
                        # If rt is already formatted (from get_sre_form_tasks) or raw dict
                        if "custom_fields" in rt and "status_color" in rt:
                            all_unique_tasks[tid] = rt
                        else:
                            all_unique_tasks[tid] = self.format_task_dict(rt)

        # =====================================================================
        # Metric 1: Donut Chart - Assignee Distribution (Open Tasks)
        # Filtered to target team members (+ unassigned if any)
        # =====================================================================
        assignee_counts: dict[str, dict[str, Any]] = {}
        for t in open_tasks:
            assignees = t.get("assignees") or []
            if not assignees:
                key = "unassigned"
                if key not in assignee_counts:
                    assignee_counts[key] = {"id": None, "name": "بدون مسئول", "count": 0, "color": "#64748b"}
                assignee_counts[key]["count"] += 1
            else:
                for a in assignees:
                    uname = a.get("username", "ناشناس")
                    if not is_target_member(uname):
                        continue
                    uid = str(a.get("id") or a.get("username"))
                    ucolor = a.get("color") or "#3b82f6"
                    if uid not in assignee_counts:
                        assignee_counts[uid] = {"id": a.get("id"), "name": uname, "count": 0, "color": ucolor}
                    assignee_counts[uid]["count"] += 1

        assignee_donut = sorted(assignee_counts.values(), key=lambda x: x["count"], reverse=True)

        # =====================================================================
        # Metric 2: Average Days in "New" State by Form
        # =====================================================================
        # =====================================================================
        # Metric 2: Average Days in "New" State by Form (with Time Range Filter)
        # Ranges: '1w' (7d), '1m' (30d), '3m' (90d), '6m' (180d), '1y' (365d), 'all'
        # =====================================================================
        # Each entry in form_new_raw: { form_name, days, cr_sec }
        form_new_raw_items = []
        for t in all_unique_tasks.values():
            st = (t.get("status") or "").lower().strip()
            form_name = t.get("form_name") or t.get("list_name") or "عمومی"
            fn_lower = form_name.lower()
            if "merchant multi deploy" in fn_lower or "it equipment" in fn_lower:
                continue

            cr_ts = t.get("date_created")
            up_ts = t.get("date_updated")
            if cr_ts:
                try:
                    cr_sec = int(cr_ts) / 1000.0
                    if st == "new":
                        days = (now_ts - cr_sec) / 86400.0
                    elif up_ts:
                        up_sec = int(up_ts) / 1000.0
                        days = max(0.1, (up_sec - cr_sec) / 86400.0)
                    else:
                        days = 0.5
                    
                    form_new_raw_items.append({
                        "form_name": form_name,
                        "days": days,
                        "cr_sec": cr_sec
                    })
                except Exception:
                    pass

        # Helper to compute averages for a given cutoff in seconds
        def compute_avg_for_cutoff(max_age_days: Optional[float] = None):
            cutoff_sec = (now_ts - max_age_days * 86400.0) if max_age_days is not None else 0
            grouped: dict[str, list[float]] = {}
            for item in form_new_raw_items:
                if item["cr_sec"] >= cutoff_sec:
                    fname = item["form_name"]
                    if fname not in grouped:
                        grouped[fname] = []
                    grouped[fname].append(item["days"])
            
            res = []
            for fname, dlist in grouped.items():
                if dlist:
                    res.append({
                        "form_name": fname,
                        "avg_days": round(sum(dlist) / len(dlist), 1),
                        "task_count": len(dlist)
                    })
            res.sort(key=lambda x: x["avg_days"], reverse=True)
            return res

        avg_days_new_by_form = compute_avg_for_cutoff(None) # default 'all'
        avg_days_ranges = {
            "1w": compute_avg_for_cutoff(7),
            "1m": compute_avg_for_cutoff(30),
            "3m": compute_avg_for_cutoff(90),
            "6m": compute_avg_for_cutoff(180),
            "1y": compute_avg_for_cutoff(365),
            "all": avg_days_new_by_form
        }

        # =====================================================================
        # Metric 3: Total Task Count per State (Bar Chart with Form Filter)
        # =====================================================================
        state_distribution: dict[str, dict[str, Any]] = {}
        # Also compute state distribution by form: form_name -> state -> count
        state_by_form: dict[str, dict[str, dict[str, Any]]] = {}
        # Excluded redundant/unwanted states as requested: 'prioritize', 'wait for customer'
        EXCLUDED_STATES = {"prioritize", "wait for customer"}
        all_forms_set = set()

        for t in open_tasks:
            st = (t.get("status") or "نامشخص").strip()
            if st.lower() in EXCLUDED_STATES:
                continue
            color = t.get("status_color") or "#8b5cf6"
            form_name = t.get("form_name") or t.get("list_name") or "عمومی"
            all_forms_set.add(form_name)

            # Overall
            if st not in state_distribution:
                state_distribution[st] = {"state": st, "count": 0, "color": color}
            state_distribution[st]["count"] += 1

            # Per form
            if form_name not in state_by_form:
                state_by_form[form_name] = {}
            if st not in state_by_form[form_name]:
                state_by_form[form_name][st] = {"state": st, "count": 0, "color": color}
            state_by_form[form_name][st]["count"] += 1

        state_counts_list = sorted(state_distribution.values(), key=lambda x: x["count"], reverse=True)
        state_counts_by_form_formatted = {}
        for fn, sdict in state_by_form.items():
            state_counts_by_form_formatted[fn] = sorted(sdict.values(), key=lambda x: x["count"], reverse=True)


        # =====================================================================
        # Metric 4: Tasks Closed / Ready to Test in Past 7 Days (Daily)
        # =====================================================================
        today_date = now.date()
        fa_day_abbrs = {5: "شنبه", 6: "یکشنبه", 0: "دوشنبه", 1: "سه‌شنبه", 2: "چهارشنبه", 3: "پنج‌شنبه", 4: "جمعه"}
        past_7_days_map = {}
        day_labels = []
        for i in range(6, -1, -1):
            day_dt = (now - datetime.timedelta(days=i)).date()
            day_str = day_dt.strftime("%Y-%m-%d")
            # Format friendly day name with Persian day abbreviation
            fa_d_name = fa_day_abbrs.get(day_dt.weekday(), day_dt.strftime("%a"))
            f_name = f"{fa_d_name} {day_dt.strftime('%m/%d')}"
            past_7_days_map[day_str] = {
                "date": day_str,
                "label": f_name,
                "is_today": (day_dt == today_date),
                "closed": 0,
                "ready_to_test": 0,
                "total": 0
            }
            day_labels.append(day_str)

        for t in all_unique_tasks.values():
            st = (t.get("status") or "").lower().strip()
            is_closed = st in ["closed", "done", "complete", "completed"]
            is_ready_to_test = "ready to test" in st
            if not is_closed and not is_ready_to_test:
                continue

            # For closed tasks, prioritize date_closed or date_done, fallback to date_updated
            if is_closed:
                action_ms = t.get("date_closed") or t.get("date_done") or t.get("date_updated") or t.get("date_created")
            else:
                action_ms = t.get("date_done") or t.get("date_updated") or t.get("date_created")

            if not action_ms:
                continue
            try:
                task_dt = datetime.datetime.fromtimestamp(int(action_ms) / 1000.0, tz=datetime.timezone.utc).date()
                d_key = task_dt.strftime("%Y-%m-%d")
                if d_key in past_7_days_map:
                    if is_closed:
                        past_7_days_map[d_key]["closed"] += 1
                        past_7_days_map[d_key]["total"] += 1
                    elif is_ready_to_test:
                        past_7_days_map[d_key]["ready_to_test"] += 1
                        past_7_days_map[d_key]["total"] += 1
            except Exception:
                pass

        weekly_throughput = [past_7_days_map[k] for k in day_labels]

        # =====================================================================
        # Metric 5: Team Activity Heatmap (GitLab / Git style matrix)
        # Saturday to Friday aligned weeks (53 full weeks = 371 days)
        # Row 0: Saturday (شنبه) ... Row 6: Friday (جمعه)
        # Filtered to target team members
        # =====================================================================
        heatmap_days = {}
        team_members_catalog: dict[str, str] = {}  # id -> username

        # Find the upcoming or current Friday to close the full week
        # In Python weekday(): Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4, Saturday=5, Sunday=6
        # Days until Friday: (4 - today_date.weekday()) % 7
        days_to_friday = (4 - today_date.weekday()) % 7
        end_friday = today_date + datetime.timedelta(days=days_to_friday)
        # 53 weeks = 53 * 7 = 371 days ending on end_friday
        start_saturday = end_friday - datetime.timedelta(days=371 - 1)

        curr_d = start_saturday
        while curr_d <= end_friday:
            d_str = curr_d.strftime("%Y-%m-%d")
            # Saturday-based index: Saturday=0, Sunday=1, ..., Friday=6
            sat_weekday = (curr_d.weekday() + 2) % 7
            heatmap_days[d_str] = {
                "date": d_str,
                "day_of_week": sat_weekday,  # 0=Saturday (شنبه), 6=Friday (جمعه)
                "is_today": (curr_d == today_date),
                "is_future": curr_d > today_date,
                "total_activity": 0,
                "user_activity": {}
            }
            curr_d += datetime.timedelta(days=1)

        for t in all_unique_tasks.values():
            # Check assignees and creator
            assignees = t.get("assignees") or []
            creator = t.get("creator") or {}
            
            # Match target members involved in this task
            involved_members = {}
            for a in assignees:
                uname = a.get("username", "Unknown")
                if is_target_member(uname):
                    involved_members[str(a.get("id"))] = uname
                    team_members_catalog[str(a.get("id"))] = uname

            c_uname = creator.get("username", "")
            if is_target_member(c_uname):
                involved_members[str(creator.get("id"))] = c_uname
                team_members_catalog[str(creator.get("id"))] = c_uname

            # Collect all distinct activity timestamps on this task:
            # date_updated, date_created, date_closed, date_done
            timestamps = set()
            for key in ["date_updated", "date_created", "date_closed", "date_done"]:
                v = t.get(key)
                if v:
                    try:
                        t_sec = int(v) / 1000.0
                        d_str = datetime.datetime.fromtimestamp(t_sec, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
                        timestamps.add(d_str)
                    except Exception:
                        pass

            for d_str in timestamps:
                if d_str in heatmap_days and not heatmap_days[d_str].get("is_future"):
                    if involved_members or not assignees:
                        heatmap_days[d_str]["total_activity"] += 1
                        for uid in involved_members.keys():
                            u_map = heatmap_days[d_str]["user_activity"]
                            u_map[uid] = u_map.get(uid, 0) + 1

        activity_matrix = list(heatmap_days.values())
        members_list = [{"id": uid, "name": uname} for uid, uname in team_members_catalog.items()]
        members_list.sort(key=lambda x: x["name"])

        # =====================================================================
        # Metric 6: Average In-Progress Duration by Form (Closed & Ready For Test Tasks)
        # Proxy: (date_closed or date_updated) - date_created, in days
        # Supports time-range filtering by closure date (cl_sec)
        # =====================================================================
        CLOSED_STATUSES = {"closed", "done", "complete", "completed"}
        inprogress_raw_items = []
        for t in all_unique_tasks.values():
            st = (t.get("status") or "").lower().strip()
            is_closed_or_tested = (st in CLOSED_STATUSES) or ("ready" in st and "test" in st)
            if not is_closed_or_tested:
                continue
            form_name = t.get("form_name") or t.get("list_name") or "عمومی"
            fn_lower = form_name.lower()
            if "merchant multi deploy" in fn_lower or "it equipment" in fn_lower:
                continue
            cr_ts = t.get("date_created")
            # Prefer a true closure timestamp. For tasks that are only in "Ready for Test" we may have a `date_done`
            # field representing when they reached that state. Use it if available; otherwise fall back to `date_updated`.
            if "ready" in (t.get("status") or "").lower() and "test" in (t.get("status") or "").lower():
                cl_ts = t.get("date_done") or t.get("date_updated")
            else:
                cl_ts = t.get("date_closed") or t.get("date_updated")
            if not cr_ts or not cl_ts:
                continue
            try:
                cr_sec = int(cr_ts) / 1000.0
                cl_sec = int(cl_ts) / 1000.0
                duration_days = (cl_sec - cr_sec) / 86400.0
                if duration_days < 0:
                    continue
                inprogress_raw_items.append({
                    "form_name": form_name,
                    "duration_days": duration_days,
                    "cl_sec": cl_sec
                })
            except Exception:
                pass

        def compute_avg_inprogress_for_cutoff(max_age_days: Optional[float] = None):
            cutoff_sec = (now_ts - max_age_days * 86400.0) if max_age_days is not None else 0
            grouped: dict[str, list[float]] = {}
            for item in inprogress_raw_items:
                if item["cl_sec"] >= cutoff_sec:
                    fname = item["form_name"]
                    if fname not in grouped:
                        grouped[fname] = []
                    grouped[fname].append(item["duration_days"])
            res = []
            for fname, dlist in grouped.items():
                if dlist:
                    res.append({
                        "form_name": fname,
                        "avg_days": round(sum(dlist) / len(dlist), 1),
                        "task_count": len(dlist)
                    })
            res.sort(key=lambda x: x["avg_days"], reverse=True)
            return res

        avg_days_in_progress_by_form = compute_avg_inprogress_for_cutoff(None)
        avg_inprogress_ranges = {
            "1w": compute_avg_inprogress_for_cutoff(7),
            "1m": compute_avg_inprogress_for_cutoff(30),
            "3m": compute_avg_inprogress_for_cutoff(90),
            "6m": compute_avg_inprogress_for_cutoff(180),
            "1y": compute_avg_inprogress_for_cutoff(365),
            "all": avg_days_in_progress_by_form
        }

        return {
            "assignee_donut": assignee_donut,
            "avg_days_new_by_form": avg_days_new_by_form,
            "avg_days_ranges": avg_days_ranges,
            "avg_days_in_progress_by_form": avg_days_in_progress_by_form,
            "avg_inprogress_ranges": avg_inprogress_ranges,
            "state_counts": state_counts_list,
            "state_counts_by_form": state_counts_by_form_formatted,
            "sre_forms": sorted(list(all_forms_set)),
            "weekly_throughput": weekly_throughput,
            "activity_heatmap": {
                "days": activity_matrix,
                "members": members_list
            },
            "generated_at": now_ts
        }


