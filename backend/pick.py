"""Інтерактивний вибір маршруту + моніторинг (локально, з домашнього IP).

Запуск (з папки backend, з заповненим .env):
    python pick.py

Питає місто відправлення/призначення (пошук як на сайті), дату — і шле статус
у Telegram. Далі можна увімкнути стеження: кожні CHECK_INTERVAL секунд перевіряє
і пінгує, щойно з'являться місця.
"""
import asyncio
import sys
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import aiohttp

from app import config
from app.uz_client import UZFetcher, search_stations
from app.parser import parse_trains, seats_snapshot, diff_new_seats, WAGON_TYPE_NAMES

BASE = "https://booking.uz.gov.ua"


async def pick_station(prompt: str) -> dict:
    while True:
        q = input(f"\n{prompt}: ").strip()
        if len(q) < 2:
            print("  Введи мінімум 2 літери.")
            continue
        stations = await search_stations(q)
        if not stations:
            print("  Нічого не знайдено, спробуй інакше.")
            continue
        for i, st in enumerate(stations, 1):
            print(f"  {i}) {st['name']}")
        choice = input("  Вибери номер (або Enter — шукати знову): ").strip()
        if not choice.isdigit():
            continue
        idx = int(choice) - 1
        if 0 <= idx < len(stations):
            print(f"  ✅ {stations[idx]['name']}")
            return stations[idx]


def ask_date() -> str:
    default = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
    d = input(f"\nДата (YYYY-MM-DD) [Enter = {default}]: ").strip()
    if not d:
        return default
    try:
        datetime.strptime(d, "%Y-%m-%d")
        return d
    except ValueError:
        print("  Невірний формат, беру", default)
        return default


def fmt(trains, route) -> str:
    link = f"{BASE}/search-trips/{route['from']['id']}/{route['to']['id']}/list?startDate={route['date']}"
    avail = [t for t in trains if t["total_free"] > 0]
    head = (f"📋 <b>{route['from']['name']} → {route['to']['name']}</b>  📅 {route['date']}\n"
            f"З місцями: {len(avail)} із {len(trains)}\n{'─'*24}")
    lines = [head]
    if not avail:
        lines.append("\n😕 Зараз вільних місць немає.")
    for t in avail:
        lines.append(f"\n🚂 <b>№{t['number']}</b>  🕐 {t['departure']} → {t['arrival']}")
        for code, info in t["seats"].items():
            if info["seats"] <= 0:
                continue
            price = f"від {info['price']} грн" if info["price"] else "—"
            lines.append(f"  ✅ {info['title']} ({code}): <b>{info['seats']}</b> місць, {price}")
    lines.append(f"\n🔗 <a href='{link}'>Купити квиток</a>")
    return "\n".join(lines)


def fmt_alert(changes, trains, route) -> str:
    link = f"{BASE}/search-trips/{route['from']['id']}/{route['to']['id']}/list?startDate={route['date']}"
    tmap = {t["number"]: t for t in trains}
    lines = ["🔔 <b>З'явилися місця!</b>",
             f"🗺 {route['from']['name']} → {route['to']['name']}  📅 {route['date']}"]
    for num, code, was, now in changes:
        t = tmap.get(num)
        dep = t["departure"] if t else "—"
        arr = t["arrival"] if t else "—"
        wagon = WAGON_TYPE_NAMES.get(code, code)
        price = ""
        if t and code in t["seats"] and t["seats"][code]["price"]:
            price = f", від {t['seats'][code]['price']} грн"
        lines.append(f"📈 <b>№{num}</b> ({dep}→{arr})\n   {wagon} ({code}): <b>{now}</b> місць{price}")
    lines.append(f"\n🔗 <a href='{link}'>Купити квиток</a>")
    return "\n\n".join(lines)


async def send(text: str):
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID or 345599904
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload) as r:
            data = await r.json()
            if not data.get("ok"):
                print("  ❌ Telegram:", data)
            else:
                print("  ✅ Надіслано в Telegram")


async def main():
    if not config.TELEGRAM_BOT_TOKEN:
        print("⚠️  Немає TELEGRAM_BOT_TOKEN у backend/.env")
        return

    print("=== Вибір маршруту ===")
    frm = await pick_station("Місто ВІДПРАВЛЕННЯ")
    to = await pick_station("Місто ПРИЗНАЧЕННЯ")
    date = ask_date()
    route = {"from": frm, "to": to, "date": date}

    f = UZFetcher()
    await f.start()
    try:
        print("\nПеревіряю наявність (запускаю браузер)…")
        raw = await f.fetch(frm["id"], to["id"], date)
        if raw is None:
            print("⚠️  Дані не отримано (таймаут/Cloudflare?).")
            return
        trains = parse_trains(raw)
        print(f"Поїздів: {len(trains)}. Шлю статус…")
        await send(fmt(trains, route))

        ans = input("\nСтежити далі й пінгувати при появі місць? (y/n): ").strip().lower()
        if ans != "y":
            return
        snap = seats_snapshot(trains)
        print(f"Стеження увімкнено (кожні {config.CHECK_INTERVAL}с). Ctrl+C — стоп.")
        while True:
            await asyncio.sleep(config.CHECK_INTERVAL)
            raw = await f.fetch(frm["id"], to["id"], date)
            if raw is None:
                print(f"  {datetime.now():%H:%M:%S} таймаут, пропускаю")
                continue
            trains = parse_trains(raw)
            new = seats_snapshot(trains)
            changes = diff_new_seats(snap, new)
            snap = new
            if changes:
                print(f"  {datetime.now():%H:%M:%S} 🔔 зміни: {changes}")
                await send(fmt_alert(changes, trains, route))
            else:
                print(f"  {datetime.now():%H:%M:%S} без змін")
    finally:
        await f.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nЗупинено.")
