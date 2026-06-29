"""Цикл моніторингу: перевіряє маршрути й шле сповіщення при появі місць."""
import asyncio
import logging
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    _KYIV = ZoneInfo("Europe/Kyiv")
except Exception:
    _KYIV = None

from aiogram import Bot

from . import config, storage
from .parser import (
    parse_trains, apply_wagon_filter, seats_snapshot, diff_seats,
    WAGON_TYPE_NAMES,
)
from .uz_client import UZFetcher

log = logging.getLogger(__name__)


def _apply_train_filter(trains: list[dict], train_filter: str) -> list[dict]:
    """Лишає лише потрібні номери поїздів (порожньо = всі). Збіг за підрядком."""
    toks = [t.strip().upper() for t in (train_filter or "").split(",") if t.strip()]
    if not toks:
        return trains
    return [tr for tr in trains if any(tok in str(tr["number"]).upper() for tok in toks)]


def in_quiet_hours(route: dict) -> bool:
    """Чи зараз тихі години (за київським часом)."""
    qf, qt = route.get("quiet_from", ""), route.get("quiet_to", "")
    if not qf or not qt:
        return False
    now = datetime.now(_KYIV).strftime("%H:%M") if _KYIV else datetime.now().strftime("%H:%M")
    if qf <= qt:
        return qf <= now < qt
    return now >= qf or now < qt   # через північ


def buy_link(route: dict) -> str:
    return (
        f"{config.BASE_URL}/search-trips/"
        f"{route['from_id']}/{route['to_id']}/list?startDate={route['date']}"
    )


def notification_action(mode: str, appeared: bool, live_id: int) -> str:
    """send — нове повідомлення, edit — тиха правка, none — лише зберегти стан."""
    if mode == "any":
        return "send"
    if mode == "appear":
        return "send" if appeared else "none"
    return "send" if appeared else ("edit" if live_id else "none")


def normalize_seat_map(raw) -> list[dict]:
    """Зводить різні форми відповіді УЗ до [{number, seats:[{number,free}]}]."""
    wagons = []
    seen = set()

    def seat_items(value):
        out = []
        items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
        for item in items:
            if isinstance(item, int) or (isinstance(item, str) and item.isdigit()):
                out.append({"number": int(item), "free": True})
                continue
            if not isinstance(item, dict):
                continue
            number = item.get("number") or item.get("place") or item.get("seat") or item.get("id")
            try:
                number = int(number)
            except (TypeError, ValueError):
                continue
            status = str(item.get("status") or "").lower()
            free = item.get("is_free", item.get("free", item.get("available", True)))
            if status in ("occupied", "busy", "sold", "reserved"):
                free = False
            out.append({"number": number, "free": bool(free)})
        return out

    def walk(value):
        if isinstance(value, dict):
            places = next((value.get(k) for k in (
                "places", "seats", "free_seats", "available_seats"
            ) if value.get(k) is not None), None)
            number = (
                value.get("wagon_number") or value.get("car_number")
                or value.get("wagon_num") or value.get("number")
            )
            seats = seat_items(places)
            if number is not None and seats:
                key = str(number)
                if key not in seen:
                    seen.add(key)
                    wagons.append({
                        "number": key,
                        "seats": sorted(seats, key=lambda seat: seat["number"]),
                    })
                return
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(raw)
    return wagons


def fmt_status(trains: list[dict], route: dict) -> str:
    link = buy_link(route)
    # лише поїзди, де є вільні місця
    avail = [t for t in trains if t["total_free"] > 0]
    head = (
        f"📋 <b>{route['from_name']} → {route['to_name']}</b>  📅 {route['date']}\n"
        f"З місцями: {len(avail)} із {len(trains)}\n{'─' * 26}"
    )
    parts = [head]
    if not avail:
        parts.append("😕 Зараз вільних місць немає. Стежу — пінгну, щойно з'являться.")
    for t in avail:
        lines = [f"🚂 <b>№{t['number']}</b>  🕐 {t['departure']} → {t['arrival']}"]
        # лише вагони з місцями
        for code, info in t["seats"].items():
            if info["seats"] <= 0:
                continue
            price = f"від {info['price']} грн" if info["price"] else "—"
            lines.append(f"  ✅ {info['title']} ({code}): <b>{info['seats']}</b> місць, {price}")
        parts.append("\n".join(lines))
    parts.append(
        f"⏱ {datetime.now():%H:%M:%S}  🔗 <a href='{link}'>Відкрити на UZ</a>"
    )
    return "\n\n".join(parts)


def fmt_alert(changes: list[tuple], trains: list[dict], route: dict) -> str:
    link = buy_link(route)
    train_map = {t["number"]: t for t in trains}
    up = any(d == "up" for *_, d in changes)
    down = any(d == "down" for *_, d in changes)
    if up and not down:
        head = "🔔 <b>З'явилися місця!</b>"
    elif down and not up:
        head = "📉 <b>Місць меншає!</b>"
    else:
        head = "🔔 <b>Зміна місць</b>"
    lines = [head, f"🗺 {route['from_name']} → {route['to_name']}  📅 {route['date']}"]
    for num, code, was, now, direction in changes:
        t = train_map.get(num)
        dep = t["departure"] if t else "—"
        arr = t["arrival"] if t else "—"
        wagon = WAGON_TYPE_NAMES.get(code, code)
        price = ""
        if t and code in t["seats"] and t["seats"][code]["price"]:
            price = f", від {t['seats'][code]['price']} грн"
        arrow = "📈" if direction == "up" else "📉"
        was_str = str(was) if was >= 0 else "0"
        lines.append(
            f"{arrow} <b>№{num}</b> ({dep}→{arr})\n"
            f"   {wagon} ({code}): {was_str} → <b>{now}</b> місць{price}"
        )
    lines.append(f"🔗 <a href='{link}'>Купити квиток</a>")
    return "\n\n".join(lines)


