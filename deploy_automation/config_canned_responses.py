from typing import NamedTuple

class CannedResponse(NamedTuple):
    key: str
    title: str
    text: str

# لیست متن‌های پیش‌فرض برای بستن یا رد تسک
# در این بخش می‌توانید به سادگی متن‌های پیش‌فرض جدید برای پیشنهاد به کاربر اضافه کنید:
CANNED_CLOSE_RESPONSES: list[CannedResponse] = [
    CannedResponse(
        key="main_prod_simultaneous",
        title="تسک main و پروداکشن همزمان",
        text="تسک main و پروداکشن همزمان ایجاد شده است، دیپلوی پروداکشن بسته میشود."
    ),
    CannedResponse(
        key="duplicate_task",
        title="تسک تکراری",
        text="این تسک مشابه تسک دیگری ثبت شده و به دلیل تکراری بودن بسته می‌شود."
    ),
    CannedResponse(
        key="incomplete_info",
        title="اطلاعات ناقص",
        text="اطلاعات لازم ارائه نشده است. لطفاً پس از تکمیل فرم، تسک مجدداً باز شود."
    )
]

def get_canned_response_by_key(key: str) -> str:
    for item in CANNED_CLOSE_RESPONSES:
        if item.key == key:
            return item.text
    return ""
