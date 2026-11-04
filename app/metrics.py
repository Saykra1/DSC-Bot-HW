"""Подсчёт метрик для хакатонов: чтение CSV и сравнение решения участника с эталонными ответами.

Без pandas/sklearn, чтобы не тянуть тяжёлые зависимости.
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass


class SubmissionError(ValueError):
    """Ошибка в файле решения — текст показывается участнику."""


@dataclass(frozen=True)
class Metric:
    name: str
    title: str
    higher_is_better: bool
    kind: str  # "label" | "score" | "regression"


METRICS: dict[str, Metric] = {
    m.name: m
    for m in [
        Metric("accuracy", "Accuracy", True, "label"),
        Metric("f1", "F1 (бинарная, положительный класс 1)", True, "label"),
        Metric("f1_macro", "F1 macro", True, "label"),
        Metric("roc_auc", "ROC AUC (нужны вероятности класса 1)", True, "score"),
        Metric("rmse", "RMSE", False, "regression"),
        Metric("mae", "MAE", False, "regression"),
        Metric("mse", "MSE", False, "regression"),
        Metric("r2", "R²", True, "regression"),
    ]
}


def read_csv(raw: bytes, what: str = "файл") -> tuple[list[str], list[dict[str, str]]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp1251")
        except UnicodeDecodeError as e:
            raise SubmissionError(f"{what}: не удалось прочитать кодировку, сохрани CSV в UTF-8") from e
    if not text.strip():
        raise SubmissionError(f"{what}: файл пустой")
    sample = text[:5000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    header = [h.strip() for h in (reader.fieldnames or [])]
    reader.fieldnames = header
    rows = [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]
    if not header:
        raise SubmissionError(f"{what}: нет строки с названиями колонок")
    return header, rows


def _column_map(raw: bytes, id_col: str, value_col: str, what: str) -> dict[str, str]:
    header, rows = read_csv(raw, what)
    for col in (id_col, value_col):
        if col not in header:
            raise SubmissionError(f"{what}: нет колонки «{col}». Найдены колонки: {', '.join(header)}")
    out: dict[str, str] = {}
    for i, row in enumerate(rows, start=2):
        key = row.get(id_col, "")
        if key in out:
            raise SubmissionError(f"{what}: id «{key}» встречается несколько раз (строка {i})")
        out[key] = row.get(value_col, "")
    return out


def ids_of(raw: bytes, id_col: str, what: str) -> list[str]:
    header, rows = read_csv(raw, what)
    if id_col not in header:
        raise SubmissionError(f"{what}: нет колонки «{id_col}». Найдены колонки: {', '.join(header)}")
    return [r.get(id_col, "") for r in rows]


def _norm_label(v: str) -> str:
    try:
        f = float(v.replace(",", "."))
    except ValueError:
        return v.strip()
    if math.isfinite(f) and f == int(f):
        return str(int(f))
    return str(f)


def _num(v: str, what: str, key: str) -> float:
    try:
        f = float(v.replace(",", "."))
    except ValueError:
        raise SubmissionError(f"{what}: значение «{v}» для id «{key}» не является числом") from None
    if not math.isfinite(f):
        raise SubmissionError(f"{what}: значение для id «{key}» не является конечным числом")
    return f


def score(submission_raw: bytes, solution_raw: bytes, id_col: str, target_col: str, metric: str) -> float:
    m = METRICS.get(metric)
    if m is None:
        raise ValueError(f"Неизвестная метрика {metric}")
    truth = _column_map(solution_raw, id_col, target_col, "Эталон")
    pred = _column_map(submission_raw, id_col, target_col, "Решение")
    missing = [k for k in truth if k not in pred]
    if missing:
        raise SubmissionError(
            f"Решение: нет предсказаний для {len(missing)} id (например, {', '.join(missing[:3])}). "
            f"Нужна строка для каждого id из test."
        )
    extra = [k for k in pred if k not in truth]
    if extra:
        raise SubmissionError(f"Решение: лишние id, которых нет в test (например, {', '.join(extra[:3])})")
    keys = list(truth)

    if m.kind == "label":
        y = [_norm_label(truth[k]) for k in keys]
        p = [_norm_label(pred[k]) for k in keys]
        if metric == "accuracy":
            return sum(a == b for a, b in zip(y, p)) / len(y)
        if metric == "f1":
            return _f1(y, p, "1")
        labels = sorted(set(y))
        return sum(_f1(y, p, c) for c in labels) / len(labels)

    if m.kind == "score":
        y = [_norm_label(truth[k]) for k in keys]
        classes = sorted(set(y))
        if len(classes) != 2:
            raise ValueError("ROC AUC считается только для двух классов")
        pos = "1" if "1" in classes else classes[-1]
        s = [_num(pred[k], "Решение", k) for k in keys]
        return _roc_auc([1 if v == pos else 0 for v in y], s)

    y = [_num(truth[k], "Эталон", k) for k in keys]
    p = [_num(pred[k], "Решение", k) for k in keys]
    n = len(y)
    if metric == "mae":
        return sum(abs(a - b) for a, b in zip(y, p)) / n
    mse = sum((a - b) ** 2 for a, b in zip(y, p)) / n
    if metric == "mse":
        return mse
    if metric == "rmse":
        return math.sqrt(mse)
    mean = sum(y) / n
    ss_tot = sum((a - mean) ** 2 for a in y)
    ss_res = mse * n
    return 1 - ss_res / ss_tot if ss_tot else 0.0


def _f1(y: list[str], p: list[str], positive: str) -> float:
    tp = sum(a == positive and b == positive for a, b in zip(y, p))
    fp = sum(a != positive and b == positive for a, b in zip(y, p))
    fn = sum(a == positive and b != positive for a, b in zip(y, p))
    return 2 * tp / (2 * tp + fp + fn) if tp else 0.0


def _roc_auc(y: list[int], s: list[float]) -> float:
    n_pos = sum(y)
    n_neg = len(y) - n_pos
    if not n_pos or not n_neg:
        raise ValueError("Для ROC AUC в эталоне должны быть оба класса")
    order = sorted(range(len(s)), key=lambda i: s[i])
    ranks = [0.0] * len(s)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and s[order[j + 1]] == s[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    sum_pos = sum(r for r, t in zip(ranks, y) if t)
    return (sum_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def better(a: float, b: float, metric: str) -> bool:
    """a лучше b по метрике?"""
    return a > b if METRICS[metric].higher_is_better else a < b
