"""Скачивание файлов по подписанной ссылке (Telegram.WebApp.downloadFile)."""

import time

from app import web
from tests.test_web import ADMIN, STUDENT, create_assignment, ctx, hdr  # noqa: F401


def link(client, path, user=STUDENT):
    r = client.post("/api/download-link", headers=hdr(user), json={"path": path})
    assert r.status_code == 200, r.text
    return r.json()["url"]


def test_template_downloads_by_link_without_headers(ctx):
    client, _, _ = ctx
    aid = create_assignment(client)
    url = link(client, f"/assignments/{aid}/template")
    assert url.startswith(f"/api/assignments/{aid}/template?dl=")
    r = client.get(url)  # так файл запрашивает сам Telegram: без X-Telegram-Init-Data
    assert r.status_code == 200
    assert r.content == client.get(f"/api/assignments/{aid}/template", headers=hdr(STUDENT)).content
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.headers["access-control-allow-origin"] == "https://web.telegram.org"


def test_link_is_bound_to_path_user_and_time(ctx, monkeypatch):
    client, _, _ = ctx
    aid = create_assignment(client)
    url = link(client, f"/assignments/{aid}/template")
    token = url.split("dl=")[1]
    # другой путь с тем же токеном не открывается
    assert client.get(f"/api/admin/assignments/{aid}/export.csv?dl={token}").status_code == 401
    # подделанный пользователь не проходит проверку подписи
    uid, exp, sig = token.split(".")
    assert client.get(f"/api/assignments/{aid}/template?dl={ADMIN}.{exp}.{sig}").status_code == 401
    # права проверяет сам эндпоинт: участнику ссылка на админский файл ничего не даёт
    assert client.get(link(client, f"/admin/assignments/{aid}/export.csv")).status_code == 403
    assert client.get(link(client, f"/admin/assignments/{aid}/export.csv", ADMIN)).status_code == 200
    # ссылка живёт несколько минут
    real = time.time
    monkeypatch.setattr(web.time, "time", lambda: real() + web.DOWNLOAD_LINK_TTL + 5)
    assert client.get(url).status_code == 401


def test_link_needs_session_and_clean_path(ctx):
    client, _, _ = ctx
    assert client.post("/api/download-link", json={"path": "/me"}).status_code == 401
    for bad in ["me", "/a?b=1", "/../me", "/x#y"]:
        assert client.post("/api/download-link", headers=hdr(STUDENT), json={"path": bad}).status_code == 400
    # без заголовка и без ссылки API по-прежнему закрыто
    assert client.get("/api/assignments").status_code == 401
