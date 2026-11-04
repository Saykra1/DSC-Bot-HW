"""Тексты для Telegram (parse_mode=HTML)."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo

from app.checker import CheckResult

VERDICT_ICON = {"correct": "✅", "partial": "🟡", "incorrect": "❌", "not_done": "⬜"}
TG_LIMIT = 4000


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def fmt_dt(dt: datetime | None, tz: ZoneInfo) -> str:
    dt = as_utc(dt)
    return dt.astimezone(tz).strftime("%d.%m.%Y %H:%M") if dt else "—"


def feedback_messages(assignment_title: str, attempt: int, result: CheckResult) -> list[str]:
    """Отзыв, разбитый на сообщения не длиннее лимита Telegram."""
    head = (
        f"📝 <b>{escape(assignment_title)}</b>, попытка {attempt}\n"
        f"Итог: <b>{result.score}/{result.max_score}</b>\n\n{escape(result.summary)}"
    )
    blocks = [head]
    for t in result.tasks:
        lines = [f"{VERDICT_ICON.get(t.verdict, '•')} <b>Задание {t.number}. {escape(t.title)}</b> — {t.score}/10"]
        if t.feedback:
            lines.append(escape(t.feedback))
        for imp in t.improvements:
            lines.append(f"• {escape(imp)}")
        blocks.append("\n".join(lines))
    return pack(blocks)


def pack(blocks: list[str], limit: int = TG_LIMIT) -> list[str]:
    messages: list[str] = []
    cur = ""
    for b in blocks:
        while len(b) > limit:  # очень длинный блок режем как есть
            if cur:
                messages.append(cur)
                cur = ""
            messages.append(b[:limit])
            b = b[limit:]
        if cur and len(cur) + 2 + len(b) > limit:
            messages.append(cur)
            cur = b
        else:
            cur = f"{cur}\n\n{b}" if cur else b
    if cur:
        messages.append(cur)
    return messages
