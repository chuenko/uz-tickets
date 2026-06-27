"""Telegram-бот: повний інлайн-інтерфейс (без Mini App).

Додавання маршруту через пошук станцій, список, статус, пауза/видалення.
Маршрути зберігаються у SQLite (storage), сповіщення шле monitor.
"""
import logging
from datetime import datetime, timedelta

from aiogram import Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
)

from . import config, storage
from .uz_client import search_stations

log = logging.getLogger(__name__)


class AddRoute(StatesGroup):
    from_search = State()
    from_pick = State()
    to_search = State()
    to_pick = State()
    date = State()


# ── Клавіатури ────────────────────────────────
def kb_main(has_routes: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="➕ Додати маршрут", callback_data="add_route")]]
    if has_routes:
        rows.append([InlineKeyboardButton(text="📋 Мої маршрути", callback_data="list_routes")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_stations(stations: list[dict], prefix: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=st["name"], callback_data=f"pick_{prefix}:{st['id']}:{st['name'][:28]}")]
            for st in stations]
    rows.append([InlineKeyboardButton(text="🔄 Шукати знову", callback_data=f"retry_{prefix}")])
    rows.append([InlineKeyboardButton(text="❌ Скасувати", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_dates() -> InlineKeyboardMarkup:
    today = datetime.now()
    rows, row = [], []
    for i in range(1, 22):
        d = today + timedelta(days=i)
        row.append(InlineKeyboardButton(text=d.strftime("%d.%m"), callback_data=f"date:{d:%Y-%m-%d}"))
        if len(row) == 4:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="❌ Скасувати", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_routes(routes: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for r in routes:
        dot = "🟢" if r["active"] else "🔴"
        rows.append([InlineKeyboardButton(
            text=f"{dot} {r['from_name']}→{r['to_name']} {r['date']}",
            callback_data=f"route:{r['key']}")])
    rows.append([InlineKeyboardButton(text="🔙 Меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_route_actions(key: str, active: bool) -> InlineKeyboardMarkup:
    toggle = ("⏸ Пауза", f"pause:{key}") if active else ("▶️ Увімкнути", f"resume:{key}")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Статус зараз", callback_data=f"status:{key}")],
        [InlineKeyboardButton(text=toggle[0], callback_data=toggle[1])],
        [InlineKeyboardButton(text="🗑 Видалити", callback_data=f"del:{key}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="list_routes")],
    ])


# ── Хендлери ──────────────────────────────────
def setup_handlers(dp: Dispatcher, monitor):

    @dp.message(Command("start"))
    async def cmd_start(msg: Message, state: FSMContext):
        await state.clear()
        await msg.answer(
            "👋 <b>UZ Tickets Monitor</b>\n\n"
            "Стежу за появою місць на потрібні рейси й одразу пінгую.\n"
            "Натисни <b>Додати маршрут</b>.",
            parse_mode="HTML",
            reply_markup=kb_main(bool(storage.list_routes(msg.chat.id))),
        )

    @dp.callback_query(F.data == "main_menu")
    async def cb_menu(cb: CallbackQuery, state: FSMContext):
        await state.clear()
        await cb.message.edit_text("Головне меню:", reply_markup=kb_main(bool(storage.list_routes(cb.message.chat.id))))

    @dp.callback_query(F.data == "cancel")
    async def cb_cancel(cb: CallbackQuery, state: FSMContext):
        await state.clear()
        await cb.message.edit_text("Скасовано.", reply_markup=kb_main(bool(storage.list_routes(cb.message.chat.id))))

    # ── Додавання ──
    @dp.callback_query(F.data == "add_route")
    async def cb_add(cb: CallbackQuery, state: FSMContext):
        if storage.count_routes(cb.message.chat.id) >= config.MAX_ROUTES:
            await cb.answer(f"Максимум {config.MAX_ROUTES} маршрутів!", show_alert=True)
            return
        await state.set_state(AddRoute.from_search)
        await cb.message.edit_text("📍 Напиши станцію <b>відправлення</b>:\n<i>напр.: Київ, Львів</i>", parse_mode="HTML")

    @dp.callback_query(F.data == "retry_from")
    async def cb_retry_from(cb: CallbackQuery, state: FSMContext):
        await state.set_state(AddRoute.from_search)
        await cb.message.edit_text("📍 Напиши станцію <b>відправлення</b>:", parse_mode="HTML")

    @dp.message(AddRoute.from_search)
    async def m_from(msg: Message, state: FSMContext):
        st = await search_stations(msg.text.strip())
        if not st:
            await msg.answer("❌ Не знайдено. Спробуй інакше:")
            return
        await state.set_state(AddRoute.from_pick)
        await msg.answer(f"🔍 Результати для «{msg.text.strip()}»:", reply_markup=kb_stations(st, "from"))

    @dp.callback_query(AddRoute.from_pick, F.data.startswith("pick_from:"))
    async def cb_pick_from(cb: CallbackQuery, state: FSMContext):
        _, sid, name = cb.data.split(":", 2)
        await state.update_data(from_id=sid, from_name=name)
        await state.set_state(AddRoute.to_search)
        await cb.message.edit_text(f"✅ Звідки: <b>{name}</b>\n\n📍 Тепер станцію <b>призначення</b>:", parse_mode="HTML")

    @dp.callback_query(F.data == "retry_to")
    async def cb_retry_to(cb: CallbackQuery, state: FSMContext):
        await state.set_state(AddRoute.to_search)
        await cb.message.edit_text("📍 Напиши станцію <b>призначення</b>:", parse_mode="HTML")

    @dp.message(AddRoute.to_search)
    async def m_to(msg: Message, state: FSMContext):
        data = await state.get_data()
        st = [s for s in await search_stations(msg.text.strip()) if s["id"] != data.get("from_id")]
        if not st:
            await msg.answer("❌ Не знайдено. Спробуй інакше:")
            return
        await state.set_state(AddRoute.to_pick)
        await msg.answer(f"🔍 Результати для «{msg.text.strip()}»:", reply_markup=kb_stations(st, "to"))

    @dp.callback_query(AddRoute.to_pick, F.data.startswith("pick_to:"))
    async def cb_pick_to(cb: CallbackQuery, state: FSMContext):
        _, sid, name = cb.data.split(":", 2)
        data = await state.get_data()
        await state.update_data(to_id=sid, to_name=name)
        await state.set_state(AddRoute.date)
        await cb.message.edit_text(
            f"✅ Звідки: <b>{data['from_name']}</b>\n✅ Куди: <b>{name}</b>\n\n📅 Вибери <b>дату</b>:",
            parse_mode="HTML", reply_markup=kb_dates())

    @dp.callback_query(AddRoute.date, F.data.startswith("date:"))
    async def cb_date(cb: CallbackQuery, state: FSMContext):
        date = cb.data.split(":", 1)[1]
        data = await state.get_data()
        await state.clear()
        ok, res = storage.add_route(cb.message.chat.id, data["from_id"], data["from_name"],
                                    data["to_id"], data["to_name"], date)
        if ok:
            await cb.message.edit_text(
                f"✅ <b>Маршрут додано!</b>\n🗺 {data['from_name']} → {data['to_name']}\n📅 {date}\n\n"
                f"Перевірка кожні {config.CHECK_INTERVAL}с. Пінгну, щойно з'являться місця.",
                parse_mode="HTML", reply_markup=kb_main(True))
        else:
            await cb.message.edit_text(f"⚠️ {res}", reply_markup=kb_main(bool(storage.list_routes(cb.message.chat.id))))

    # ── Список / керування ──
    @dp.callback_query(F.data == "list_routes")
    async def cb_list(cb: CallbackQuery):
        routes = storage.list_routes(cb.message.chat.id)
        if not routes:
            await cb.answer("Немає маршрутів.", show_alert=True)
            return
        await cb.message.edit_text("📋 <b>Твої маршрути:</b>", parse_mode="HTML", reply_markup=kb_routes(routes))

    @dp.callback_query(F.data.startswith("route:"))
    async def cb_route(cb: CallbackQuery):
        key = cb.data.split(":", 1)[1]
        r = storage.get_route(key)
        if not r or r["chat_id"] != cb.message.chat.id:
            await cb.answer("Не знайдено.", show_alert=True)
            return
        status = "🟢 Активний" if r["active"] else "🔴 Пауза"
        await cb.message.edit_text(
            f"🗺 <b>{r['from_name']} → {r['to_name']}</b>\n📅 {r['date']}\nСтатус: {status}",
            parse_mode="HTML", reply_markup=kb_route_actions(key, r["active"]))

    @dp.callback_query(F.data.startswith("status:"))
    async def cb_status(cb: CallbackQuery):
        key = cb.data.split(":", 1)[1]
        r = storage.get_route(key)
        if not r or r["chat_id"] != cb.message.chat.id:
            await cb.answer("Не знайдено.", show_alert=True)
            return
        await cb.answer("⏳ Перевіряю…")
        await cb.message.edit_text("⏳ Завантажую дані (~15с)…")
        text = await monitor.fetch_status_text(r)
        await cb.message.edit_text(text, parse_mode="HTML", disable_web_page_preview=True,
                                   reply_markup=kb_route_actions(key, r["active"]))

    @dp.callback_query(F.data.startswith("pause:"))
    async def cb_pause(cb: CallbackQuery):
        key = cb.data.split(":", 1)[1]
        storage.set_active(key, False)
        await cb.answer("⏸ Пауза")
        await cb.message.edit_reply_markup(reply_markup=kb_route_actions(key, False))

    @dp.callback_query(F.data.startswith("resume:"))
    async def cb_resume(cb: CallbackQuery):
        key = cb.data.split(":", 1)[1]
        storage.set_active(key, True)
        await cb.answer("▶️ Увімкнено")
        await cb.message.edit_reply_markup(reply_markup=kb_route_actions(key, True))

    @dp.callback_query(F.data.startswith("del:"))
    async def cb_del(cb: CallbackQuery):
        key = cb.data.split(":", 1)[1]
        storage.delete_route(key)
        await cb.answer("🗑 Видалено")
        routes = storage.list_routes(cb.message.chat.id)
        if routes:
            await cb.message.edit_text("📋 <b>Твої маршрути:</b>", parse_mode="HTML", reply_markup=kb_routes(routes))
        else:
            await cb.message.edit_text("Маршрутів немає.", reply_markup=kb_main(False))

    @dp.message(Command("id"))
    async def cmd_id(msg: Message):
        await msg.answer(f"chat_id: <code>{msg.chat.id}</code>", parse_mode="HTML")
