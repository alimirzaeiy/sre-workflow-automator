#!/usr/bin/env python3
"""
ابزار کمکی استعلام و مشاهده شناسه‌های کلیک‌آپ اعضای تیم
Usage:
    python list_clickup_members.py
    python list_clickup_members.py "Ali"
"""
import sys
import asyncio
from deploy_automation.integrations.clickup_service import ClickUpService
from deploy_automation.config import settings

async def main():
    service = ClickUpService()
    team_id = settings.CLICKUP_TEAM_ID
    if not team_id:
        print("❌ CLICKUP_TEAM_ID در فایل .env تعریف نشده است.")
        return

    filter_query = sys.argv[1].lower() if len(sys.argv) > 1 else ""

    status, data = await service._http_request("GET", f"{service.base_url}/team/{team_id}")
    if status != 200 or not isinstance(data, dict):
        print(f"❌ خطا در دریافت اطلاعات از کلیک‌آپ (کد {status}): {data}")
        return

    team = data.get("team", {})
    members = team.get("members", [])
    workspace_name = team.get("name", "Workspace")

    print(f"\n👥 اعضای ورک‌اسپیس: {workspace_name} (تعداد کل: {len(members)})")
    print("=" * 80)
    print(f"{'ClickUp ID':<15} | {'نام (Username)':<30} | {'ایمیل'}")
    print("-" * 80)

    matched = 0
    for m in members:
        u = m.get("user", m)
        uid = str(u.get("id", ""))
        uname = str(u.get("username", ""))
        email = str(u.get("email", ""))

        if filter_query:
            if filter_query not in uname.lower() and filter_query not in email.lower() and filter_query not in uid:
                continue

        print(f"{uid:<15} | {uname:<30} | {email}")
        matched += 1

    print("=" * 80)
    if filter_query:
        print(f"🔍 تعداد نتایج فیلتر شده برای '{filter_query}': {matched}")
    else:
        print(f"💡 نکته: برای جستجوی یک عضو خاص می‌توانید دستور را به شکل زیر اجرا کنید:")
        print(f"   python list_clickup_members.py <نام یا ایمیل>")

if __name__ == "__main__":
    asyncio.run(main())
