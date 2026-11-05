"""Тестовая часть задания: формат вопросов, разбор .md, проверка ответов.

Тест хранится в Assignment.quiz (JSON):
    {"attempts": 1, "questions": [вопрос, ...]}

Вопрос:
    {"id": "q1a2b3", "type": "single" | "multiple" | "text" | "number",
     "text": "условие в markdown, формулы в $...$ / $$...$$", "points": 1,
     "options": [{"id": "a", "text": "..."}],      # single / multiple
     "correct": ["a"],                              # single / multiple
     "answers": ["ответ", "другой вариант"],         # text
     "value": 3.14, "tolerance": 0.01,              # number
     "explanation": "пояснение, показывается после дедлайна"}
"""

from __future__ import annotations

import math
import re
import secrets
from fractions import Fraction

TYPES = ("single", "multiple", "text", "number")
MAX_QUESTIONS = 200
MAX_POINTS = 100
MAX_TEXT = 20000
MAX_OPTIONS = 26
DEFAULT_ATTEMPTS = 1


class QuizError(ValueError):
    """Ошибка в тесте, текст можно показать организатору."""


def _new_id(prefix: str = "q") -> str:
    return prefix + secrets.token_hex(4)


# ---------- проверка и нормализация теста от организатора ----------


def _num(v, what: str) -> float:
    try:
        x = parse_number(v) if isinstance(v, str) else float(v)
    except (TypeError, ValueError):
        x = None
    if x is None or not math.isfinite(x):
        raise QuizError(f"{what}: нужно число")
    return x


def normalize_quiz(data: dict | None) -> dict | None:
    """Проверяет тест из конструктора и приводит его к каноничному виду. None или пустой список — теста нет."""
    if not data:
        return None
    if not isinstance(data, dict):
        raise QuizError("Неверный формат теста")
    raw_questions = data.get("questions") or []
    if not isinstance(raw_questions, list):
        raise QuizError("Неверный формат теста")
    if not raw_questions:
        return None
    if len(raw_questions) > MAX_QUESTIONS:
        raise QuizError(f"Слишком много вопросов (максимум {MAX_QUESTIONS})")
    try:
        attempts = int(data.get("attempts") or DEFAULT_ATTEMPTS)
    except (TypeError, ValueError):
        raise QuizError("Число попыток теста должно быть целым")
    if not 1 <= attempts <= 100:
        raise QuizError("Попыток на тест должно быть от 1 до 100")

    questions, seen = [], set()
    for i, q in enumerate(raw_questions, 1):
        if not isinstance(q, dict):
            raise QuizError(f"Вопрос {i}: неверный формат")
        where = f"Вопрос {i}"
        qtype = q.get("type") or "single"
        if qtype not in TYPES:
            raise QuizError(f"{where}: неизвестный тип «{qtype}»")
        text = str(q.get("text") or "").strip()
        if not text:
            raise QuizError(f"{where}: пустое условие")
        if len(text) > MAX_TEXT:
            raise QuizError(f"{where}: условие слишком длинное")
        try:
            points = int(q.get("points") or 1)
        except (TypeError, ValueError):
            raise QuizError(f"{where}: баллы должны быть целым числом")
        if not 1 <= points <= MAX_POINTS:
            raise QuizError(f"{where}: баллы должны быть от 1 до {MAX_POINTS}")
        qid = str(q.get("id") or "")[:32]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", qid) or qid in seen:
            qid = _new_id()
        seen.add(qid)
        out = {"id": qid, "type": qtype, "text": text, "points": points}
        explanation = str(q.get("explanation") or "").strip()
        if explanation:
            out["explanation"] = explanation[:MAX_TEXT]

        if qtype in ("single", "multiple"):
            options, ids = [], set()
            for j, o in enumerate(q.get("options") or [], 1):
                if isinstance(o, str):
                    o = {"text": o}
                otext = str((o or {}).get("text") or "").strip()
                if not otext:
                    raise QuizError(f"{where}: пустой вариант ответа №{j}")
                oid = str(o.get("id") or "")[:16]
                if not re.fullmatch(r"[A-Za-z0-9_-]+", oid) or oid in ids:
                    oid = _new_id("o")
                ids.add(oid)
                options.append({"id": oid, "text": otext[:MAX_TEXT]})
            if len(options) < 2:
                raise QuizError(f"{where}: нужно хотя бы два варианта ответа")
            if len(options) > MAX_OPTIONS:
                raise QuizError(f"{where}: слишком много вариантов (максимум {MAX_OPTIONS})")
            correct = [c for c in (q.get("correct") or []) if c in ids]
            correct = [o["id"] for o in options if o["id"] in set(correct)]  # порядок как у вариантов
            if not correct:
                raise QuizError(f"{where}: отметь правильный вариант")
            if qtype == "single" and len(correct) > 1:
                raise QuizError(f"{where}: в вопросе с одним ответом отмечено несколько верных вариантов")
            out["options"] = options
            out["correct"] = correct
        elif qtype == "text":
            answers = [str(a).strip()[:500] for a in (q.get("answers") or []) if str(a).strip()]
            if not answers:
                raise QuizError(f"{where}: укажи правильный ответ")
            out["answers"] = list(dict.fromkeys(answers))[:20]
        else:
            out["value"] = _num(q.get("value"), f"{where}: правильный ответ")
            tol = q.get("tolerance")
            tol = 0.0 if tol in (None, "") else _num(tol, f"{where}: допуск")
            if tol < 0:
                raise QuizError(f"{where}: допуск не может быть отрицательным")
            out["tolerance"] = tol
        questions.append(out)
    return {"attempts": attempts, "questions": questions}


