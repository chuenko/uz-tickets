#!/usr/bin/env python3
"""
UZ Train Monitor Bot — Telegram керування
Пошук станцій через API (як на сайті), моніторинг до 5 маршрутів.

Встановлення:
    pip install playwright aiohttp python-dotenv aiogram
    playwright install chromium

Запуск:
    python uz_bot.py
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

import aiohttp
from dotenv import load_dotenv
from playwright.async_api import async_playwright, BrowserContext
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

load_dotenv()

# ─────────────────────────────────────────────
# КОНФІГ
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
TELEGRAM_CHAT_ID   = int(os.getenv("TELEGRAM_CHAT_ID", "0"))
CHECK_INTERVAL     = int(os.getenv("CHECK_INTERVAL", "120"))
MAX_ROUTES         = 5
HEADLESS           = os.getenv("HEADLESS", "true").lower() == "true"

BASE_URL = "https://booking.uz.gov.ua"
API_BASE = "https://app.uz.gov.ua"

WAGON_TYPE_NAMES = {
    "П": "Плацкарт", "К": "Купе", "Л": "Люкс/СВ",
    "С": "Сидячий",  "О": "Загальний",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("uz_bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# FSM
# ─────────────────────────────────────────────
class AddRoute(StatesGroup):
    from_search  = State()   # юзер пише назву станції відправлення
    from_pick    = State()   # юзер вибирає зі списку
    to_search    = State()   # юзер пише назву станції призначення
    to_pick      = State()   # юзер вибирає зі списку
    date         = State()   # юзер вибирає дату


# ─────────────────────────────────────────────
# ПОШУК СТАНЦІЙ через UZ API
# ─────────────────────────────────────────────
SEARCH_HEADERS = {
    "Accept":          "application/json",
    "Accept-Language": "uk-UA,uk;q=0.9",
    "Origin":          BASE_URL,
    "Referer":         f"{BASE_URL}/",
    "X-Client-Locale": "uk",
    "X-User-Agent":    "UZ/2 Web/1 User/guest",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    ),
}

async def search_stations(query: str) -> list[dict]:
    """Повертає список {id, name} для запиту через реальний API."""
    url = f"{API_BASE}/api/stations?search={query}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=SEARCH_HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    log.warning(f"stations search {resp.status} for '{query}'")
                    return []
                data = await resp.json(content_type=None)
                return normalize_stations(data)
    except Exception as e:
        log.error(f"search_stations error: {e}")
        return []


def normalize_stations(data) -> list[dict]:
    """Нормалізує різні формати відповіді API в [{id, name}]."""
    results = []
    items = data if isinstance(data, list) else (
        data.get("data") or data.get("stations") or data.get("items") or []
    )
    for item in items[:10]:  # максимум 10 результатів
        sid  = str(item.get("id") or item.get("station_id") or item.get("code") or "")
        name = item.get("name") or item.get("title") or item.get("station_name") or ""
        if sid and name:
            results.append({"id": sid, "name": name})
    return results


# ─────────────────────────────────────────────
# PLAYWRIGHT FETCHER
# ─────────────────────────────────────────────
class UZFetcher:
    def __init__(self):
        self._pw      = None
        self._browser = None
        self.context: Optional[BrowserContext] = None

    async def start(self):
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        self.context = await self._browser.new_context(
            locale="uk-UA",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/147.0.0.0 Safari/537.36"
            ),
        )
        log.info(f"Браузер запущено (headless={HEADLESS})")

    async def fetch(self, from_id: str, to_id: str, date: str) -> Optional[dict]:
        page = await self.context.new_page()
        result = None
        got = asyncio.Event()

        async def on_response(response):
            nonlocal result
            url = response.url
            if (
                "/api/v3/trips" in url
                and "station_from_id" in url
                and f"date={date}" in url
            ):
                try:
                    if response.status == 200:
                        result = await response.json()
                        log.info(f"API OK [{from_id}→{to_id} {date}]")
                    else:
                        log.warning(f"API {response.status} [{from_id}→{to_id}]")
                except Exception as e:
                    log.error(f"Помилка читання: {e}")
                finally:
                    got.set()

        page.on("response", on_response)
        url = f"{BASE_URL}/search-trips/{from_id}/{to_id}/list?startDate={date}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            try:
                await asyncio.wait_for(got.wait(), timeout=20)
            except asyncio.TimeoutError:
                log.warning(f"Таймаут [{from_id}→{to_id} {date}]")
        except Exception as e:
            log.error(f"Навігація: {e}")
        finally:
            await page.close()
        return result

    async def close(self):
        if self.context:  await self.context.close()
        if self._browser: await self._browser.close()
        if self._pw:      await self._pw.stop()


# ─────────────────────────────────────────────
# ПАРСИНГ
# ─────────────────────────────────────────────
def parse_trains(data) -> list[dict]:
    items = []
    if isinstance(data, dict):
        items = data.get("direct") or data.get("data") or data.get("trips") or []
    elif isinstance(data, list):
        items = data

    trains = []
    for raw in items:
        train_obj = raw.get("train", {})
        number    = train_obj.get("number", "?")

        def fmt_ts(ts):
            if not ts:
                return "—"
            try:
                return datetime.fromtimestamp(int(ts)).strftime("%d.%m %H:%M")
            except:
                return str(ts)

        departure = fmt_ts(raw.get("depart_at"))
        arrival   = fmt_ts(raw.get("arrive_at"))

        wagon_classes = train_obj.get("wagon_classes") or []
        seats = {}
        for wc in wagon_classes:
            code  = wc.get("id", "?")
            title = wc.get("name") or WAGON_TYPE_NAMES.get(code, code)
            count = int(wc.get("free_seats") or 0)
            price = round(float(wc.get("price") or 0) / 100)
            seats[code] = {"title": title, "seats": count, "price": price}

        trains.append({
            "number": number,
            "departure": departure,
            "arrival": arrival,
            "seats": seats,
        })
    return trains


def seats_snapshot(trains: list[dict]) -> dict:
    return {
        t["number"]: {code: info["seats"] for code, info in t["seats"].items()}
        for t in trains
    }


def diff_seats(old: dict, new: dict) -> list[tuple]:
    changes = []
    for num, new_seats in new.items():
        old_seats = old.get(num, {})
        for code, new_cnt in new_seats.items():
            old_cnt = old_seats.get(code, -1)
            if old_cnt != new_cnt:
                changes.append((num, code, old_cnt, new_cnt))
    return changes


# ─────────────────────────────────────────────
# ФОРМАТУВАННЯ
# ─────────────────────────────────────────────
def fmt_train(t: dict) -> str:
    lines = [f"🚂 <b>№{t['number']}</b>  🕐 {t['departure']} → {t['arrival']}"]
    if t["seats"]:
        for code, info in t["seats"].items():
            cnt = info["seats"]
            price_str = f"{info['price']:.0f} грн" if info["price"] else "—"
            icon = "✅" if cnt > 0 else "❌"
            lines.append(f"  {icon} {info['title']} ({code}): <b>{cnt}</b> місць, від {price_str}")
    else:
        lines.append("  ❌ Місць немає")
    return "\n".join(lines)


def fmt_alert(changes: list[tuple], trains: list[dict], route: dict) -> str:
    from_name = route.get("from_name", route["from"])
    to_name   = route.get("to_name",   route["to"])
    link = f"{BASE_URL}/search-trips/{route['from']}/{route['to']}/list?startDate={route['date']}"
    train_map = {t["number"]: t for t in trains}

    lines = [
        f"🔔 <b>Зміна місць!</b>",
        f"🗺 {from_name} → {to_name}  📅 {route['date']}\n",
    ]
    for num, code, was, now in changes:
        t = train_map.get(num)
        dep = t["departure"] if t else "—"
        arr = t["arrival"]   if t else "—"
        wagon_name = WAGON_TYPE_NAMES.get(code, code)
        was_str = str(was) if was >= 0 else "н/д"
        arrow = "📈" if now > max(was, 0) else "📉"
        lines.append(
            f"{arrow} <b>№{num}</b> ({dep}→{arr})\n"
            f"   {wagon_name} ({code}): {was_str} → <b>{now}</b> місць"
        )
    lines.append(f"\n🔗 <a href='{link}'>Купити квиток</a>")
    return "\n\n".join(lines)


def fmt_status(trains: list[dict], route: dict) -> str:
    from_name = route.get("from_name", route["from"])
    to_name   = route.get("to_name",   route["to"])
    link = f"{BASE_URL}/search-trips/{route['from']}/{route['to']}/list?startDate={route['date']}"
    parts = [
        f"📋 <b>{from_name} → {to_name}</b>  📅 {route['date']}\n"
        f"Поїздів: {len(trains)}\n{'─'*28}",
    ]
    for t in trains:
        parts.append(fmt_train(t))
    parts.append(f"⏱ {datetime.now().strftime('%H:%M:%S')}  🔗 <a href='{link}'>Відкрити</a>")
    return "\n\n".join(parts)


# ─────────────────────────────────────────────
# КЛАВІАТУРИ
# ─────────────────────────────────────────────
def kb_main(has_routes: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="➕ Додати маршрут", callback_data="add_route")]]
    if has_routes:
        rows.append([InlineKeyboardButton(text="📋 Мої маршрути", callback_data="list_routes")])
        rows.append([InlineKeyboardButton(text="🔍 Статус зараз", callback_data="status_now")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_station_results(stations: list[dict], prefix: str) -> InlineKeyboardMarkup:
    rows = []
    for st in stations:
        rows.append([InlineKeyboardButton(
            text=st["name"],
            callback_data=f"pick_{prefix}:{st['id']}:{st['name'][:30]}"
        )])
    rows.append([InlineKeyboardButton(text="🔄 Шукати знову", callback_data=f"retry_{prefix}")])
    rows.append([InlineKeyboardButton(text="❌ Скасувати", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_dates() -> InlineKeyboardMarkup:
    today = datetime.now()
    rows = []
    row = []
    for i in range(1, 22):
        d = today + timedelta(days=i)
        label = d.strftime("%d.%m")
        value = d.strftime("%Y-%m-%d")
        row.append(InlineKeyboardButton(text=label, callback_data=f"date:{value}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="❌ Скасувати", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_routes(routes: dict) -> InlineKeyboardMarkup:
    rows = []
    for key, r in routes.items():
        from_name = r.get("from_name", r["from"])
        to_name   = r.get("to_name",   r["to"])
        status    = "🟢" if r.get("active", True) else "🔴"
        rows.append([InlineKeyboardButton(
            text=f"{status} {from_name}→{to_name} {r['date']}",
            callback_data=f"route_info:{key}"
        )])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_route_actions(key: str, active: bool) -> InlineKeyboardMarkup:
    toggle_text = "⏸ Пауза" if active else "▶️ Увімкнути"
    toggle_cb   = f"pause:{key}" if active else f"resume:{key}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_text,   callback_data=toggle_cb)],
        [InlineKeyboardButton(text="🗑 Видалити", callback_data=f"delete:{key}")],
        [InlineKeyboardButton(text="🔙 Назад",    callback_data="list_routes")],
    ])


# ─────────────────────────────────────────────
# МОНІТОР
# ─────────────────────────────────────────────
class UZMonitor:
    def __init__(self, bot: Bot):
        self.bot     = bot
        self.fetcher = UZFetcher()
        self.routes: dict[str, dict] = {}

    def route_key(self, from_id: str, to_id: str, date: str) -> str:
        return f"{from_id}_{to_id}_{date}"

    def add_route(self, from_id: str, from_name: str, to_id: str, to_name: str, date: str) -> tuple[bool, str]:
        key = self.route_key(from_id, to_id, date)
        if key in self.routes:
            return False, "Такий маршрут вже є."
        if len(self.routes) >= MAX_ROUTES:
            return False, f"Максимум {MAX_ROUTES} маршрутів. Видаліть один."
        self.routes[key] = {
            "from": from_id, "from_name": from_name,
            "to":   to_id,   "to_name":   to_name,
            "date": date, "active": True, "snapshot": {}
        }
        log.info(f"Додано маршрут: {key} ({from_name}→{to_name})")
        return True, key

    def delete_route(self, key: str):
        self.routes.pop(key, None)

    def toggle_route(self, key: str, active: bool):
        if key in self.routes:
            self.routes[key]["active"] = active

    async def start(self):
        await self.fetcher.start()
        log.info("Монітор запущено")
        while True:
            await self.check_all()
            await asyncio.sleep(CHECK_INTERVAL)

    async def check_all(self):
        for key, route in list(self.routes.items()):
            if not route.get("active", True):
                continue
            try:
                await self.check_route(key, route)
            except Exception as e:
                log.error(f"Помилка маршруту {key}: {e}")

    async def check_route(self, key: str, route: dict):
        raw = await self.fetcher.fetch(route["from"], route["to"], route["date"])
        if raw is None:
            return
        trains   = parse_trains(raw)
        new_snap = seats_snapshot(trains)
        old_snap = route.get("snapshot", {})
        changes  = diff_seats(old_snap, new_snap)
        route["snapshot"] = new_snap

        if changes:
            log.info(f"Зміни [{key}]: {changes}")
            msg = fmt_alert(changes, trains, route)
            await self.bot.send_message(TELEGRAM_CHAT_ID, msg, parse_mode="HTML")
        else:
            log.info(f"Без змін [{key}]")

    async def fetch_status(self, key: str) -> Optional[str]:
        route = self.routes.get(key)
        if not route:
            return None
        raw = await self.fetcher.fetch(route["from"], route["to"], route["date"])
        if raw is None:
            return "⚠️ Не вдалось отримати дані."
        trains = parse_trains(raw)
        return fmt_status(trains, route)

    async def close(self):
        await self.fetcher.close()


# ─────────────────────────────────────────────
# ХЕНДЛЕРИ
# ─────────────────────────────────────────────
def setup_handlers(dp: Dispatcher, monitor: UZMonitor):

    @dp.message(Command("start"))
    async def cmd_start(msg: Message):
        await msg.answer(
            "👋 <b>UZ Train Monitor</b>\n\n"
            f"Моніторю до {MAX_ROUTES} маршрутів одночасно.\n"
            "Сповіщу при будь-якій зміні кількості місць.\n\n"
            "Натисни <b>Додати маршрут</b> і напиши назву станції.",
            parse_mode="HTML",
            reply_markup=kb_main(bool(monitor.routes))
        )

    @dp.message(Command("menu"))
    async def cmd_menu(msg: Message, state: FSMContext):
        await state.clear()
        await msg.answer("Головне меню:", reply_markup=kb_main(bool(monitor.routes)))

    @dp.callback_query(F.data == "main_menu")
    async def cb_main_menu(cb: CallbackQuery, state: FSMContext):
        await state.clear()
        await cb.message.edit_text("Головне меню:", reply_markup=kb_main(bool(monitor.routes)))

    # ── Додати маршрут ──────────────────────────

    @dp.callback_query(F.data == "add_route")
    async def cb_add_route(cb: CallbackQuery, state: FSMContext):
        if len(monitor.routes) >= MAX_ROUTES:
            await cb.answer(f"Максимум {MAX_ROUTES} маршрутів!", show_alert=True)
            return
        await state.set_state(AddRoute.from_search)
        await cb.message.edit_text(
            "📍 Напишіть назву станції <b>відправлення</b>:\n\n"
            "<i>Приклад: Київ, Варшава, Львів, Краків...</i>",
            parse_mode="HTML"
        )

    # Пошук FROM — юзер пише текст
    @dp.message(AddRoute.from_search)
    async def msg_from_search(msg: Message, state: FSMContext):
        query = msg.text.strip()
        if len(query) < 2:
            await msg.answer("Введіть мінімум 2 символи.")
            return
        stations = await search_stations(query)
        if not stations:
            await msg.answer(
                f"❌ Станцій за запитом «{query}» не знайдено.\nСпробуйте інакше:",
            )
            return
        await state.update_data(from_query=query)
        await state.set_state(AddRoute.from_pick)
        await msg.answer(
            f"🔍 Результати для «{query}»:",
            reply_markup=kb_station_results(stations, "from")
        )

    # Retry FROM
    @dp.callback_query(F.data == "retry_from")
    async def cb_retry_from(cb: CallbackQuery, state: FSMContext):
        await state.set_state(AddRoute.from_search)
        await cb.message.edit_text(
            "📍 Напишіть назву станції <b>відправлення</b>:",
            parse_mode="HTML"
        )

    # Вибір FROM зі списку
    @dp.callback_query(AddRoute.from_pick, F.data.startswith("pick_from:"))
    async def cb_pick_from(cb: CallbackQuery, state: FSMContext):
        parts   = cb.data.split(":", 2)
        from_id = parts[1]
        from_name = parts[2] if len(parts) > 2 else from_id
        await state.update_data(from_id=from_id, from_name=from_name)
        await state.set_state(AddRoute.to_search)
        await cb.message.edit_text(
            f"✅ <b>Звідки:</b> {from_name}\n\n"
            f"📍 Напишіть назву станції <b>призначення</b>:\n\n"
            f"<i>Приклад: Варшава, Краків, Берлін...</i>",
            parse_mode="HTML"
        )

    # Пошук TO — юзер пише текст
    @dp.message(AddRoute.to_search)
    async def msg_to_search(msg: Message, state: FSMContext):
        query = msg.text.strip()
        if len(query) < 2:
            await msg.answer("Введіть мінімум 2 символи.")
            return
        data = await state.get_data()
        stations = await search_stations(query)
        # Виключаємо станцію відправлення
        stations = [s for s in stations if s["id"] != data.get("from_id")]
        if not stations:
            await msg.answer(f"❌ Станцій за запитом «{query}» не знайдено. Спробуйте інакше:")
            return
        await state.update_data(to_query=query)
        await state.set_state(AddRoute.to_pick)
        await msg.answer(
            f"🔍 Результати для «{query}»:",
            reply_markup=kb_station_results(stations, "to")
        )

    # Retry TO
    @dp.callback_query(F.data == "retry_to")
    async def cb_retry_to(cb: CallbackQuery, state: FSMContext):
        await state.set_state(AddRoute.to_search)
        data = await state.get_data()
        await cb.message.edit_text(
            f"✅ <b>Звідки:</b> {data.get('from_name', '?')}\n\n"
            f"📍 Напишіть назву станції <b>призначення</b>:",
            parse_mode="HTML"
        )

    # Вибір TO зі списку
    @dp.callback_query(AddRoute.to_pick, F.data.startswith("pick_to:"))
    async def cb_pick_to(cb: CallbackQuery, state: FSMContext):
        parts   = cb.data.split(":", 2)
        to_id   = parts[1]
        to_name = parts[2] if len(parts) > 2 else to_id
        data    = await state.get_data()
        await state.update_data(to_id=to_id, to_name=to_name)
        await state.set_state(AddRoute.date)
        await cb.message.edit_text(
            f"✅ <b>Звідки:</b> {data['from_name']}\n"
            f"✅ <b>Куди:</b>   {to_name}\n\n"
            f"📅 Виберіть <b>дату</b>:",
            parse_mode="HTML",
            reply_markup=kb_dates()
        )

    # Вибір дати
    @dp.callback_query(AddRoute.date, F.data.startswith("date:"))
    async def cb_date_selected(cb: CallbackQuery, state: FSMContext):
        date = cb.data.split(":", 1)[1]
        data = await state.get_data()
        await state.clear()

        ok, result = monitor.add_route(
            data["from_id"], data["from_name"],
            data["to_id"],   data["to_name"],
            date
        )

        if ok:
            await cb.message.edit_text(
                f"✅ <b>Маршрут додано!</b>\n\n"
                f"🗺 {data['from_name']} → {data['to_name']}\n"
                f"📅 {date}\n\n"
                f"Перевірка кожні {CHECK_INTERVAL}с.\n"
                f"Сповіщу при будь-якій зміні місць.",
                parse_mode="HTML",
                reply_markup=kb_main(True)
            )
        else:
            await cb.message.edit_text(f"⚠️ {result}", reply_markup=kb_main(bool(monitor.routes)))

    # Скасувати
    @dp.callback_query(F.data == "cancel")
    async def cb_cancel(cb: CallbackQuery, state: FSMContext):
        await state.clear()
        await cb.message.edit_text("Скасовано.", reply_markup=kb_main(bool(monitor.routes)))

    # ── Список маршрутів ────────────────────────

    @dp.callback_query(F.data == "list_routes")
    async def cb_list_routes(cb: CallbackQuery):
        if not monitor.routes:
            await cb.answer("Немає маршрутів.", show_alert=True)
            return
        await cb.message.edit_text(
            "📋 <b>Твої маршрути:</b>\nНатисни для керування:",
            parse_mode="HTML",
            reply_markup=kb_routes(monitor.routes)
        )

    @dp.callback_query(F.data.startswith("route_info:"))
    async def cb_route_info(cb: CallbackQuery):
        key   = cb.data.split(":", 1)[1]
        route = monitor.routes.get(key)
        if not route:
            await cb.answer("Маршрут не знайдено.", show_alert=True)
            return
        from_name = route.get("from_name", route["from"])
        to_name   = route.get("to_name",   route["to"])
        status    = "🟢 Активний" if route.get("active", True) else "🔴 Пауза"
        await cb.message.edit_text(
            f"🗺 <b>{from_name} → {to_name}</b>\n"
            f"📅 {route['date']}\n"
            f"Статус: {status}",
            parse_mode="HTML",
            reply_markup=kb_route_actions(key, route.get("active", True))
        )

    @dp.callback_query(F.data.startswith("pause:"))
    async def cb_pause(cb: CallbackQuery):
        key = cb.data.split(":", 1)[1]
        monitor.toggle_route(key, False)
        await cb.answer("⏸ На паузі")
        await cb.message.edit_reply_markup(reply_markup=kb_route_actions(key, False))

    @dp.callback_query(F.data.startswith("resume:"))
    async def cb_resume(cb: CallbackQuery):
        key = cb.data.split(":", 1)[1]
        monitor.toggle_route(key, True)
        await cb.answer("▶️ Увімкнено")
        await cb.message.edit_reply_markup(reply_markup=kb_route_actions(key, True))

    @dp.callback_query(F.data.startswith("delete:"))
    async def cb_delete(cb: CallbackQuery):
        key = cb.data.split(":", 1)[1]
        monitor.delete_route(key)
        await cb.answer("🗑 Видалено")
        if monitor.routes:
            await cb.message.edit_text(
                "📋 <b>Твої маршрути:</b>",
                parse_mode="HTML",
                reply_markup=kb_routes(monitor.routes)
            )
        else:
            await cb.message.edit_text("Маршрутів немає.", reply_markup=kb_main(False))

    # ── Статус зараз ────────────────────────────

    @dp.callback_query(F.data == "status_now")
    async def cb_status_now(cb: CallbackQuery):
        if not monitor.routes:
            await cb.answer("Немає маршрутів.", show_alert=True)
            return
        rows = []
        for key, r in monitor.routes.items():
            from_name = r.get("from_name", r["from"])
            to_name   = r.get("to_name",   r["to"])
            rows.append([InlineKeyboardButton(
                text=f"{from_name}→{to_name} {r['date']}",
                callback_data=f"check_now:{key}"
            )])
        rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")])
        await cb.message.edit_text(
            "Який маршрут перевірити?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )

    @dp.callback_query(F.data.startswith("check_now:"))
    async def cb_check_now(cb: CallbackQuery):
        key = cb.data.split(":", 1)[1]
        await cb.answer("⏳ Перевіряю...")
        await cb.message.edit_text("⏳ Завантажую дані, зачекайте ~10с...")
        text = await monitor.fetch_status(key)
        await cb.message.edit_text(
            text or "⚠️ Не вдалось отримати дані.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Оновити", callback_data=f"check_now:{key}")],
                [InlineKeyboardButton(text="🔙 Назад",   callback_data="main_menu")],
            ])
        )


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
async def main():
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN":
        log.error("Встанови TELEGRAM_BOT_TOKEN в .env")
        return

    bot     = Bot(token=TELEGRAM_BOT_TOKEN)
    storage = MemoryStorage()
    dp      = Dispatcher(storage=storage)
    monitor = UZMonitor(bot)

    setup_handlers(dp, monitor)

    monitor_task = asyncio.create_task(monitor.start())
    log.info("Бот запущено. Надішли /start")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        monitor_task.cancel()
        await monitor.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Зупинено.")
