"""API и статика мини аппа (FastAPI)."""


import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from aiogram import Bot
from aiogram.types import BufferedInputFile
from aiogram.utils.web_app import safe_parse_webapp_init_data
from fastapi import APIRouter, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from app.service import HomeworkService, ServiceError, submission_to_dict

STATIC = Path(__file__).parent / "static"
INIT_DATA_MAX_AGE = 24 * 3600


@dataclass
class CurrentUser:
    id: int
    is_admin: bool


def attachment(name: str) -> dict[str, str]:
    return {"Content-Disposition": f"attachment; filename=\"homework.ipynb\"; filename*=UTF-8''{quote(name)}"}


def build_api(service: HomeworkService, bot: Bot | None) -> APIRouter:
    settings = service.settings
    api = APIRouter(prefix="/api")

    async def current_user(x_telegram_init_data: Annotated[str, Header()] = "") -> CurrentUser:
        if x_telegram_init_data:
            try:
                data = safe_parse_webapp_init_data(settings.bot_token, x_telegram_init_data)
            except ValueError:
                raise HTTPException(401, "Неверная подпись Telegram")
            if data.auth_date and time.time() - data.auth_date.timestamp() > INIT_DATA_MAX_AGE:
                raise HTTPException(401, "Сессия устарела, открой мини апп заново")
            if data.user is None:
                raise HTTPException(401, "Нет пользователя")
            u = data.user
            full_name = " ".join(x for x in [u.first_name, u.last_name] if x)
            await service.upsert_user(u.id, u.username, full_name)
            return CurrentUser(u.id, service.is_admin(u.id))
        if settings.dev_user_id:
            await service.upsert_user(settings.dev_user_id, "dev", "Dev User")
            return CurrentUser(settings.dev_user_id, service.is_admin(settings.dev_user_id))
        raise HTTPException(401, "Открой мини апп из Telegram")

    UserDep = Annotated[CurrentUser, Depends(current_user)]

    def admin_only(user: UserDep) -> CurrentUser:
        if not user.is_admin:
            raise HTTPException(403, "Только для организаторов")
        return user

    AdminDep = Annotated[CurrentUser, Depends(admin_only)]

    def err(e: ServiceError) -> HTTPException:
        return HTTPException(400, str(e))

    @api.get("/me")
    async def me(user: UserDep):
        return {"id": user.id, "is_admin": user.is_admin, "bot_enabled": bot is not None}

    @api.get("/assignments")
    async def assignments(user: UserDep):
        views = await service.list_assignments(user.id, include_inactive=user.is_admin)
        return [v.to_dict(settings.tz) for v in views]

    @api.get("/assignments/{aid}/template")
    async def template(aid: int, user: UserDep):
        try:
            name, raw = await service.template_bytes(aid)
        except ServiceError as e:
            raise HTTPException(404, str(e))
        return Response(raw, media_type="application/x-ipynb+json", headers=attachment(name))

    @api.post("/assignments/{aid}/template/send")
    async def template_to_chat(aid: int, user: UserDep):
        """Скачивание из WebView на телефоне работает не везде, поэтому бот присылает файл в чат."""
        if bot is None:
            raise HTTPException(503, "Бот не запущен")
        try:
            name, raw = await service.template_bytes(aid)
        except ServiceError as e:
            raise HTTPException(404, str(e))
        await bot.send_document(user.id, BufferedInputFile(raw, filename=name), caption="Шаблон задания")
        return {"ok": True}

    @api.post("/assignments/{aid}/submissions")
    async def submit(aid: int, user: UserDep, file: UploadFile = File(...)):
        raw = await file.read()
        try:
            sub = await service.submit(user.id, aid, raw, file.filename or "")
        except ServiceError as e:
            raise err(e)
        return submission_to_dict(sub)

    @api.get("/submissions")
    async def my_submissions(user: UserDep, assignment_id: int | None = None):
        subs = await service.list_user_submissions(user.id, assignment_id)
        return [submission_to_dict(s) | {"assignment_title": s.assignment.title} for s in subs]

    @api.get("/submissions/{sid}")
    async def submission(sid: int, user: UserDep):
        try:
            sub = await service.get_submission(sid)
        except ServiceError as e:
            raise HTTPException(404, str(e))
        if sub.user_id != user.id and not user.is_admin:
            raise HTTPException(403, "Это не твоя сдача")
        d = submission_to_dict(sub, include_user=user.is_admin) | {"assignment_title": sub.assignment.title}
        if not user.is_admin:
            d["error"] = None
        return d

    # ---------- админка ----------

    @api.post("/admin/assignments")
    async def create_assignment(
        user: AdminDep,
        title: Annotated[str, Form()],
        template: UploadFile = File(...),
        reference: UploadFile = File(...),
        description: Annotated[str, Form()] = "",
        max_attempts: Annotated[int | None, Form()] = None,
        deadline: Annotated[str, Form()] = "",
    ):
        dl = None
        if deadline:
            try:
                dl = datetime.fromisoformat(deadline)
            except ValueError:
                raise HTTPException(400, "Неверный формат дедлайна")
            if dl.tzinfo is None:
                dl = dl.replace(tzinfo=settings.tz)
        try:
            a, warnings = await service.create_assignment(
                title, description, await template.read(), await reference.read(), max_attempts, dl
            )
        except ServiceError as e:
            raise err(e)
        prepared = await service._get_prepared(a)
        return {"id": a.id, "warnings": warnings, "tasks": [{"number": t.number, "title": t.title} for t in prepared.tasks]}

    @api.patch("/admin/assignments/{aid}")
    async def update_assignment(aid: int, user: AdminDep, is_active: Annotated[bool, Form()]):
        try:
            a = await service.set_active(aid, is_active)
        except ServiceError as e:
            raise HTTPException(404, str(e))
        return {"id": a.id, "is_active": a.is_active}

    @api.get("/admin/assignments/{aid}/submissions")
    async def assignment_submissions(aid: int, user: AdminDep):
        subs = await service.list_assignment_submissions(aid)
        return [submission_to_dict(s, include_user=True) for s in subs]

    @api.get("/admin/assignments/{aid}/export.csv")
    async def export(aid: int, user: AdminDep):
        try:
            data = await service.export_csv(aid)
        except ServiceError as e:
            raise HTTPException(404, str(e))
        return Response(data.encode("utf-8-sig"), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="results_{aid}.csv"'})

    @api.post("/admin/submissions/{sid}/recheck")
    async def recheck(sid: int, user: AdminDep):
        try:
            sub = await service.recheck(sid)
        except ServiceError as e:
            raise err(e)
        return {"id": sub.id, "status": "queued"}

    @api.get("/admin/submissions/{sid}/file")
    async def submission_file(sid: int, user: AdminDep):
        try:
            sub = await service.get_submission(sid)
        except ServiceError as e:
            raise HTTPException(404, str(e))
        return FileResponse(sub.file_path, filename=sub.file_name or f"submission_{sid}.ipynb")

    return api


def build_app(service: HomeworkService, bot: Bot | None, lifespan=None) -> FastAPI:
    app = FastAPI(title="DSC Homework Bot", lifespan=lifespan)
    app.include_router(build_api(service, bot))

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app