def total_points(quiz: dict | None) -> int:
    return sum(int(q.get("points") or 1) for q in (quiz or {}).get("questions", []))


def public_question(q: dict, number: int) -> dict:
    """Вопрос без правильных ответов — для участника до дедлайна."""
    out = {"id": q["id"], "number": number, "type": q["type"], "text": q["text"], "points": q["points"]}
    if q["type"] in ("single", "multiple"):
        out["options"] = [{"id": o["id"], "text": o["text"]} for o in q["options"]]
    return out


def key_of(q: dict) -> dict:
    """Правильный ответ и пояснение вопроса."""
    return {k: q[k] for k in ("correct", "answers", "value", "tolerance", "explanation") if k in q}


# ---------- проверка ответов ----------


def parse_number(s: str) -> float | None:
    s = (s or "").strip().replace("−", "-").replace(" ", "").replace(" ", "").replace(",", ".")
    if not s:
        return None
    try:
        if "/" in s:
            return float(Fraction(s))
        return float(s)
    except (ValueError, ZeroDivisionError):
        return None


def normalize_text(s: str) -> str:
    s = (s or "").lower().replace("ё", "е").strip()
    s = re.sub(r"\s+", " ", s)
    return s.strip(" .;!")


def is_correct(q: dict, answer) -> bool:
    t = q["type"]
    if t == "single":
        return isinstance(answer, str) and answer in q["correct"]
    if t == "multiple":
        return isinstance(answer, list) and set(answer) == set(q["correct"])
    if t == "text":
        return isinstance(answer, str) and normalize_text(answer) in {normalize_text(a) for a in q["answers"]}
    if t == "number":
        x = parse_number(answer) if isinstance(answer, str) else None
        if x is None:
            return False
        return abs(x - q["value"]) <= q.get("tolerance", 0) + 1e-9 * max(1.0, abs(q["value"]))
    return False


def clean_answer(q: dict, answer):
    """Ответ участника в каноничном виде (лишнее отбрасываем). None — ответа нет."""
    t = q["type"]
    if t == "single":
        ids = {o["id"] for o in q["options"]}
        return answer if isinstance(answer, str) and answer in ids else None
    if t == "multiple":
        if not isinstance(answer, list):
            return None
        chosen = {a for a in answer if isinstance(a, str)}
        picked = [o["id"] for o in q["options"] if o["id"] in chosen]
        return picked or None
    if answer is None or isinstance(answer, (list, dict)):
        return None
    answer = str(answer).strip()[:500]
    return answer or None


