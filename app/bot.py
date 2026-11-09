"""Telegram-бот (aiogram 3). Всё, что есть в мини аппе, доступно и командами."""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime
from html import escape
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    User as TgUser,
    WebAppInfo,
)

from app.checker import CheckResult
from app.faq import FAQ, HELP_INTRO
from app.formatting import feedback_messages, fmt_dt, quiz_messages
from app.hackathons import hackathons_for
from app.service import HomeworkService, ServiceError, has_notebook, is_published, quiz_attempts, reference_available

log = logging.getLogger(__name__)

def queue_text(q: dict | None) -> str:
    """«Перед тобой 12 работ, примерно 8 мин» — только когда очередь заметная."""
    if not q or q["ahead"] < 3:
        return ""
    eta = f", примерно {max(1, round(q['eta_seconds'] / 60))} мин" if q.get("eta_seconds") else ""
    return f"\nПеред тобой в очереди {q['ahead']} работ{eta}."


STATUS_TEXT = {"queued": "⏳ в очереди", "checking": "🔎 проверяется", "done": "готово", "error": "⚠️ ошибка"}


class SubmitFlow(StatesGroup):
    waiting_file = State()


class NewAssignment(StatesGroup):
    title = State()
    description = State()
    template = State()
    reference = State()
    settings = State()


def build_bot(token: str, proxy: str = "") -> Bot:
    # proxy: http://host:port или socks5://user:pass@host:port — если api.telegram.org недоступен напрямую
    session = AiohttpSession(proxy=proxy) if proxy else None
    return Bot(token=token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def make_notifier(bot: Bot):
    async def notify(tg_id: int, messages: list) -> None:
        for msg in messages:
            if isinstance(msg, dict):  # {"text": ..., "buttons": [[[подпись, callback_data], ...], ...]}
                markup = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
                    for row in msg.get("buttons") or []
                ]) if msg.get("buttons") else None
                await bot.send_message(tg_id, msg["text"], reply_markup=markup)
            else:
                await bot.send_message(tg_id, msg)

    return notify


async def setup_bot_ui(bot: Bot, webapp_url: str) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="tasks", description="Домашние задания"),
            BotCommand(command="my", description="Мои сдачи и отзывы"),
            BotCommand(command="hackathon", description="Хакатон: условие, данные, лидерборд"),
            BotCommand(command="profile", description="Профиль: ник и ФИО"),
            BotCommand(command="help", description="Помощь и частые вопросы"),
        ]
    )
    if webapp_url:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Домашки", web_app=WebAppInfo(url=webapp_url)))


