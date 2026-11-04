"""Telegram-бот (aiogram 3). Всё, что есть в мини аппе, доступно и командами."""

from __future__ import annotations

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
from app.formatting import feedback_messages, fmt_dt
from app.service import HomeworkService, ServiceError

log = logging.getLogger(__name__)

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
    async def notify(tg_id: int, messages: list[str]) -> None:
        for text in messages:
            await bot.send_message(tg_id, text)

    return notify


async def setup_bot_ui(bot: Bot, webapp_url: str) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="tasks", description="Домашние задания"),
            BotCommand(command="my", description="Мои сдачи и отзывы"),
            BotCommand(command="help", description="Как сдавать домашки"),
        ]
    )
    if webapp_url:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Домашки", web_app=WebAppInfo(url=webapp_url)))


def build_dispatcher(service: HomeworkService, webapp_url: str = "") -> Dispatcher:
    dp = Dispatcher()
    router = Router()
    dp.include_router(router)
    tz = service.settings.tz

    async def touch(user: TgUser) -> None:
        await service.upsert_user(user.id, user.username, user.full_name)

    def webapp_kb() -> InlineKeyboardMarkup | None:
        if not webapp_url:
            return None
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="📱 Открыть мини апп", web_app=WebAppInfo(url=webapp_url))]]
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
            "/my — твои сдачи и отзывы."
        )
        if service.is_admin(message.from_user.id):
            text += "\n\nТы организатор: /admin — управление заданиями."
        await message.answer(text, reply_markup=webapp_kb())

    @router.message(Command("help"))
    async def help_cmd(message: Message):
        await message.answer(
            "Как сдавать: /tasks → задание → «Сдать работу» → отправь .ipynb файлом.\n"
            "Можно просто прислать .ipynb, я спрошу, к какому заданию его отнести.\n"
            "Число попыток ограничено, засчитывается лучшая.\n/cancel — отменить текущее действие."
        )

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
        lines.append(f"Заданий в ноутбуке: {a.task_count}")
        lines.append(f"Попытки: {view.attempts_used} из {a.max_attempts}")
        if a.deadline:
            lines.append(f"Дедлайн: {fmt_dt(a.deadline, tz)}")
        if view.best_score is not None:
            lines.append(f"Лучший результат: {view.best_score}/{view.max_score}")
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📥 Скачать шаблон", callback_data=f"tpl:{aid}")],
                [InlineKeyboardButton(text="📤 Сдать работу", callback_data=f"sub:{aid}")],
                [InlineKeyboardButton(text="🗂 Мои попытки", callback_data=f"my:{aid}")],
            ]
        )
        await cb.message.answer("\n".join(lines), reply_markup=kb)
        await cb.answer()

    @router.callback_query(F.data.startswith("tpl:"))
    async def send_template(cb: CallbackQuery):
        aid = int(cb.data.split(":")[1])
        try:
            name, raw = await service.template_bytes(aid)
        except ServiceError as e:
            await cb.answer(str(e), show_alert=True)
            return
        await cb.message.answer_document(BufferedInputFile(raw, filename=name), caption="Шаблон задания")
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
        await message.answer(f"Принято! Работа #{sub.id} в очереди на проверку, отзыв придёт сюда.")

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
            f"Лимит попыток и дедлайн (время {escape(service.settings.timezone)}), например:\n"
            "<code>3 2026-11-20 23:59</code>\n"
            f"Только лимит: <code>3</code>. По умолчанию ({service.settings.default_max_attempts} попытки, без дедлайна): <code>-</code>"
        )

    @router.message(NewAssignment.settings, NOT_COMMAND)
    async def new_settings(message: Message, state: FSMContext, bot: Bot):
        text = message.text.strip()
        max_attempts, deadline = None, None
        if text != "-":
            parts = text.split(maxsplit=1)
            try:
                max_attempts = int(parts[0])
                if max_attempts < 1:
                    raise ValueError
                if len(parts) > 1:
                    deadline = datetime.strptime(parts[1].strip(), "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            except ValueError:
                await message.answer("Не понял формат. Пример: <code>3 2026-11-20 23:59</code> или <code>-</code>")
                return
        data = await state.get_data()
        tpl = (await bot.download(data["template_file_id"])).read()
        ref = (await bot.download(data["reference_file_id"])).read()
        try:
            a, warnings = await service.create_assignment(
                data["title"], data.get("description", ""), tpl, ref, max_attempts, deadline
            )
        except ServiceError as e:
            await state.clear()
            await message.answer(f"❌ {escape(str(e))}\nНачни заново: /new")
            return
        await state.clear()
        prepared = await service._get_prepared(a)
        lines = [f"✅ Задание #{a.id} «{escape(a.title)}» создано и открыто для сдачи.", "", "Найденные задания:"]
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
    async def loose_document(message: Message, state: FSMContext):
        await touch(message.from_user)
        name = message.document.file_name or ""
        if not name.lower().endswith(".ipynb"):
            await message.answer("Я принимаю только .ipynb файлы.")
            return
        views = await service.list_assignments(message.from_user.id)
        if not views:
            await message.answer("Сейчас нет открытых заданий.")
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
            lines.append(f"#{s.id} · {escape(s.assignment.title)} · {fmt_dt(s.created_at, tz)} · {score}")
            if s.status == "done":
                rows.append([InlineKeyboardButton(text=f"Отзыв #{s.id}", callback_data=f"res:{s.id}")])
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
            await message.answer(f"Участник: {escape(sub.user.display_name)} (id {sub.user_id})")
        if sub.status != "done":
            text = f"Статус: {STATUS_TEXT.get(sub.status, sub.status)}"
            if sub.error and admin_view:
                text += f"\n{escape(sub.error[:1000])}"
            await message.answer(text)
        else:
            attempt = await service.attempt_number(sub)
            for text in feedback_messages(sub.assignment.title, attempt, CheckResult.from_dict(sub.result)):
                await message.answer(text)
            if admin_view and sub.result and sub.result.get("warnings"):
                await message.answer("⚠️ " + escape("; ".join(sub.result["warnings"])))

    @router.callback_query(F.data.startswith("res:"))
    async def result_cb(cb: CallbackQuery):
        await send_result(cb.message, cb.from_user.id, int(cb.data.split(":")[1]))
        await cb.answer()

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
            "",
        ]
        rows = []
        for v in views:
            a = v.assignment
            lines.append(f"#{a.id} {escape(a.title)} — {'открыто' if a.is_active else 'закрыто'}")
            rows.append(
                [
                    InlineKeyboardButton(text=f"#{a.id} сдачи", callback_data=f"subs:{a.id}"),
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
        lines = [f"Сдачи по заданию #{aid} (последние 40):"]
        for s in subs[:40]:
            score = f"{s.score}/{s.max_score}" if s.status == "done" else STATUS_TEXT.get(s.status, s.status)
            lines.append(f"/sub {s.id} · {escape(s.user.display_name)} · {score}")
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

    return dp