def auto_comment(score: int, max_score: int) -> str:
    if max_score <= 0:
        return ""
    ratio = score / max_score
    if ratio >= 1:
        return "Молодец, всё верно!"
    if ratio >= 0.75:
        return "Совсем чуть-чуть не хватило до идеала."
    if ratio >= 0.4:
        return "Неплохо, но есть над чем поработать."
    return "В следующий раз получится лучше!"


def grade(quiz: dict, answers: dict) -> dict:
    """Проверяет ответы. Результат хранит снимок вопросов, чтобы правки теста не ломали историю."""
    answers = answers if isinstance(answers, dict) else {}
    items = []
    for n, q in enumerate(quiz["questions"], 1):
        ans = clean_answer(q, answers.get(q["id"]))
        ok = ans is not None and is_correct(q, ans)
        items.append(public_question(q, n) | key_of(q) | {
            "answer": ans,
            "is_correct": ok,
            "score": q["points"] if ok else 0,
            "auto_score": None,  # не None — организатор поменял баллы, здесь оценка системы
        })
    return summarize({"kind": "quiz", "questions": items})


def summarize(result: dict) -> dict:
    qs = result["questions"]
    result["score"] = sum(q["score"] for q in qs)
    result["max_score"] = sum(q["points"] for q in qs)
    result["answered"] = sum(1 for q in qs if q.get("answer") is not None)
    result["correct"] = sum(1 for q in qs if q.get("is_correct"))
    result["auto_comment"] = auto_comment(result["score"], result["max_score"])
    return result


def regrade(result: dict, quiz: dict) -> dict:
    """Пересчёт попытки после правки ключа. Баллы, поставленные организатором вручную, сохраняются."""
    by_id = {q["id"]: (n, q) for n, q in enumerate(quiz["questions"], 1)}
    items = []
    for old in result.get("questions", []):
        if old["id"] not in by_id:
            items.append(old)  # вопрос удалили — оставляем как было
            continue
        n, q = by_id[old["id"]]
        ans = clean_answer(q, old.get("answer"))
        ok = ans is not None and is_correct(q, ans)
        item = public_question(q, n) | key_of(q) | {"answer": ans, "is_correct": ok}
        if old.get("auto_score") is not None:  # ручная оценка — не трогаем
            item["score"] = min(old["score"], q["points"])
            item["auto_score"] = q["points"] if ok else 0
        else:
            item["score"] = q["points"] if ok else 0
            item["auto_score"] = None
        items.append(item)
    items.sort(key=lambda x: x.get("number", 0))
    return summarize({**result, "questions": items})


def hide_keys(result: dict) -> dict:
    """Результат попытки без правильных ответов (до дедлайна)."""
    out = dict(result)
    out["questions"] = [{k: v for k, v in q.items() if k not in ("correct", "answers", "value", "tolerance", "explanation")}
                        for q in result.get("questions", [])]
    out["keys_hidden"] = True
    return out


