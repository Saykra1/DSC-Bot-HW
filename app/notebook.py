"""Разбор .ipynb: поиск заданий в шаблоне и сопоставление ячеек с эталоном и работой участника.

Идея: шаблон для участников и эталон отличаются только ячейками-заданиями.
Все остальные ячейки совпадают и служат «якорями». Работа участника — это шаблон,
в котором дописаны ячейки-задания (участник может добавить свои ячейки).
Для каждого задания берём всё, что лежит между соседними якорями.
"""

from __future__ import annotations

import ast
import difflib
import json
import re
from dataclasses import dataclass, field

from app.config import DEFAULT_TASK_MARKERS


class NotebookError(ValueError):
    """Файл не похож на корректный Jupyter-ноутбук."""


@dataclass
class Cell:
    index: int
    cell_type: str
    source: str

    @property
    def key(self) -> str:
        return normalize(self.source)


@dataclass
class Task:
    number: int  # с 1
    template_index: int
    title: str
    instruction: str
    template_code: str
    context_before: str = ""  # код предыдущей ячейки, чтобы модель понимала переменные


@dataclass
class TaskMatch:
    task: Task
    code: str
    cells: list[int] = field(default_factory=list)
    ambiguous: bool = False  # несколько заданий подряд без якорей между ними


def load_notebook(raw: bytes | str) -> list[Cell]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise NotebookError("Файл не является корректным JSON-ноутбуком (.ipynb)") from e
    if not isinstance(data, dict) or not isinstance(data.get("cells"), list):
        raise NotebookError("В файле нет списка ячеек. Это точно .ipynb?")
    cells = []
    for i, c in enumerate(data["cells"]):
        if not isinstance(c, dict):
            continue
        src = c.get("source", "")
        if isinstance(src, list):
            src = "".join(str(s) for s in src)
        cells.append(Cell(index=i, cell_type=str(c.get("cell_type", "code")), source=str(src)))
    return cells


def normalize(src: str) -> str:
    lines = [ln.rstrip() for ln in src.replace("\r\n", "\n").split("\n")]
    return "\n".join(ln for ln in lines if ln.strip())


_PLACEHOLDER_LINE = re.compile(r"^\s*(\.\.\.|pass|raise NotImplementedError(\(\))?)\s*(#.*)?$")


def strip_noise(src: str) -> str:
    """Код без комментариев, пустых строк и заглушек вида `...`/`pass`."""
    out = []
    for ln in src.replace("\r\n", "\n").split("\n"):
        s = ln.strip()
        if not s or s.startswith("#") or _PLACEHOLDER_LINE.match(ln):
            continue
        out.append(ln.rstrip())
    return "\n".join(out)


def is_task_cell(cell: Cell, markers: list[str] | None = None) -> bool:
    if cell.cell_type != "code":
        return False
    markers = markers if markers is not None else DEFAULT_TASK_MARKERS
    low = cell.source.lower()
    if any(m in low for m in markers):
        return True
    return any(ln.strip() == "..." for ln in cell.source.split("\n"))


def _comment_text(src: str) -> str:
    lines = [ln.strip().lstrip("#").strip() for ln in src.split("\n") if ln.strip().startswith("#")]
    return "\n".join(ln for ln in lines if ln)


def _heading(md: str) -> str:
    for ln in md.split("\n"):
        s = ln.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()
    first = md.strip().split("\n")[0].strip() if md.strip() else ""
    return first[:80]


def find_tasks(cells: list[Cell], markers: list[str] | None = None) -> list[Task]:
    tasks: list[Task] = []
    last_heading = ""
    last_md = ""
    prev_code = ""
    for pos, cell in enumerate(cells):
        if cell.cell_type == "markdown":
            h = _heading(cell.source)
            if any(ln.strip().startswith("#") for ln in cell.source.split("\n")):
                last_heading = h
            last_md = cell.source.strip()
            continue
        if cell.cell_type != "code":
            continue
        if is_task_cell(cell, markers):
            comments = _comment_text(cell.source)
            instruction = "\n\n".join(x for x in [last_md, comments] if x)
            title = last_heading or (comments.split("\n")[0] if comments else f"Ячейка {cell.index + 1}")
            tasks.append(
                Task(
                    number=len(tasks) + 1,
                    template_index=pos,
                    title=title[:120],
                    instruction=instruction[:3000],
                    template_code=cell.source,
                    context_before=prev_code[-2000:],
                )
            )
        if cell.source.strip():
            prev_code = cell.source
    return tasks


def match_tasks(template: list[Cell], other: list[Cell], tasks: list[Task]) -> list[TaskMatch]:
    """Для каждого задания находит соответствующий код в другом ноутбуке (эталоне или работе)."""
    task_positions = {t.template_index for t in tasks}
    # Ячейки-задания получают уникальный ключ, чтобы не служить якорями.
    a_keys = [f"\0task{i}" if i in task_positions else c.key for i, c in enumerate(template)]
    b_keys = [c.key for c in other]
    # Пустые ячейки тоже не годятся в якоря.
    a_keys = [k if k else f"\0emptyA{i}" for i, k in enumerate(a_keys)]
    b_keys = [k if k else f"\0emptyB{i}" for i, k in enumerate(b_keys)]

    sm = difflib.SequenceMatcher(a=a_keys, b=b_keys, autojunk=False)
    mapping: dict[int, int] = {}
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            mapping[blk.a + k] = blk.b + k

    # Группируем задания по «окну» между соседними якорями.
    groups: dict[tuple[int, int], list[Task]] = {}
    for t in tasks:
        prev_anchor = max((a for a in mapping if a < t.template_index), default=None)
        next_anchor = min((a for a in mapping if a > t.template_index), default=None)
        lo = mapping[prev_anchor] + 1 if prev_anchor is not None else 0
        hi = mapping[next_anchor] if next_anchor is not None else len(other)
        groups.setdefault((lo, hi), []).append(t)

    results: list[TaskMatch] = []
    for (lo, hi), group in groups.items():
        region = [c for c in other[lo:hi] if c.cell_type == "code" and c.source.strip()]
        if len(group) == 1:
            t = group[0]
            results.append(TaskMatch(t, "\n\n".join(c.source for c in region), [c.index for c in region]))
        elif len(region) == len(group):
            for t, c in zip(group, region):
                results.append(TaskMatch(t, c.source, [c.index]))
        else:
            code = "\n\n".join(c.source for c in region)
            for t in group:
                results.append(TaskMatch(t, code, [c.index for c in region], ambiguous=True))
    results.sort(key=lambda m: m.task.number)
    return results


def syntax_error(code: str) -> str | None:
    """Возвращает описание синтаксической ошибки или None. Магии Jupyter (%, !) игнорируются."""
    cleaned = "\n".join(
        "" if ln.lstrip().startswith(("%", "!")) else ln for ln in code.replace("\r\n", "\n").split("\n")
    )
    try:
        ast.parse(cleaned)
    except SyntaxError as e:
        return f"строка {e.lineno}: {e.msg}"
    return None


def strip_outputs(raw: bytes) -> bytes:
    """Удаляет выводы ячеек (картинки и т.п.), чтобы шаблон, отправляемый участникам, был лёгким."""
    data = json.loads(raw)
    for c in data.get("cells", []):
        if c.get("cell_type") == "code":
            c["outputs"] = []
            c["execution_count"] = None
    return json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