class UZMonitor:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.fetcher = UZFetcher()
        self._task: asyncio.Task | None = None

    async def start(self):
        await self.fetcher.start()
        log.info("Монітор запущено (інтервал %sс)", config.CHECK_INTERVAL)
        while True:
            try:
                await self.check_all()
            except Exception as e:
                log.error("check_all: %s", e)
            await asyncio.sleep(config.CHECK_INTERVAL)

    async def check_all(self):
        for route in storage.list_routes():
            if not route["active"]:
                continue
            try:
                await self.check_route(route)
            except Exception as e:
                log.error("Маршрут %s: %s", route["key"], e)

    async def _get_trains(self, route: dict) -> list[dict] | None:
        raw = await self.fetcher.fetch(route["from_id"], route["to_id"], route["date"])
        if raw is None:
            return None
        trains = parse_trains(raw)
        trains = _apply_train_filter(trains, route.get("train_filter", ""))
        return apply_wagon_filter(trains, route.get("wagon_filter", ""))

    async def check_route(self, route: dict):
        trains = await self._get_trains(route)
        if trains is None:
            return
        new_snap = seats_snapshot(trains)
        changes = diff_seats(route.get("snapshot", {}), new_snap)
        storage.save_snapshot(route["key"], new_snap)
        if not changes:
            return

        # місця реально з'явилися (вагон з 0/відсутній → >0) — це момент ловлі
        appeared = any(d == "up" and was <= 0 for (_n, _c, was, _now, d) in changes)
        avail = any(t["total_free"] > 0 for t in trains)
        text = fmt_status(trains, route)          # поточна наявність (одне живе повідомлення)
        live_id = route.get("live_msg_id") or 0
        chat = route["chat_id"]
        mode = route.get("notify_on") or "appear_decrease"
        action = notification_action(mode, appeared, live_id)
        log.info("Зміни [%s]: appeared=%s avail=%s mode=%s action=%s",
                 route["key"], appeared, avail, mode, action)

        if action == "send":
            # нова поява місць → окреме повідомлення (вночі — без звуку)
            message = fmt_alert(changes, trains, route) if mode == "any" else text
            msg = await self.bot.send_message(chat, message, parse_mode="HTML",
                                              disable_web_page_preview=True,
                                              disable_notification=in_quiet_hours(route))
            storage.set_live_msg(route["key"], msg.message_id)
        elif action == "edit":
            # лише коливання (більше/менше) → мовчки редагуємо те саме повідомлення
            try:
                await self.bot.edit_message_text(text, chat_id=chat, message_id=live_id,
                                                 parse_mode="HTML", disable_web_page_preview=True)
            except Exception:
                msg = await self.bot.send_message(chat, text, parse_mode="HTML",
                                                  disable_web_page_preview=True)
                storage.set_live_msg(route["key"], msg.message_id)
        # місць не лишилось → скинути, щоб наступна поява знову пінгнула
        if not avail:
            storage.set_live_msg(route["key"], 0)

    async def fetch_status_text(self, route: dict) -> str:
        trains = await self._get_trains(route)
        if trains is None:
            return "⚠️ Не вдалося отримати дані. Спробуйте пізніше."
        return fmt_status(trains, route)

    async def fetch_status_json(self, route: dict) -> dict:
        trains = await self._get_trains(route)
        if trains is None:
            return {"ok": False, "trains": []}
        # лише поїзди з вільними місцями
        return {"ok": True, "trains": [t for t in trains if t["total_free"] > 0]}

    async def list_all_trains(self, route: dict) -> dict:
        """Усі поїзди маршруту (для вибору у налаштуваннях) — без фільтрів."""
        raw = await self.fetcher.fetch(route["from_id"], route["to_id"], route["date"])
        if raw is None:
            return {"ok": False, "trains": []}
        trains = parse_trains(raw)
        return {"ok": True, "trains": [
            {
                "id": t["id"], "number": t["number"],
                "departure": t["departure"], "arrival": t["arrival"],
                "classes": [
                    {"code": code, "title": info["title"]}
                    for code, info in t["seats"].items() if code.upper() != "Л"
                ],
            }
            for t in trains
        ]}

    async def fetch_seat_map_json(self, route: dict, trip_id: str, class_code: str) -> dict:
        if class_code.upper() == "Л":
            return {"ok": False, "wagons": [], "detail": "Люкс/СВ заборонено"}
        raw = await self.fetcher.fetch_seat_map(
            route["from_id"], route["to_id"], route["date"], trip_id, class_code,
        )
        if raw is None:
            return {"ok": False, "wagons": []}
        wagons = normalize_seat_map(raw)
        return {"ok": bool(wagons), "wagons": wagons}

    async def close(self):
        await self.fetcher.close()