# ---------- разбор .md ----------

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_NUMBERED = re.compile(r"^(\d{1,3})[.)]\s+(.*)$")
_CHECK = re.compile(r"^\s*[-*+]\s+\[([ xX+vV✓✔]?)\]\s?(.*)$")
_RADIO = re.compile(r"^\s*[-*+]\s+\(([ xX+vV✓✔•*]?)\)\s?(.*)$")
_LETTER = re.compile(r"^\s*([A-Za-zА-Яа-я])[).]\s+(.*)$")
_DIRECTIVE = re.compile(
    r"^\s*(?:\*\*|__)?(ответ|правильный ответ|верный ответ|ответы|answer|баллы|балл|балла|баллов|points|"
    r"пояснение|объяснение|решение|explanation|тип|type|допуск|погрешность|tolerance)(?:\*\*|__)?\s*[:：]\s*(?:\*\*|__)?\s*(.*)$",
    re.IGNORECASE,
)
_POINTS_IN_TITLE = re.compile(r"\s*[(\[]\s*(\d{1,3})\s*(?:балл\w*|б\.?|pts?|points?)\s*[)\]]\s*", re.IGNORECASE)
_QUESTION_PREFIX = re.compile(r"^(?:вопрос|question|задание|q)\s*№?\s*\d*\s*[.:)\-–—]?\s*", re.IGNORECASE)
_NUM_PREFIX = re.compile(r"^\d{1,3}\s*[.)]\s*")
_TOLERANCE = re.compile(r"^(.*?)\s*(?:±|\+-|\+/-)\s*(.+)$")

_TYPE_WORDS = {
    "single": ("single", "один", "одиночный", "один вариант", "один ответ", "radio"),
    "multiple": ("multiple", "несколько", "множественный", "несколько вариантов", "несколько ответов", "checkbox"),
    "text": ("text", "текст", "строка", "свой ответ", "ввод"),
    "number": ("number", "число", "числовой", "numeric"),
}
_CHECKED = set("xX+vV✓✔•*")


def _is_fence(line: str) -> str | None:
    m = re.match(r"^\s*(```+|~~~+)", line)
    return m.group(1)[:3] if m else None


def _mask_lines(lines: list[str]) -> list[bool]:
    """True — строка внутри блока кода или формулы $$...$$ (её не разбираем)."""
    inside, fence, math_open = [], None, False
    for line in lines:
        f = _is_fence(line)
        if fence:
            inside.append(True)
            if f and f[0] == fence[0]:
                fence = None
            continue
        if math_open:
            inside.append(True)
            if "$$" in line:
                math_open = False
            continue
        if f:
            fence = f
            inside.append(True)
            continue
        stripped = line.strip()
        if stripped.startswith("$$") and stripped.count("$$") == 1:
            math_open = True
            inside.append(True)
            continue
        inside.append(False)
    return inside


def _split_questions(lines: list[str], masked: list[bool]) -> tuple[list[tuple[str, list[str], list[bool]]], str]:
    """Делит документ на вопросы. Возвращает [(заголовок, строки, маска)], название теста."""
    heads = [(i, len(m.group(1)), m.group(2)) for i, line in enumerate(lines)
             if not masked[i] and (m := _HEADING.match(line))]
    title = ""
    starts: list[tuple[int, str]] = []
    if heads:
        levels = [lv for _, lv, _ in heads]
        repeated = sorted({lv for lv in levels if levels.count(lv) >= 2})
        level = repeated[0] if repeated else min(levels)
        if level == 1 and levels.count(1) == 1 and len(heads) > 1:
            level = min(lv for lv in levels if lv > 1)
        for i, lv, text in heads:
            if lv == level:
                starts.append((i, text))
            elif lv < level and not title:
                title = text
    else:
        for i, line in enumerate(lines):
            if not masked[i] and (m := _NUMBERED.match(line)):
                starts.append((i, line))
    blocks = []
    for k, (i, head) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        blocks.append((head, lines[i + 1:end], masked[i + 1:end]))
    return blocks, title


def _clean_title(head: str) -> tuple[str, int | None]:
    points = None
    m = _POINTS_IN_TITLE.search(head)
    if m:
        points = int(m.group(1))
        head = (head[:m.start()] + " " + head[m.end():]).strip()
    head = _QUESTION_PREFIX.sub("", head.strip(), count=1) if _QUESTION_PREFIX.match(head.strip()) else head.strip()
    head = _NUM_PREFIX.sub("", head, count=1)
    return head.strip(), points


