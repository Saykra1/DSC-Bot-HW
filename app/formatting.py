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
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def fmt_dt(dt: datetime | None, tz: ZoneInfo) -> str:
    dt = as_utc(dt)
    return dt.astimezone(tz).strftime("%d.%m.%Y %H:%M") if dt else "—"


def feedback_messages(
    assignment_title: str, attempt: int, result: CheckResult, organizer_comment: str | None = None
) -> list[str]:
    """Отзыв, разбитый на сообщения не длиннее лимита Telegram."""
    head = (
        f"📝 <b>{escape(assignment_title)}</b>, попытка {attempt}\n"
        f"Итог: <b>{result.score}/{result.max_score}</b>\n\n{escape(result.summary)}"
    )
    blocks = [head]
    for t in result.tasks:
        edited = f" (изменено организатором, было {t.auto_score})" if t.auto_score is not None else ""
        lines = [f"{VERDICT_ICON.get(t.verdict, '•')} <b>Задание {t.number}. {escape(t.title)}</b> — {t.score}/10{edited}"]
        if t.feedback:
            lines.append(escape(t.feedback))
        for imp in t.improvements:
            lines.append(f"• {escape(imp)}")
        blocks.append("\n".join(lines))
    if organizer_comment:
        blocks.append(f"💬 <b>Комментарий организатора:</b>\n{escape(organizer_comment)}")
    return pack(blocks)


def quiz_messages(
    assignment_title: str, attempt: int | None, result: dict, organizer_comment: str | None = None, reveal: bool = False
) -> list[str]:
    """Итог попытки теста. Формулы в Telegram не отрисуются, поэтому подробный разбор — в мини аппе."""
    head = (
        f"🧩 <b>Тест «{escape(assignment_title)}»</b>, попытка {attempt or '—'}\n"
        f"Итог: <b>{result.get('score', 0)}/{result.get('max_score', 0)}</b> · "
        f"верно {result.get('correct', 0)} из {len(result.get('questions', []))}\n"
        f"{escape(result.get('auto_comment') or '')}"
    )
    marks = []
    for q in result.get("questions", []):
        icon = "✅" if q.get("score") == q.get("points") else ("🟡" if q.get("score") else "❌")
        edited = f" (изменено, было {q['auto_score']})" if q.get("auto_score") is not None else ""
        marks.append(f"{icon} Вопрос {q.get('number')}: {q.get('score')}/{q.get('points')}{edited}")
    blocks = [head, "\n".join(marks)]
    blocks.append("Разбор с формулами — в мини аппе." + ("" if reveal else " Правильные ответы откроются после дедлайна."))
    if organizer_comment:
        blocks.append(f"💬 <b>Комментарий организатора:</b>\n{escape(organizer_comment)}")
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
