# deploy_automation/utils — Utility Helpers

> [!IMPORTANT]
> **Live Graph Rule**: If you read or edit `text_helpers.py`, update this `GEMINI.md` in the same commit if a new formatting function was added or a function signature changed.

## Purpose

Pure formatting utilities. No external calls, no state. All functions take plain Python objects and return formatted strings.

## Files

### `text_helpers.py`

#### Functions

```python
def format_telegram_review_message(report: ProjectReport) -> str:
    """
    Formats the interactive Telegram message sent to admin after running checks.
    Output includes: project name, repo URL, check results summary, 
    proposed ClickUp comment text, and a confirmation question.
    Uses HTML parse_mode (bold tags, code blocks).
    """

def format_clickup_final_comment(
    comment_body: str,
    reporter_username: Optional[str] = None
) -> str:
    """
    Wraps comment body with standard Persian greeting and intro line.
    Adds @mention to reporter if username provided.
    Output: Persian text ready to be posted to ClickUp task.
    """

def format_clickup_success_comment(
    reporter_username: Optional[str] = None
) -> str:
    """
    Standard Persian success message for when all 9 checks pass.
    """
```

## Dependencies

- `deploy_automation.models.ProjectReport`

## Agent Notes

- Output is in **Persian**. Do not translate to English.
- `format_telegram_review_message` uses **HTML** formatting (not Markdown) — uses `<b>`, `<code>` tags.
- `format_clickup_final_comment` is for failed checks (issues list). `format_clickup_success_comment` is for all-pass scenario.
- This module is very small — safe to read in full when needed.
