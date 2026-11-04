import math

import pytest

from app.metrics import SubmissionError, better, read_csv, score

SOL = b"id,target\n1,1\n2,0\n3,1\n4,0\n"


def test_accuracy_f1_and_label_normalisation():
    sub = b"id,target\n1,1.0\n2,0\n3,0\n4,0\n"
    assert score(sub, SOL, "id", "target", "accuracy") == 0.75
    assert score(sub, SOL, "id", "target", "f1") == pytest.approx(2 / 3)
    # f1_macro: класс 1 -> 2/3, класс 0 -> 0.8
    assert score(sub, SOL, "id", "target", "f1_macro") == pytest.approx((2 / 3 + 0.8) / 2)


def test_semicolon_and_order_independent():
    sub = "id;target\n4;0\n3;1\n2;0\n1;1\n".encode("utf-8-sig")
    assert score(sub, SOL, "id", "target", "accuracy") == 1.0


def test_roc_auc_with_ties():
    sub = b"id,target\n1,0.9\n2,0.1\n3,0.5\n4,0.5\n"
    # пары (pos, neg): (1,2)=1 (1,4)=1 (3,2)=1 (3,4)=0.5 -> 3.5/4
    assert score(sub, SOL, "id", "target", "roc_auc") == pytest.approx(0.875)


def test_regression_metrics():
    sol = b"id,y\na,1\nb,2\nc,3\n"
    sub = b"id,y\na,1\nb,2\nc,5\n"
    assert score(sub, sol, "id", "y", "mae") == pytest.approx(2 / 3)
    assert score(sub, sol, "id", "y", "mse") == pytest.approx(4 / 3)
    assert score(sub, sol, "id", "y", "rmse") == pytest.approx(math.sqrt(4 / 3))
    assert score(sub, sol, "id", "y", "r2") == pytest.approx(1 - 4 / 2)


@pytest.mark.parametrize("sub, msg", [
    (b"id,pred\n1,1\n", "нет колонки"),
    (b"id,target\n1,1\n2,0\n", "нет предсказаний"),
    (b"id,target\n1,1\n2,0\n3,1\n4,0\n5,1\n", "лишние id"),
    (b"id,target\n1,1\n1,0\n3,1\n4,0\n", "несколько раз"),
    (b"", "пустой"),
])
def test_validation_errors(sub, msg):
    with pytest.raises(SubmissionError, match=msg):
        score(sub, SOL, "id", "target", "accuracy")


def test_non_numeric_regression():
    with pytest.raises(SubmissionError, match="не является числом"):
        score(b"id,y\na,x\n", b"id,y\na,1\n", "id", "y", "rmse")


def test_better_and_read_cp1251():
    assert better(0.9, 0.8, "accuracy") and better(1.0, 2.0, "rmse")
    header, rows = read_csv("id,имя\n1,Вася\n".encode("cp1251"))
    assert header == ["id", "имя"] and rows[0]["имя"] == "Вася"
