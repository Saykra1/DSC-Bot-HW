"""API и статика мини аппа (FastAPI)."""


import json
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
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import metrics
from app.hackathons import UploadedFile, hackathons_for
from app import quiz as qz
from app.service import HomeworkService, ServiceError, has_notebook, reference_available, submission_to_dict, total_max

STATIC = Path(__file__).parent / "static"
INIT_DATA_MAX_AGE = 24 * 3600


class ProfileIn(BaseModel):
    nickname: str | None = None
    fio: str | None = None


class ActiveIn(BaseModel):
    is_active: bool


class ReviewIn(BaseModel):
    task_scores: dict[int, int] = {}  # у теста ключи — номера вопросов
    comment: str | None = None


class QuizAnswersIn(BaseModel):
    answers: dict[str, str | list[str] | None] = {}


def participant_view(d: dict, sub) -> dict:
    """Участнику до дедлайна не отдаём правильные ответы теста."""
    if sub.kind == "quiz" and d.get("result") and not reference_available(sub.assignment):
        d["result"] = qz.hide_keys(d["result"])
    return d


def parse_quiz_field(value: str | None):
    """Тест из формы: None — не прислали (пустую строку FastAPI тоже отдаёт как None), "null" — убрать, иначе JSON."""
    if value is None:
        return False
    if not value.strip() or value.strip() == "null":
        return None
    try:
        return json.loads(value)
    except ValueError:
        raise HTTPException(400, "Неверный формат теста")


@dataclass
class CurrentUser:
    id: int
    is_admin: bool


def attachment(name: str) -> dict[str, str]:
    ext = Path(name).suffix or ".bin"
    return {"Content-Disposition": f"attachment; filename=\"file{ext}\"; filename*=UTF-8''{quote(name)}"}


async def upload(f: UploadFile | None) -> UploadedFile | None:
    if f is None or not f.filename:
        return None
    return UploadedFile(f.filename, await f.read())