def build_dispatcher(service: HomeworkService, webapp_url: str = "") -> Dispatcher:
    dp = Dispatcher()
    router = Router()
    dp.include_router(router)
    tz = service.settings.tz
    hacks = hackathons_for(service)

    async def touch(user: TgUser) -> None:
        await service.upsert_user(user.id, user.username, user.full_name)

    def webapp_kb() -> InlineKeyboardMarkup | None:
        url = service.webapp_url or webapp_url
        if not url:
            return None
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="📱 Открыть мини апп", web_app=WebAppInfo(url=url))]]
        )

    def is_admin(m: Message | CallbackQuery) -> bool:
        return service.is_admin(m.from_user.id)

    # ---------- общие команды ----------

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext):
        await state.clear()
        await touch(message.from_user)
        text = (
            "Привет! Я проверяю домашки «Месяца бота».\n\n"
            "1. /tasks — выбери задание и скачай шаблон ноутбука.\n"
            "2. Допиши код в ячейках с заданиями.\n"
            "3. Нажми «Сдать работу» и пришли .ipynb файлом.\n"
            "4. Через пару минут придёт оценка и отзыв по коду.\n\n"
            "/my — твои сдачи и отзывы.\n/hackathon — хакатоны.\n/profile — ник и ФИО.\n/help — частые вопросы: VPN, профиль, попытки."
        )
        user = await service.get_user(message.from_user.id)
        if not user.fio and not service.is_admin(message.from_user.id):
            text += ("\n\n❗ Укажи ФИО, чтобы организаторы засчитали твои домашки:\n"
                     "<code>/fio Иванов Иван Иванович</code>")
        if service.is_admin(message.from_user.id):
            text += "\n\nТы организатор: /admin — управление заданиями."
        await message.answer(text, reply_markup=webapp_kb())

    # ---------- профиль ----------

    def profile_text(u) -> str:
        return (
            "<b>Профиль</b>\n"
            f"Ник в боте: {escape(u.nickname or '— (используется имя из Telegram)')}\n"
            f"ФИО: {escape(u.fio or '— не указано')}\n"
            f"Telegram: {escape(u.full_name)}{' (@' + escape(u.username) + ')' if u.username else ''}\n\n"
            "Изменить:\n<code>/name Новый ник</code>\n<code>/fio Фамилия Имя Отчество</code>"
        )

    @router.message(Command("profile"))
    async def profile_cmd(message: Message):
        await touch(message.from_user)
        await message.answer(profile_text(await service.get_user(message.from_user.id)))

    @router.message(Command("name"))
    async def name_cmd(message: Message, command: CommandObject):
        await touch(message.from_user)
        if not command.args:
            await message.answer("Напиши ник после команды: <code>/name Мой ник</code>")
            return
        try:
            u = await service.update_profile(message.from_user.id, nickname=command.args)
        except ServiceError as e:
            await message.answer(f"❌ {escape(str(e))}")
            return
        await message.answer(f"Готово, ник: {escape(u.nickname)}")

    @router.message(Command("fio"))
    async def fio_cmd(message: Message, command: CommandObject):
        await touch(message.from_user)
        if not command.args:
            await message.answer("Напиши ФИО после команды: <code>/fio Иванов Иван Иванович</code>")
            return
        try:
            u = await service.update_profile(message.from_user.id, fio=command.args)
        except ServiceError as e:
            await message.answer(f"❌ {escape(str(e))}")
            return
        await message.answer(f"Готово, ФИО: {escape(u.fio)}")

    def faq_kb() -> InlineKeyboardMarkup:
        items = list(FAQ.items())
        rows = [[InlineKeyboardButton(text=label, callback_data=f"faq:{key}")] for key, (label, _) in items]
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @router.message(Command("help"))
    async def help_cmd(message: Message):
        await touch(message.from_user)
        await message.answer(HELP_INTRO, reply_markup=faq_kb())

    @router.callback_query(F.data.startswith("faq:"))
    async def faq_answer(cb: CallbackQuery):
        key = cb.data.split(":", 1)[1]
        back = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← Все вопросы", callback_data="faq:")]])
        if key in FAQ:
            text, markup = FAQ[key][1], back
        else:  # «← Все вопросы» или устаревшая кнопка
            text, markup = HELP_INTRO, faq_kb()
        with contextlib.suppress(Exception):  # «message is not modified» при повторном нажатии
            await cb.message.edit_text(text, reply_markup=markup)
        await cb.answer()

    @router.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("Отменено.")

    # ---------- задания ----------

    async def assignments_kb(user_id: int, prefix: str = "a") -> InlineKeyboardMarkup | None:
        views = await service.list_assignments(user_id)
        if not views:
            return None
        rows = []
        for v in views:
            best = f" · {v.best_score}/{v.max_score}" if v.best_score is not None else ""
            rows.append([InlineKeyboardButton(text=f"{v.assignment.title}{best}", callback_data=f"{prefix}:{v.assignment.id}")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @router.message(Command("tasks"))
    async def tasks_cmd(message: Message):
        await touch(message.from_user)
        kb = await assignments_kb(message.from_user.id)
        if kb is None:
            await message.answer("Пока нет открытых заданий.")
            return
        await message.answer("Выбери задание:", reply_markup=kb)

    @router.callback_query(F.data.startswith("a:"))
    async def assignment_card(cb: CallbackQuery):
        aid = int(cb.data.split(":")[1])
        views = await service.list_assignments(cb.from_user.id, include_inactive=is_admin(cb))
        view = next((v for v in views if v.assignment.id == aid), None)
        if view is None:
            await cb.answer("Задание не найдено", show_alert=True)
            return
        a = view.assignment
        lines = [f"<b>{escape(a.title)}</b>"]
        if a.description:
            lines.append(escape(a.description))
        nb = has_notebook(a)
        if a.quiz:
            lines.append(f"🧩 Тест: {len(a.quiz['questions'])} вопр., попытки {view.quiz_attempts_used} из {quiz_attempts(a)}"
                         " — проходится в мини аппе")
        if nb:
            lines.append(f"📓 Заданий в ноутбуке: {a.task_count}, попытки {view.attempts_used} из {a.max_attempts}")
        if a.deadline:
            lines.append(f"Дедлайн: {fmt_dt(a.deadline, tz)} (МСК)")
        if not is_published(a):
            lines.append(f"🕒 Участники увидят задание {fmt_dt(a.publish_at, tz)} (МСК)")
        if view.best_score is not None:
            lines.append(f"Лучший результат: {view.best_score}/{view.max_score}")
        rows = []
        if a.quiz and service.webapp_url:
            rows.append([InlineKeyboardButton(text="🧩 Пройти тест", web_app=WebAppInfo(url=service.webapp_url))])
        if nb:
            rows += [
                [InlineKeyboardButton(text="📥 Скачать шаблон", callback_data=f"tpl:{aid}")],
                [InlineKeyboardButton(text="📤 Сдать работу", callback_data=f"sub:{aid}")],
            ]
        rows.append([InlineKeyboardButton(text="🗂 Мои попытки", callback_data=f"my:{aid}")])
        if reference_available(a) and nb:
            lines.append("Дедлайн прошёл: можно сравнить своё решение с эталоном.")
            rows.append([InlineKeyboardButton(text="📗 Эталонное решение", callback_data=f"ref:{aid}")])
        elif a.deadline and nb:
            lines.append("Эталон откроется после дедлайна.")
        kb = InlineKeyboardMarkup(inline_keyboard=rows)
        await cb.message.answer("\n".join(lines), reply_markup=kb)
        await cb.answer()

    @router.callback_query(F.data.startswith("tpl:"))
    async def send_template(cb: CallbackQuery):
        aid = int(cb.data.split(":")[1])
        try:
            name, raw = await service.template_bytes(aid, cb.from_user.id)
        except ServiceError as e:
            await cb.answer(str(e), show_alert=True)
            return
        await cb.message.answer_document(BufferedInputFile(raw, filename=name), caption="Шаблон задания")
        await cb.answer()

    @router.callback_query(F.data.startswith("ref:"))
    async def send_reference(cb: CallbackQuery):
        aid = int(cb.data.split(":")[1])
        try:
            name, raw = await service.reference_bytes(aid, cb.from_user.id)
        except ServiceError as e:
            await cb.answer(str(e), show_alert=True)
            return
        await cb.message.answer_document(
            BufferedInputFile(raw, filename=name),
            caption="Эталонное решение. Сравнить по заданиям со своим кодом удобнее в мини аппе: Мои сдачи → попытка.",
        )
        await cb.answer()

    @router.callback_query(F.data.startswith("sub:"))
    async def ask_file(cb: CallbackQuery, state: FSMContext):
        aid = int(cb.data.split(":")[1])
        await state.set_state(SubmitFlow.waiting_file)
        await state.update_data(assignment_id=aid)
        await cb.message.answer("Пришли свой .ipynb файлом (как документ). /cancel — отмена.")
        await cb.answer()

    async def do_submit(message: Message, user_id: int, aid: int, raw: bytes, file_name: str) -> None:
        try:
            sub = await service.submit(user_id, aid, raw, file_name)
        except ServiceError as e:
            await message.answer(f"❌ {escape(str(e))}")
            return
        attempt = await service.attempt_number(sub)
        await message.answer(f"Принято! Попытка {attempt} в очереди на проверку, отзыв придёт сюда.{queue_text(await service.queue_info(sub.id))}")

    @router.message(SubmitFlow.waiting_file, F.document)
    async def got_file(message: Message, state: FSMContext, bot: Bot):
        await touch(message.from_user)
        data = await state.get_data()
        await state.clear()
        raw = (await bot.download(message.document)).read()
        await do_submit(message, message.from_user.id, data["assignment_id"], raw, message.document.file_name or "")

    NOT_COMMAND = F.text & ~F.text.startswith("/")

    @router.message(SubmitFlow.waiting_file, NOT_COMMAND)
    async def waiting_not_file(message: Message):
        await message.answer("Жду .ipynb файлом. /cancel — отмена.")

    # ---------- создание задания (организаторы) ----------
    # Эти хендлеры должны стоять раньше общего хендлера документов.

    @router.message(Command("new"))
    async def new_start(message: Message, state: FSMContext):
        if not is_admin(message):
            return
        await state.clear()
        await state.set_state(NewAssignment.title)
        await message.answer("Новое задание. Как назовём? (/cancel — отмена)")

    @router.message(NewAssignment.title, NOT_COMMAND)
    async def new_title(message: Message, state: FSMContext):
        await state.update_data(title=message.text.strip())
        await state.set_state(NewAssignment.description)
        await message.answer("Короткое описание для участников (или «-», чтобы пропустить):")

    @router.message(NewAssignment.description, NOT_COMMAND)
    async def new_description(message: Message, state: FSMContext):
        desc = "" if message.text.strip() == "-" else message.text.strip()
        await state.update_data(description=desc)
        await state.set_state(NewAssignment.template)
        await message.answer("Пришли ноутбук-<b>шаблон</b> для участников (.ipynb).")

    @router.message(NewAssignment.template, F.document)
    async def new_template(message: Message, state: FSMContext, bot: Bot):
        await state.update_data(template_file_id=message.document.file_id)
        await state.set_state(NewAssignment.reference)
        await message.answer("Теперь пришли <b>эталонное</b> решение (.ipynb).")

    @router.message(NewAssignment.reference, F.document)
    async def new_reference(message: Message, state: FSMContext):
        await state.update_data(reference_file_id=message.document.file_id)
        await state.set_state(NewAssignment.settings)
        await message.answer(
            "Лимит попыток, дедлайн и (необязательно) время публикации, всё по МСК. Например:\n"
            "<code>3 2026-11-20 23:59</code> — открыть сразу\n"
            "<code>3 2026-11-20 23:59 2026-11-10 10:00</code> — участники увидят задание 10.11 в 10:00 "
            "и получат уведомление\n"
            f"Только лимит: <code>3</code>. По умолчанию ({service.settings.default_max_attempts} попытки, без дедлайна): <code>-</code>"
        )

    @router.message(NewAssignment.settings, NOT_COMMAND)
    async def new_settings(message: Message, state: FSMContext, bot: Bot):
        text = message.text.strip()
        max_attempts, deadline, publish_at = None, None, None
        if text != "-":
            parts = text.split()
            try:
                if len(parts) not in (1, 3, 5):
                    raise ValueError
                max_attempts = int(parts[0])
                if max_attempts < 1:
                    raise ValueError
                if len(parts) >= 3:
                    deadline = datetime.strptime(" ".join(parts[1:3]), "%Y-%m-%d %H:%M").replace(tzinfo=tz)
                if len(parts) == 5:
                    publish_at = datetime.strptime(" ".join(parts[3:5]), "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            except ValueError:
                await message.answer("Не понял формат. Пример: <code>3 2026-11-20 23:59</code> или <code>-</code>")
                return
        data = await state.get_data()
        tpl = (await bot.download(data["template_file_id"])).read()
        ref = (await bot.download(data["reference_file_id"])).read()
        try:
            a, warnings = await service.create_assignment(
                data["title"], data.get("description", ""), tpl, ref, max_attempts, deadline, publish_at
            )
        except ServiceError as e:
            await state.clear()
            await message.answer(f"❌ {escape(str(e))}\nНачни заново: /new")
            return
        await state.clear()
        prepared = await service._get_prepared(a)
        if publish_at:
            head = (f"✅ Задание «{escape(a.title)}» создано (id {a.id}). Участники увидят его "
                    f"{fmt_dt(publish_at, tz)} (МСК) и получат уведомление.")
        else:
            head = f"✅ Задание «{escape(a.title)}» создано (id {a.id}) и открыто для сдачи. Участникам ушло уведомление."
        lines = [head, "", "Найденные задания:"]
        for t in prepared.tasks:
            lines.append(f"{t.number}. {escape(t.title)}")
        if warnings:
            lines.append("")
            lines.append("⚠️ Проверь:")
            lines += [f"• {escape(w)}" for w in warnings]
        await message.answer("\n".join(lines))

    @router.message(NewAssignment.template, NOT_COMMAND)
    @router.message(NewAssignment.reference, NOT_COMMAND)
    async def new_need_file(message: Message):
        await message.answer("Жду .ipynb файлом. /cancel — отмена.")

    # ---------- документ без контекста ----------

    @router.message(F.document)
    async def loose_document(message: Message, state: FSMContext, bot: Bot):
        await touch(message.from_user)
        name = message.document.file_name or ""
        if name.lower().endswith(".csv"):
            await hackathon_csv(message, bot)
            return
        if not name.lower().endswith(".ipynb"):
            await message.answer("Я принимаю .ipynb (домашки) и .csv (решения хакатона).")
            return
        views = [v for v in await service.list_assignments(message.from_user.id) if has_notebook(v.assignment)]
        if not views:
            await message.answer("Сейчас нет открытых заданий с ноутбуком.")
            return
        await state.update_data(pending_file_id=message.document.file_id, pending_file_name=name)
        rows = [
            [InlineKeyboardButton(text=v.assignment.title, callback_data=f"subf:{v.assignment.id}")] for v in views
        ]
        await message.answer("К какому заданию относится этот файл?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @router.callback_query(F.data.startswith("subf:"))
    async def submit_pending(cb: CallbackQuery, state: FSMContext, bot: Bot):
        aid = int(cb.data.split(":")[1])
        data = await state.get_data()
        file_id = data.get("pending_file_id")
        if not file_id:
            await cb.answer("Пришли файл ещё раз", show_alert=True)
            return
        await state.update_data(pending_file_id=None)
        raw = (await bot.download(file_id)).read()
        await do_submit(cb.message, cb.from_user.id, aid, raw, data.get("pending_file_name", ""))
        await cb.answer()

    # ---------- мои сдачи ----------

    async def send_my(message: Message, user_id: int, aid: int | None):
        subs = await service.list_user_submissions(user_id, aid)
        if not subs:
            await message.answer("Сдач пока нет.")
            return
        rows, lines = [], []
        for s in subs[:15]:
            score = f"{s.score}/{s.max_score}" if s.status == "done" else STATUS_TEXT.get(s.status, s.status)
            attempt = f"попытка {s.attempt}" if s.attempt else "не засчитана"
            part = "тест · " if s.kind == "quiz" else ""
            lines.append(f"{escape(s.assignment.title)} · {part}{attempt} · {fmt_dt(s.created_at, tz)} · {score}")
            if s.status == "done":
                label = "Тест" if s.kind == "quiz" else "Отзыв"
                rows.append([InlineKeyboardButton(text=f"{label}: {s.assignment.title[:40]} · попытка {s.attempt}",
                                                  callback_data=f"res:{s.id}")])
        await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)

    @router.message(Command("my"))
    async def my_cmd(message: Message):
        await touch(message.from_user)
        await send_my(message, message.from_user.id, None)

    @router.callback_query(F.data.startswith("my:"))
    async def my_cb(cb: CallbackQuery):
        await send_my(cb.message, cb.from_user.id, int(cb.data.split(":")[1]))
        await cb.answer()

    async def send_result(message: Message, viewer_id: int, sid: int, admin_view: bool = False):
        try:
            sub = await service.get_submission(sid)
        except ServiceError as e:
            await message.answer(escape(str(e)))
            return
        if sub.user_id != viewer_id and not service.is_admin(viewer_id):
            await message.answer("Это не твоя сдача.")
            return
        if admin_view:
            await message.answer(
                f"Сдача id {sub.id} · попытка {sub.attempt or '— (ошибка проверки)'}\n"
                f"Участник: {escape(sub.user.admin_label)} (tg id {sub.user_id})"
            )
        if sub.status != "done":
            text = f"Статус: {STATUS_TEXT.get(sub.status, sub.status)}"
            if sub.error and admin_view:
                text += f"\n{escape(sub.error[:1000])}"
            await message.answer(text)
        elif sub.kind == "quiz":
            reveal = admin_view or reference_available(sub.assignment)
            for text in quiz_messages(sub.assignment.title, sub.attempt, sub.result, sub.organizer_comment, reveal):
                await message.answer(text)
        else:
            for text in feedback_messages(
                sub.assignment.title, sub.attempt, CheckResult.from_dict(sub.result), sub.organizer_comment
            ):
                await message.answer(text)
            if admin_view and sub.result and sub.result.get("warnings"):
                await message.answer("⚠️ " + escape("; ".join(sub.result["warnings"])))

    @router.callback_query(F.data.startswith("res:"))
    async def result_cb(cb: CallbackQuery):
        await send_result(cb.message, cb.from_user.id, int(cb.data.split(":")[1]))
        await cb.answer()

    # ---------- хакатоны ----------

    def fmt_score(v: float | None) -> str:
        return "—" if v is None else f"{v:.5g}"

    async def current_hackathon(user_id: int) -> dict | None:
        """Идущий хакатон, иначе ближайший предстоящий, иначе последний прошедший."""
        items = await hacks.list_all(user_id)
        items = [h for h in items if h["is_active"]]
        for st in ("running", "upcoming"):
            found = [h for h in items if h["status"] == st]
            if found:
                return found[0]
        return items[-1] if items else None

    @router.message(Command("hackathon"))
    async def hackathon_cmd(message: Message):
        await touch(message.from_user)
        h = await current_hackathon(message.from_user.id)
        if h is None:
            await message.answer("Ближайших хакатонов пока нет. Как только организаторы его объявят, я напишу.")
            return
        start, end = fmt_dt(datetime.fromisoformat(h["starts_at"]), tz), fmt_dt(datetime.fromisoformat(h["ends_at"]), tz)
        if h["status"] == "upcoming":
            await message.answer(
                f"🏆 Ближайший хакатон <b>{escape(h['title'])}</b> пройдёт с {start} по {end} (МСК).\n"
                "Данные и условие появятся здесь в момент старта."
            )
            return
        lines = [f"🏆 <b>{escape(h['title'])}</b>",
                 f"{'Идёт' if h['status'] == 'running' else 'Завершён'}: с {start} по {end} (МСК)",
                 f"Метрика: {escape(h['metric_title'])} ({'больше' if h['higher_is_better'] else 'меньше'} — лучше)"]
        if h["description"]:
            desc = h["description"]
            lines += ["", escape(desc[:2500] + ("…" if len(desc) > 2500 else ""))]
        if h["status"] == "running":
            lines += ["", f"Формат ответа: CSV с колонками <code>{escape(h['id_column'])}</code> и "
                          f"<code>{escape(h['target_column'])}</code>, строка на каждый id из test. "
                          "Пришли файл .csv прямо в этот чат.",
                      f"Сегодня использовано {h['used_today']} из {h['daily_limit']} попыток."]
        if h.get("team_mode"):
            team = h.get("team")
            if team:
                lines.append(f"👥 Твоя команда: <b>{escape(team['name'])}</b> ({team['size']} из {h['team_max']})"
                             + (" — ты капитан" if team["is_captain"] else ""))
            elif h["status"] != "finished":
                lines.append(f"👥 Хакатон командный (до {h['team_max']} человек). Ты пока без команды — "
                             "создай свою, попросись в чужую или выбери «Нет команды» в мини аппе, во вкладке «Хакатоны».")
        if h["my_best"] is not None:
            lines.append(f"{'Лучший результат команды' if h.get('team') else 'Твой лучший результат'}: {fmt_score(h['my_best'])}")
        names = {"train": "📊 train", "test": "🧪 test", "baseline": "🧩 baseline", "task": "📄 условие"}
        btns = [InlineKeyboardButton(text=names[k], callback_data=f"hf:{h['id']}:{k}") for k in names if k in h["files"]]
        rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
        rows.append([InlineKeyboardButton(text="🏅 Лидерборд", callback_data=f"hlb:{h['id']}"),
                     InlineKeyboardButton(text="🗂 Мои решения", callback_data=f"hmy:{h['id']}")])
        await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @router.callback_query(F.data.startswith("hf:"))
    async def hackathon_file_cb(cb: CallbackQuery):
        _, hid, kind = cb.data.split(":")
        try:
            name, raw = await hacks.file(int(hid), kind, cb.from_user.id)
        except ServiceError as e:
            await cb.answer(str(e), show_alert=True)
            return
        await cb.message.answer_document(BufferedInputFile(raw, filename=name))
        await cb.answer()

    @router.callback_query(F.data.startswith("hlb:"))
    async def hackathon_lb_cb(cb: CallbackQuery):
        hid = int(cb.data.split(":")[1])
        table = await hacks.leaderboard(hid, cb.from_user.id)
        if not table:
            await cb.answer("Пока нет ни одного решения", show_alert=True)
            return
        lines = ["🏅 <b>Лидерборд</b> (место · участник · результат · попыток)"]
        for r in table[:20]:
            me = " ← ты" if r["is_me"] else ""
            lines.append(f"{r['place']}. {escape(r['name'])} · {fmt_score(r['score'])} · {r['attempts']}{me}")
        mine = next((r for r in table[20:] if r["is_me"]), None)
        if mine:
            lines.append(f"…\n{mine['place']}. {escape(mine['name'])} · {fmt_score(mine['score'])} · {mine['attempts']} ← ты")
        await cb.message.answer("\n".join(lines))
        await cb.answer()

    @router.callback_query(F.data.startswith("hmy:"))
    async def hackathon_my_cb(cb: CallbackQuery):
        hid = int(cb.data.split(":")[1])
        subs = await hacks.my_submissions(cb.from_user.id, hid)
        if not subs:
            await cb.answer("Решений пока нет", show_alert=True)
            return
        lines = ["🗂 <b>Мои решения</b>"]
        for x in subs[:20]:
            res = fmt_score(x["score"]) if x["status"] == "done" else "ошибка: " + escape((x["error"] or "")[:150])
            lines.append(f"№{x['number']} · {fmt_dt(datetime.fromisoformat(x['created_at']), tz)} · {res}")
        await cb.message.answer("\n".join(lines))
        await cb.answer()

    @router.callback_query(F.data.startswith("treq:"))
    async def team_request_cb(cb: CallbackQuery):
        """Кнопки «Принять / Отклонить» под приглашением в команду или заявкой капитану."""
        _, action, rid = cb.data.split(":")
        try:
            r = await hacks.teams.decide(int(rid), cb.from_user.id, action)
        except ServiceError as e:
            await cb.answer(str(e), show_alert=True)
            with contextlib.suppress(Exception):
                await cb.message.edit_reply_markup(reply_markup=None)
            return
        if r["kind"] == "invite":
            note = (f"✅ Ты в команде «{escape(r['team_name'])}». Состав и лидерборд — в мини аппе."
                    if r["status"] == "accepted" else "Приглашение отклонено.")
        else:
            note = (f"✅ {escape(r['user_name'])} теперь в команде." if r["status"] == "accepted"
                    else f"Заявка от {escape(r['user_name'])} отклонена.")
        with contextlib.suppress(Exception):
            await cb.message.edit_text(f"{cb.message.html_text}\n\n{note}", reply_markup=None)
        await cb.answer("Готово")

    async def hackathon_csv(message: Message, bot: Bot) -> None:
        h = await current_hackathon(message.from_user.id)
        if h is None or h["status"] != "running":
            await message.answer("Сейчас нет идущего хакатона, .csv принимать некуда. /hackathon — подробности.")
            return
        raw = (await bot.download(message.document)).read()
        try:
            sub = await hacks.submit(message.from_user.id, h["id"], raw, message.document.file_name or "")
        except ServiceError as e:
            await message.answer(f"❌ {escape(str(e))}")
            return
        mine = await hacks.my_submissions(message.from_user.id, h["id"])
        number = next(x["number"] for x in mine if x["id"] == sub.id)
        if sub.status == "error":
            await message.answer(f"⚠️ Решение №{number} не принято (попытка не списана):\n{escape(sub.error or '')}")
            return
        table = await hacks.leaderboard(h["id"], message.from_user.id)
        place = next((r["place"] for r in table if r["is_me"]), None)
        await message.answer(
            f"✅ Решение №{number}: {escape(h['metric_title'])} = <b>{fmt_score(sub.score)}</b>\n"
            f"Твоё место в лидерборде: {place} из {len(table)}"
        )

    # ---------- админка ----------

    @router.message(Command("admin"))
    async def admin_cmd(message: Message):
        if not is_admin(message):
            return
        views = await service.list_assignments(message.from_user.id, include_inactive=True)
        lines = [
            "<b>Организаторам</b>",
            "/new — создать задание (шаблон + эталон)",
            "/sub &lt;id&gt; — отзыв и файл конкретной сдачи",
            "/recheck &lt;id&gt; — перепроверить сдачу",
            "/score &lt;id&gt; &lt;задание&gt;=&lt;балл&gt; … — изменить баллы, например <code>/score 12 2=8 3=10</code>",
            "/comment &lt;id&gt; &lt;текст&gt; — комментарий участнику от организатора",
            "/webapp &lt;https://адрес&gt; — сменить адрес мини аппа (после перезапуска туннеля)",
            "Править задания и создавать хакатоны — в мини аппе (вкладка «Организаторам»).",
            "",
        ]
        rows = []
        for v in views:
            a = v.assignment
            state_txt = "открыто" if a.is_active else "закрыто"
            if a.is_active and not is_published(a):
                state_txt = f"появится {fmt_dt(a.publish_at, tz)} (МСК)"
            lines.append(f"id {a.id} · {escape(a.title)} — {state_txt}")
            rows.append(
                [
                    InlineKeyboardButton(text=f"id {a.id}: сдачи", callback_data=f"subs:{a.id}"),
                    InlineKeyboardButton(text="CSV", callback_data=f"csv:{a.id}"),
                    InlineKeyboardButton(text="Закрыть" if a.is_active else "Открыть", callback_data=f"tog:{a.id}"),
                ]
            )
        if not views:
            lines.append("Заданий пока нет.")
        await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)

    @router.callback_query(F.data.startswith("tog:"))
    async def toggle_cb(cb: CallbackQuery):
        if not is_admin(cb):
            return
        aid = int(cb.data.split(":")[1])
        a = await service.get_assignment(aid)
        a = await service.set_active(aid, not a.is_active)
        await cb.answer(f"«{a.title}»: {'открыто' if a.is_active else 'закрыто'}", show_alert=True)

    @router.callback_query(F.data.startswith("csv:"))
    async def csv_cb(cb: CallbackQuery):
        if not is_admin(cb):
            return
        aid = int(cb.data.split(":")[1])
        data = await service.export_csv(aid)
        await cb.message.answer_document(
            BufferedInputFile(data.encode("utf-8-sig"), filename=f"results_{aid}.csv"), caption="Лучший результат каждого участника"
        )
        await cb.answer()

    @router.callback_query(F.data.startswith("subs:"))
    async def subs_cb(cb: CallbackQuery):
        if not is_admin(cb):
            return
        aid = int(cb.data.split(":")[1])
        subs = await service.list_assignment_submissions(aid)
        if not subs:
            await cb.answer("Сдач пока нет", show_alert=True)
            return
        lines = [f"Сдачи по заданию id {aid} (последние 40). /sub &lt;id&gt; — подробно:"]
        for s in subs[:40]:
            score = f"{s.score}/{s.max_score}" if s.status == "done" else STATUS_TEXT.get(s.status, s.status)
            attempt = f"№{s.attempt}" if s.attempt else "№—"
            part = " · тест" if s.kind == "quiz" else ""
            lines.append(f"/sub {s.id}{part} · {attempt} · {escape(s.user.admin_label)} · {score}")
        await cb.message.answer("\n".join(lines))
        await cb.answer()

    @router.message(Command("sub"))
    async def sub_cmd(message: Message, command: CommandObject):
        if not is_admin(message):
            return
        if not command.args or not command.args.strip().isdigit():
            await message.answer("Использование: /sub &lt;id&gt;")
            return
        sid = int(command.args.strip())
        await send_result(message, message.from_user.id, sid, admin_view=True)
        try:
            sub = await service.get_submission(sid)
            if sub.kind == "quiz":
                return
            await message.answer_document(
                BufferedInputFile(Path(sub.file_path).read_bytes(), filename=sub.file_name or f"submission_{sid}.ipynb")
            )
        except (ServiceError, OSError):
            pass

    @router.message(Command("recheck"))
    async def recheck_cmd(message: Message, command: CommandObject):
        if not is_admin(message):
            return
        if not command.args or not command.args.strip().isdigit():
            await message.answer("Использование: /recheck &lt;id&gt;")
            return
        try:
            await service.recheck(int(command.args.strip()))
        except ServiceError as e:
            await message.answer(escape(str(e)))
            return
        await message.answer("Отправил на перепроверку, результат придёт участнику.")

    @router.message(Command("webapp"))
    async def webapp_cmd(message: Message, command: CommandObject, bot: Bot):
        if not is_admin(message):
            return
        if not command.args:
            await message.answer(
                f"Сейчас мини апп открывается по адресу:\n{escape(service.webapp_url or '— не задан')}\n\n"
                "Сменить (например, после перезапуска туннеля): <code>/webapp https://новый-адрес</code>"
            )
            return
        try:
            url = service.set_webapp_url(command.args)
        except ServiceError as e:
            await message.answer(f"❌ {escape(str(e))}")
            return
        try:
            await setup_bot_ui(bot, url)
        except Exception:  # noqa: BLE001
            log.exception("Не удалось обновить кнопку меню")
        await message.answer(
            f"Готово, мини апп теперь открывается по адресу {escape(url)}.\n"
            "Кнопка «Домашки» в меню уже обновлена. Кнопки в старых сообщениях ведут на старый адрес: "
            "нажми /start, чтобы получить новую.",
            reply_markup=webapp_kb(),
        )

    @router.message(Command("score"))
    async def score_cmd(message: Message, command: CommandObject):
        if not is_admin(message):
            return
        parts = (command.args or "").split()
        usage = ("Использование: <code>/score &lt;id сдачи&gt; 1=8 2=10</code>\n"
                 "Для попытки теста номера — это вопросы, баллы от 0 до веса вопроса.")
        if len(parts) < 2 or not parts[0].isdigit():
            await message.answer(usage)
            return
        scores = {}
        for p in parts[1:]:
            k, _, v = p.partition("=")
            if not (k.isdigit() and v.isdigit()):
                await message.answer(usage)
                return
            scores[int(k)] = int(v)
        try:
            sub = await service.review(int(parts[0]), task_scores=scores, reviewer_id=message.from_user.id)
        except ServiceError as e:
            await message.answer(f"❌ {escape(str(e))}")
            return
        await message.answer(f"Готово: {sub.score}/{sub.max_score}. Участник получил уведомление.")

    @router.message(Command("comment"))
    async def comment_cmd(message: Message, command: CommandObject):
        if not is_admin(message):
            return
        sid, _, text = (command.args or "").strip().partition(" ")
        if not sid.isdigit() or not text.strip():
            await message.answer("Использование: <code>/comment &lt;id сдачи&gt; текст комментария</code>")
            return
        try:
            await service.review(int(sid), comment=text, reviewer_id=message.from_user.id)
        except ServiceError as e:
            await message.answer(f"❌ {escape(str(e))}")
            return
        await message.answer("Комментарий отправлен участнику.")

    return dp
