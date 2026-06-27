"""Цикл моніторингу: перевіряє маршрути й шле сповіщення при появі місць."""
import asyncio
import logging
from datetime import datetime

from aiogram import Bot

from . import config, storage
from .parser import (
    parse_trains, apply_wagon_filter, seats_snapshot, diff_new_seats,
    WAGON_TYPE_NAMES,
)
from .uz_client import UZFetcher

log = logging.getLogger(__name__)


def buy_link(route: dict) -> str:
    return (
        f"{config.BASE_URL}/search-trips/"
        f"{route['from_id']}/{route['to_id']}/list?startDate={route['date']}"
    )


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
    lines = [
        "🔔 <b>З'явилися місця!</b>",
        f"🗺 {route['from_name']} → {route['to_name']}  📅 {route['date']}",
    ]
    for num, code, was, now in changes:
        t = train_map.get(num)
        dep = t["departure"] if t else "—"
        arr = t["arrival"] if t else "—"
        wagon = WAGON_TYPE_NAMES.get(code, code)
        price = ""
        if t and code in t["seats"] and t["seats"][code]["price"]:
            price = f", від {t['seats'][code]['price']} грн"
        lines.append(
            f"📈 <b>№{num}</b> ({dep}→{arr})\n"
            f"   {wagon} ({code}): <b>{now}</b> місць{price}"
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
        return apply_wagon_filter(trains, route.get("wagon_filter", ""))

    async def check_route(self, route: dict):
        trains = await self._get_trains(route)
        if trains is None:
            return
        new_snap = seats_snapshot(trains)
        changes = diff_new_seats(route.get("snapshot", {}), new_snap)
        storage.save_snapshot(route["key"], new_snap)

        if changes:
            log.info("Місця [%s]: %s", route["key"], changes)
            await self.bot.send_message(
                route["chat_id"], fmt_alert(changes, trains, route),
                parse_mode="HTML", disable_web_page_preview=True,
            )

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

    async def close(self):
        await self.fetcher.close()