def _trim(lines: list[str]) -> str:
    text = "\n".join(lines).strip("\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _parse_type(word: str) -> str | None:
    w = word.strip().lower().strip(".")
    for t, words in _TYPE_WORDS.items():
        if w in words:
            return t
    return None


def _parse_block(head: str, lines: list[str], masked: list[bool], number: int) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    where = f"Вопрос {number}"
    title, points = _clean_title(head)
    body: list[str] = [title] if title else []
    options: list[dict] = []  # {"text", "checked", "letter", "radio"}
    answer_raw: str | None = None
    explanation: list[str] = []
    qtype: str | None = None
    tolerance_raw: str | None = None
    mode = "body"  # body | options | explanation

    for idx, (line, inside) in enumerate(zip(lines, masked)):
        if inside:
            if mode == "explanation":
                explanation.append(line)
            elif mode == "options" and options and (line[:1] in (" ", "\t") or not line.strip()):
                options[-1]["text"] += "\n" + line.strip("\n")
            else:
                body.append(line)
            continue
        m = _DIRECTIVE.match(line)
        if m:
            key, value = m.group(1).lower(), m.group(2).strip().rstrip("*_").strip()
            if key in ("ответ", "правильный ответ", "верный ответ", "ответы", "answer"):
                answer_raw = value
                mode = "after"
            elif key in ("баллы", "балл", "балла", "баллов", "points"):
                if re.fullmatch(r"\d{1,3}", value):
                    points = int(value)
                else:
                    warnings.append(f"{where}: не понял число баллов «{value}»")
            elif key in ("пояснение", "объяснение", "решение", "explanation"):
                explanation = [value] if value else []
                mode = "explanation"
            elif key in ("тип", "type"):
                qtype = _parse_type(value)
                if qtype is None:
                    warnings.append(f"{where}: неизвестный тип «{value}»")
            else:
                tolerance_raw = value
            continue
        if mode == "explanation":
            explanation.append(line)
            continue
        cm = _CHECK.match(line) or _RADIO.match(line)
        if cm:
            radio = not _CHECK.match(line)
            options.append({"text": cm.group(2).strip(), "checked": cm.group(1) in _CHECKED, "radio": radio, "letter": None})
            mode = "options"
            continue
        lm = _LETTER.match(line)
        if lm and ((options and options[-1]["letter"]) or _letters_follow(lines, idx)):
            options.append({"text": lm.group(2).strip(), "checked": False, "radio": False, "letter": lm.group(1).lower()})
            mode = "options"
            continue
        if mode == "options" and line[:1] in (" ", "\t") and line.strip():
            options[-1]["text"] += "\n" + line.strip()
            continue
        if mode == "options" and not line.strip():
            continue
        body.append(line)

    text = _trim(body)
    q: dict = {"id": _new_id(), "text": text, "points": points or 1}
    expl = _trim(explanation)
    if expl:
        q["explanation"] = expl
    if not text:
        warnings.append(f"{where}: пустое условие")

    if options:
        opts = [{"id": _new_id("o"), "text": o["text"]} for o in options]
        checked = [opts[i]["id"] for i, o in enumerate(options) if o["checked"]]
        if answer_raw:
            letters = [o["letter"] for o in options]
            picked = [s.strip().lower().strip(").") for s in re.split(r"[,;\s]+", answer_raw) if s.strip()]
            for p in picked:
                if p in letters:
                    checked.append(opts[letters.index(p)]["id"])
                elif p.isdigit() and 1 <= int(p) <= len(opts):
                    checked.append(opts[int(p) - 1]["id"])
                else:
                    warnings.append(f"{where}: в ответе «{answer_raw}» не понял «{p}»")
        checked = list(dict.fromkeys(checked))
        if qtype not in ("single", "multiple"):
            radio = any(o["radio"] for o in options)
            qtype = "single" if radio or len(checked) <= 1 else "multiple"
        if qtype == "single" and len(checked) > 1:
            warnings.append(f"{where}: отмечено несколько верных вариантов — сделал вопрос с несколькими ответами")
            qtype = "multiple"
        if not checked:
            warnings.append(f"{where}: не отмечен правильный вариант")
        if len(opts) < 2:
            warnings.append(f"{where}: нужен хотя бы ещё один вариант ответа")
        q |= {"type": qtype, "options": opts, "correct": checked}
        return q, warnings

    value = None
    tol = None
    raw = (answer_raw or "").strip()
    if raw and qtype in (None, "number"):
        tm = _TOLERANCE.match(raw)
        base, tol_part = (tm.group(1), tm.group(2)) if tm else (raw, tolerance_raw)
        value = parse_number(base.strip("$ "))
        tol = parse_number(tol_part.strip("$ ")) if tol_part else None
        if qtype == "number" and value is None:
            warnings.append(f"{where}: ответ «{raw}» не похож на число")
    if value is not None and qtype in (None, "number"):
        q |= {"type": "number", "value": value, "tolerance": abs(tol) if tol is not None else 0.0}
        return q, warnings
    answers = [a.strip() for a in raw.split("|") if a.strip()] if raw else []
    if not answers:
        warnings.append(f"{where}: нет вариантов ответа и не указан «Ответ:»")
    q |= {"type": "text", "answers": answers}
    return q, warnings


def _letters_follow(lines: list[str], i: int) -> bool:
    """Строка «A) ...» — вариант, только если за ней идёт хотя бы ещё одна «B) ...»."""
    m = _LETTER.match(lines[i])
    if not m:
        return False
    nxt = chr(ord(m.group(1)) + 1)
    return any((lm := _LETTER.match(later)) and lm.group(1) == nxt for later in lines[i + 1:i + 6])


def parse_markdown(md: str) -> tuple[dict, list[str]]:
    """Разбирает .md с вопросами. Возвращает (тест в формате конструктора, предупреждения)."""
    md = (md or "").lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    if not md.strip():
        raise QuizError("Файл пустой")
    lines = md.split("\n")
    masked = _mask_lines(lines)
    blocks, title = _split_questions(lines, masked)
    if not blocks:
        raise QuizError("Не нашёл вопросов. Каждый вопрос должен начинаться с заголовка «## » или с номера «1.»")
    if len(blocks) > MAX_QUESTIONS:
        raise QuizError(f"Слишком много вопросов (максимум {MAX_QUESTIONS})")
    questions, warnings = [], []
    for n, (head, blines, bmask) in enumerate(blocks, 1):
        q, w = _parse_block(head, blines, bmask, n)
        questions.append(q)
        warnings += w
    return {"title": title, "attempts": DEFAULT_ATTEMPTS, "questions": questions}, warnings


def _fmt_num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else repr(float(x))


def to_markdown(quiz: dict, title: str = "") -> str:
    """Тест обратно в .md (тот же формат, что принимает parse_markdown)."""
    out = [f"# {title}", ""] if title else []
    for n, q in enumerate(quiz.get("questions", []), 1):
        out.append(f"## Вопрос {n}")
        out.append("")
        out.append(q["text"])
        out.append("")
        if q["type"] in ("single", "multiple"):
            correct = set(q["correct"])
            if q["type"] == "multiple" and len(correct) == 1:
                out += ["Тип: несколько", ""]
            for o in q["options"]:
                first, *rest = o["text"].split("\n")
                out.append(f"- [{'x' if o['id'] in correct else ' '}] {first}")
                out += [f"  {r}" for r in rest]
            out.append("")
        elif q["type"] == "text":
            out += [f"Ответ: {' | '.join(q['answers'])}", ""]
        else:
            tol = f" ± {_fmt_num(q['tolerance'])}" if q.get("tolerance") else ""
            out += [f"Ответ: {_fmt_num(q['value'])}{tol}", ""]
        if q["points"] != 1:
            out += [f"Баллы: {q['points']}", ""]
        if q.get("explanation"):
            out += [f"Пояснение: {q['explanation']}", ""]
    return "\n".join(out).rstrip() + "\n"