def build_api(service: HomeworkService, bot: Bot | None) -> APIRouter:
    settings = service.settings
    hacks = hackathons_for(service)
    api = APIRouter(prefix="/api")
    known_users: dict[int, tuple[str | None, str]] = {}  # tg_id -> (username, имя), уже записанные в базу

    def parse_dt(value: str | None, what: str) -> datetime | None:
        """Время из формы: ISO или datetime-local. Без часового пояса считаем, что это МСК (TIMEZONE)."""
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.strip())
        except ValueError:
            raise HTTPException(400, f"Неверный формат: {what}")
        return dt.replace(tzinfo=settings.tz) if dt.tzinfo is None else dt

    def opt_dt(value: str | None, what: str):
        """Для правки: None — поле не прислали (не менять), "null" — убрать.

        Пустую строку из формы FastAPI отдаёт как None, поэтому для «убрать» нужен явный "null".
        """
        if value is None:
            return False
        if value.strip() == "null":
            return None
        return parse_dt(value, what)

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
            # Мини апп шлёт 3–5 запросов на каждый экран: пишем в базу, только если имя в Telegram поменялось
            if known_users.get(u.id) != (u.username, full_name):
                await service.upsert_user(u.id, u.username, full_name)
                known_users[u.id] = (u.username, full_name)
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
        u = await service.get_user(user.id)
        return {"id": user.id, "is_admin": user.is_admin, "bot_enabled": bot is not None, "profile": u.to_dict(),
                "timezone": settings.timezone}

    @api.patch("/profile")
    async def update_profile(body: ProfileIn, user: UserDep):
        try:
            u = await service.update_profile(user.id, body.nickname, body.fio)
        except ServiceError as e:
            raise err(e)
        return u.to_dict()

    @api.get("/assignments")
    async def assignments(user: UserDep):
        views = await service.list_assignments(user.id, include_inactive=user.is_admin)
        return [v.to_dict(settings.tz) for v in views]

    @api.get("/assignments/{aid}/template")
    async def template(aid: int, user: UserDep):
        try:
            name, raw = await service.template_bytes(aid, user.id)
        except ServiceError as e:
            raise HTTPException(404, str(e))
        return Response(raw, media_type="application/x-ipynb+json", headers=attachment(name))

    @api.post("/assignments/{aid}/template/send")
    async def template_to_chat(aid: int, user: UserDep):
        """Скачивание из WebView на телефоне работает не везде, поэтому бот присылает файл в чат."""
        if bot is None:
            raise HTTPException(503, "Бот не запущен")
        try:
            name, raw = await service.template_bytes(aid, user.id)
        except ServiceError as e:
            raise HTTPException(404, str(e))
        await bot.send_document(user.id, BufferedInputFile(raw, filename=name), caption="Шаблон задания")
        return {"ok": True}

    @api.get("/assignments/{aid}/reference")
    async def reference(aid: int, user: UserDep):
        """Эталонный код по заданиям — участникам открывается после дедлайна."""
        try:
            return await service.reference_tasks(aid, user.id)
        except ServiceError as e:
            raise HTTPException(403, str(e))

    @api.get("/assignments/{aid}/reference/file")
    async def reference_file(aid: int, user: UserDep):
        try:
            name, raw = await service.reference_bytes(aid, user.id)
        except ServiceError as e:
            raise HTTPException(403, str(e))
        return Response(raw, media_type="application/x-ipynb+json", headers=attachment(name))

    @api.post("/assignments/{aid}/reference/send")
    async def reference_to_chat(aid: int, user: UserDep):
        if bot is None:
            raise HTTPException(503, "Бот не запущен")
        try:
            name, raw = await service.reference_bytes(aid, user.id)
        except ServiceError as e:
            raise HTTPException(403, str(e))
        await bot.send_document(user.id, BufferedInputFile(raw, filename=name), caption="Эталонное решение")
        return {"ok": True}

    @api.post("/assignments/{aid}/submissions")
    async def submit(aid: int, user: UserDep, file: UploadFile = File(...)):
        raw = await file.read()
        try:
            sub = await service.submit(user.id, aid, raw, file.filename or "")
        except ServiceError as e:
            raise err(e)
        return submission_to_dict(sub) | {"queue": await service.queue_info(sub.id)}

    @api.get("/assignments/{aid}/quiz")
    async def quiz(aid: int, user: UserDep):
        try:
            return await service.quiz_for(aid, user.id)
        except ServiceError as e:
            raise HTTPException(404, str(e))

    @api.post("/assignments/{aid}/quiz")
    async def submit_quiz(aid: int, body: QuizAnswersIn, user: UserDep):
        try:
            sub = await service.submit_quiz(user.id, aid, body.answers)
            sub = await service.get_submission(sub.id)
        except ServiceError as e:
            raise err(e)
        d = submission_to_dict(sub) | {"assignment_title": sub.assignment.title,
                                       "reference_available": reference_available(sub.assignment)}
        return d if user.is_admin else participant_view(d, sub)

    @api.get("/submissions")
    async def my_submissions(user: UserDep, assignment_id: int | None = None):
        subs = await service.list_user_submissions(user.id, assignment_id)
        return [participant_view(submission_to_dict(s) | {"assignment_title": s.assignment.title}, s) for s in subs]

    @api.get("/submissions/{sid}")
    async def submission(sid: int, user: UserDep):
        try:
            sub = await service.get_submission(sid)
        except ServiceError as e:
            raise HTTPException(404, str(e))
        if sub.user_id != user.id and not user.is_admin:
            raise HTTPException(403, "Это не твоя сдача")
        d = submission_to_dict(sub, include_user=user.is_admin) | {
            "assignment_title": sub.assignment.title,
            "reference_available": reference_available(sub.assignment),
        }
        if sub.status == "queued":
            d["queue"] = await service.queue_info(sid)
        if not user.is_admin:
            d["error"] = None
            d = participant_view(d, sub)
        return d

    @api.get("/submissions/{sid}/code")
    async def my_code(sid: int, user: UserDep):
        """Код своей сдачи по заданиям. Эталон рядом — только после дедлайна (организаторам всегда)."""
        try:
            sub = await service.get_submission(sid)
        except ServiceError as e:
            raise HTTPException(404, str(e))
        if sub.user_id != user.id and not user.is_admin:
            raise HTTPException(403, "Это не твоя сдача")
        return await service.submission_code(sid, include_reference=user.is_admin)

    # ---------- хакатоны ----------

    @api.get("/metrics")
    async def metric_list(user: UserDep):
        return [{"name": m.name, "title": m.title, "higher_is_better": m.higher_is_better} for m in metrics.METRICS.values()]

    @api.get("/hackathons")
    async def hackathon_list(user: UserDep):
        return await hacks.list_all(user.id)

    async def get_hackathon(hid: int, user: CurrentUser):
        try:
            h = await hacks.get(hid)
        except ServiceError as e:
            raise HTTPException(404, str(e))
        if not h.is_active and not user.is_admin:
            raise HTTPException(404, "Хакатон не найден")
        return h

    @api.get("/hackathons/{hid}")
    async def hackathon(hid: int, user: UserDep):
        return await hacks.to_dict(await get_hackathon(hid, user), user.id)

    @api.get("/hackathons/{hid}/files/{kind}")
    async def hackathon_file(hid: int, kind: str, user: UserDep):
        try:
            name, raw = await hacks.file(hid, kind, user.id)
        except ServiceError as e:
            raise HTTPException(403, str(e))
        return Response(raw, media_type="application/octet-stream", headers=attachment(name))

    @api.post("/hackathons/{hid}/files/{kind}/send")
    async def hackathon_file_to_chat(hid: int, kind: str, user: UserDep):
        if bot is None:
            raise HTTPException(503, "Бот не запущен")
        try:
            name, raw = await hacks.file(hid, kind, user.id)
        except ServiceError as e:
            raise HTTPException(403, str(e))
        await bot.send_document(user.id, BufferedInputFile(raw, filename=name))
        return {"ok": True}

    @api.post("/hackathons/{hid}/submissions")
    async def hackathon_submit(hid: int, user: UserDep, file: UploadFile = File(...)):
        await get_hackathon(hid, user)
        try:
            sub = await hacks.submit(user.id, hid, await file.read(), file.filename or "")
        except ServiceError as e:
            raise err(e)
        mine = await hacks.my_submissions(user.id, hid)
        return next(x for x in mine if x["id"] == sub.id)

    @api.get("/hackathons/{hid}/submissions")
    async def hackathon_my_submissions(hid: int, user: UserDep):
        await get_hackathon(hid, user)
        return await hacks.my_submissions(user.id, hid)

    @api.get("/hackathons/{hid}/leaderboard")
    async def hackathon_leaderboard(hid: int, user: UserDep):
        await get_hackathon(hid, user)
        return await hacks.leaderboard(hid, user.id)

    # ---------- админка ----------

    @api.post("/admin/assignments")
    async def create_assignment(
        user: AdminDep,
        title: Annotated[str, Form()],
        template: UploadFile | None = File(None),
        reference: UploadFile | None = File(None),
        description: Annotated[str, Form()] = "",
        max_attempts: Annotated[int | None, Form()] = None,
        deadline: Annotated[str, Form()] = "",
        publish_at: Annotated[str, Form()] = "",
        quiz: Annotated[str | None, Form()] = None,
    ):
        """Задание: ноутбук (template + reference), тест (quiz — JSON конструктора) или обе части."""
        dl = parse_dt(deadline, "дедлайн")
        pub = parse_dt(publish_at, "время публикации")
        tpl, ref = await upload(template), await upload(reference)
        quiz_data = parse_quiz_field(quiz)
        try:
            a, warnings = await service.create_assignment(
                title, description, tpl.data if tpl else None, ref.data if ref else None, max_attempts, dl, pub,
                quiz=quiz_data or None,
            )
        except ServiceError as e:
            raise err(e)
        return {"id": a.id, "warnings": warnings, "tasks": await tasks_of(a)}

    async def tasks_of(a) -> list[dict]:
        if not has_notebook(a):
            return []
        prepared = await service._get_prepared(a)
        return [{"number": t.number, "title": t.title} for t in prepared.tasks]

    @api.patch("/admin/assignments/{aid}")
    async def update_assignment(aid: int, body: ActiveIn, user: AdminDep):
        try:
            a = await service.set_active(aid, body.is_active)
        except ServiceError as e:
            raise HTTPException(404, str(e))
        return {"id": a.id, "is_active": a.is_active}

    @api.post("/admin/assignments/{aid}/edit")
    async def edit_assignment(
        aid: int,
        user: AdminDep,
        title: Annotated[str | None, Form()] = None,
        description: Annotated[str | None, Form()] = None,
        max_attempts: Annotated[int | None, Form()] = None,
        deadline: Annotated[str | None, Form()] = None,
        publish_at: Annotated[str | None, Form()] = None,
        template: UploadFile | None = File(None),
        reference: UploadFile | None = File(None),
        quiz: Annotated[str | None, Form()] = None,
    ):
        """Правка задания. Не присланные поля не меняются; quiz="null" — убрать тест."""
        tpl = await upload(template)
        ref = await upload(reference)
        try:
            a, warnings = await service.update_assignment(
                aid, title=title, description=description, max_attempts=max_attempts,
                deadline=opt_dt(deadline, "дедлайн"), publish_at=opt_dt(publish_at, "время публикации"),
                template_raw=tpl.data if tpl else None, reference_raw=ref.data if ref else None,
                quiz=parse_quiz_field(quiz),
            )
        except ServiceError as e:
            raise err(e)
        return {"id": a.id, "warnings": warnings, "files_changed": bool(tpl or ref), "tasks": await tasks_of(a)}

    @api.get("/admin/assignments/{aid}/quiz")
    async def quiz_full(aid: int, user: AdminDep):
        try:
            return await service.quiz_full(aid)
        except ServiceError as e:
            raise HTTPException(404, str(e))

    @api.get("/admin/assignments/{aid}/quiz.md")
    async def quiz_markdown(aid: int, user: AdminDep):
        try:
            a = await service.get_assignment(aid)
        except ServiceError as e:
            raise HTTPException(404, str(e))
        if not a.quiz:
            raise HTTPException(404, "В этом задании нет теста")
        return Response(qz.to_markdown(a.quiz, a.title).encode("utf-8"), media_type="text/markdown",
                        headers=attachment(f"{a.title[:60]}.md"))

    @api.post("/admin/quiz/parse")
    async def quiz_parse(user: AdminDep, file: UploadFile | None = File(None), text: Annotated[str, Form()] = ""):
        """Разбор .md с вопросами для конструктора (в базу ничего не пишется)."""
        if file is not None and file.filename:
            raw = await file.read()
            if len(raw) > 2 * 1024 * 1024:
                raise HTTPException(400, "Файл слишком большой (максимум 2 МБ)")
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise HTTPException(400, "Файл должен быть в кодировке UTF-8")
        try:
            data, warnings = qz.parse_markdown(text)
        except qz.QuizError as e:
            raise HTTPException(400, str(e))
        return data | {"warnings": warnings}

    @api.post("/admin/assignments/{aid}/recheck")
    async def recheck_assignment(aid: int, user: AdminDep):
        n = await service.recheck_all(aid)
        return {"queued": n}

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

    @api.get("/admin/submissions/{sid}/code")
    async def submission_code(sid: int, user: AdminDep):
        try:
            return await service.submission_code(sid)
        except ServiceError as e:
            raise HTTPException(404, str(e))

    @api.patch("/admin/submissions/{sid}/review")
    async def review(sid: int, body: ReviewIn, user: AdminDep):
        try:
            sub = await service.review(sid, body.task_scores, body.comment, reviewer_id=user.id)
        except ServiceError as e:
            raise err(e)
        sub = await service.get_submission(sub.id)
        return submission_to_dict(sub, include_user=True) | {"assignment_title": sub.assignment.title}

    @api.post("/admin/hackathons")
    async def create_hackathon(
        user: AdminDep,
        title: Annotated[str, Form()],
        starts_at: Annotated[str, Form()],
        ends_at: Annotated[str, Form()],
        metric: Annotated[str, Form()],
        id_column: Annotated[str, Form()],
        target_column: Annotated[str, Form()],
        train: UploadFile = File(...),
        test: UploadFile = File(...),
        solution: UploadFile = File(...),
        baseline: UploadFile | None = File(None),
        task: UploadFile | None = File(None),
        description: Annotated[str, Form()] = "",
        daily_limit: Annotated[int, Form()] = 5,
    ):
        try:
            h = await hacks.create(
                title=title, description=description,
                starts_at=parse_dt(starts_at, "начало"), ends_at=parse_dt(ends_at, "окончание"),
                metric=metric, id_column=id_column, target_column=target_column, daily_limit=daily_limit,
                train=await upload(train), test=await upload(test), solution=await upload(solution),
                baseline=await upload(baseline), task=await upload(task),
            )
        except ServiceError as e:
            raise err(e)
        return await hacks.to_dict(h, user.id)

    @api.post("/admin/hackathons/{hid}/edit")
    async def edit_hackathon(
        hid: int,
        user: AdminDep,
        title: Annotated[str | None, Form()] = None,
        description: Annotated[str | None, Form()] = None,
        starts_at: Annotated[str | None, Form()] = None,
        ends_at: Annotated[str | None, Form()] = None,
        metric: Annotated[str | None, Form()] = None,
        id_column: Annotated[str | None, Form()] = None,
        target_column: Annotated[str | None, Form()] = None,
        daily_limit: Annotated[int | None, Form()] = None,
        is_active: Annotated[bool | None, Form()] = None,
        train: UploadFile | None = File(None),
        test: UploadFile | None = File(None),
        solution: UploadFile | None = File(None),
        baseline: UploadFile | None = File(None),
        task: UploadFile | None = File(None),
    ):
        try:
            h, rescored = await hacks.update(
                hid, title=title, description=description,
                starts_at=parse_dt(starts_at, "начало"), ends_at=parse_dt(ends_at, "окончание"),
                metric=metric or None, id_column=id_column or None, target_column=target_column or None,
                daily_limit=daily_limit, is_active=is_active,
                train=await upload(train), test=await upload(test), solution=await upload(solution),
                baseline=await upload(baseline), task=await upload(task),
            )
        except ServiceError as e:
            raise err(e)
        return await hacks.to_dict(h, user.id) | {"rescored": rescored}

    @api.get("/admin/hackathons/{hid}/submissions")
    async def hackathon_all_submissions(hid: int, user: AdminDep):
        return await hacks.all_submissions(hid)

    @api.get("/admin/participants")
    async def participants(user: AdminDep):
        assignments, rows = await service.participants()
        return {
            "assignments": [{"id": a.id, "title": a.title, "max_score": total_max(a)} for a in assignments],
            "participants": rows,
        }

    @api.get("/admin/submissions/{sid}/file")
    async def submission_file(sid: int, user: AdminDep):
        try:
            sub = await service.get_submission(sid)
        except ServiceError as e:
            raise HTTPException(404, str(e))
        if not sub.file_path:
            raise HTTPException(404, "У попытки теста нет файла")
        return FileResponse(sub.file_path, filename=sub.file_name or f"submission_{sid}.ipynb")

    return api


def build_app(service: HomeworkService, bot: Bot | None, lifespan=None) -> FastAPI:
    app = FastAPI(title="DSC Homework Bot", lifespan=lifespan)
    app.include_router(build_api(service, bot))

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index():
        # Версия в ссылках на css/js меняется при каждом изменении файлов, чтобы Telegram не держал старые
        version = str(max(int((STATIC / name).stat().st_mtime) for name in ("app.css", "app.js", "quiz.js")))
        html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__V__", version)
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app
