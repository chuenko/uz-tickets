#!/usr/bin/env python3
"""
UZ (Укрзалізниця) Train Ticket Monitor — Playwright версія
Перехоплює реальні API запити через браузер, обходить Cloudflare/reCAPTCHA.
Відправляє сповіщення в Telegram при появі місць.

Встановлення:
    pip install playwright aiohttp python-dotenv
    playwright install chromium

Запуск:
    python uz_monitor.py
"""

import asyncio
import logging
import os
import sys
from datetime import datetime
from typing import Optional

import aiohttp
from dotenv import load_dotenv
from playwright.async_api import async_playwright, BrowserContext

load_dotenv()

# ─────────────────────────────────────────────
# КОНФІГУРАЦІЯ — заповніть тут або в .env
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "YOUR_CHAT_ID")

STATION_FROM   = os.getenv("STATION_FROM",   "2200001")   # Київ-Пасажирський
STATION_TO     = os.getenv("STATION_TO",     "5310017")   # Львів
SEARCH_DATE    = os.getenv("SEARCH_DATE",    "2026-05-21")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))

TRAIN_FILTER_RAW   = os.getenv("TRAIN_FILTER", "")
TRAIN_FILTER       = [t.strip() for t in TRAIN_FILTER_RAW.split(",") if t.strip()]

WAGON_TYPE_FILTER_RAW = os.getenv("WAGON_TYPES", "")
WAGON_TYPE_FILTER     = [w.strip() for w in WAGON_TYPE_FILTER_RAW.split(",") if w.strip()]

# True = фоновий режим (без вікна), False = видно браузер
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

# ─────────────────────────────────────────────
BASE_URL = "https://booking.uz.gov.ua"

WAGON_TYPE_NAMES = {
    "П": "Плацкарт", "К": "Купе", "Л": "Люкс/СВ",
    "С": "Сидячий",  "О": "Загальний",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("uz_monitor.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────
async def send_telegram(text: str) -> bool:
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN":
        log.info(f"[Telegram STUB]\n{text[:300]}")
        return True
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                ok = resp.status == 200
                if not ok:
                    log.warning(f"Telegram {resp.status}: {await resp.text()}")
                return ok
    except Exception as e:
        log.error(f"Telegram error: {e}")
        return False


# ─────────────────────────────────────────────
# ПАРСИНГ
# ─────────────────────────────────────────────
def parse_trains(data) -> list[dict]:
    # Поезда лежат в data["direct"] (прямые рейсы)
    items = []
    if isinstance(data, dict):
        items = data.get("direct") or data.get("data") or data.get("trips") or []
    elif isinstance(data, list):
        items = data

    trains = []
    for raw in items:
        train_obj = raw.get("train", {})
        number    = train_obj.get("number", "?")
        trip_id   = raw.get("id", "")

        # Время — Unix timestamp → читаемый формат
        def fmt_ts(ts):
            if not ts:
                return "—"
            try:
                return datetime.fromtimestamp(int(ts)).strftime("%d.%m %H:%M")
            except:
                return str(ts)

        departure = fmt_ts(raw.get("depart_at"))
        arrival   = fmt_ts(raw.get("arrive_at"))

        if TRAIN_FILTER and number not in TRAIN_FILTER:
            continue

        wagon_classes = train_obj.get("wagon_classes") or []
        seats = {}
        for wc in wagon_classes:
            code  = wc.get("id", "?")                          # "К", "П" и т.д.
            title = wc.get("name") or WAGON_TYPE_NAMES.get(code, code)
            count = int(wc.get("free_seats") or 0)
            price = round(float(wc.get("price") or 0) / 100)   # копейки → гривны

            if WAGON_TYPE_FILTER and code not in WAGON_TYPE_FILTER:
                continue
            seats[code] = {"title": title, "seats": count, "price": price}

        trains.append({
            "id": trip_id, "number": number,
            "departure": departure, "arrival": arrival, "seats": seats,
        })
    return trains


def fmt_train(t: dict, is_new: bool = False) -> str:
    lines = [
        f"{'🆕 ' if is_new else ''}🚂 Поїзд <b>№{t['number']}</b>",
        f"🕐 {t['departure']} → {t['arrival']}",
    ]
    if t["seats"]:
        for code, info in t["seats"].items():
            price_str = f"{info['price']:.0f} грн" if info["price"] else "—"
            lines.append(f"  • {info['title']} ({code}): <b>{info['seats']} місць</b>, від {price_str}")
    else:
        lines.append("  ❌ Місць немає")
    return "\n".join(lines)


def build_alert(trains, flags) -> str:
    link = f"{BASE_URL}/search-trips/{STATION_FROM}/{STATION_TO}/list?startDate={SEARCH_DATE}"
    parts = [f"🎫 <b>З'явились місця!</b>  📅 {SEARCH_DATE}\n"]
    for t, f in zip(trains, flags):
        parts.append(fmt_train(t, f))
    parts.append(f"\n🔗 <a href='{link}'>Купити квиток</a>")
    return "\n\n".join(parts)


def build_summary(trains) -> str:
    link = f"{BASE_URL}/search-trips/{STATION_FROM}/{STATION_TO}/list?startDate={SEARCH_DATE}"
    parts = [
        f"📋 <b>УЗ Моніторинг</b>   {SEARCH_DATE}\n"
        f"Знайдено поїздів: {len(trains)}\n{'─'*28}",
    ]
    for t in trains:
        parts.append(fmt_train(t))
    parts.append(f"⏱ {datetime.now().strftime('%H:%M:%S')}  🔗 <a href='{link}'>Відкрити</a>")
    return "\n\n".join(parts)


# ─────────────────────────────────────────────
# PLAYWRIGHT FETCHER
# ─────────────────────────────────────────────
class UZFetcher:
    def __init__(self):
        self._pw = None
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

    async def fetch(self) -> Optional[list | dict]:
        """Відкриває сторінку пошуку і перехоплює відповідь /api/v3/trips."""
        page = await self.context.new_page()
        result = None
        got = asyncio.Event()

        async def on_response(response):
            nonlocal result
            url = response.url
            if (
                "/api/v3/trips" in url
                and "station_from_id" in url
                and f"date={SEARCH_DATE}" in url
            ):
                try:
                    if response.status == 200:
                        result = await response.json()
                        log.info(f"API OK: {url[:100]}")
                    else:
                        body = await response.text()
                        log.warning(f"API {response.status}: {body[:200]}")
                except Exception as e:
                    log.error(f"Помилка читання відповіді: {e}")
                finally:
                    got.set()

        page.on("response", on_response)

        url = (
            f"{BASE_URL}/search-trips/{STATION_FROM}/{STATION_TO}"
            f"/list?startDate={SEARCH_DATE}"
        )
        try:
            log.info(f"Відкриваю {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            try:
                await asyncio.wait_for(got.wait(), timeout=20)
            except asyncio.TimeoutError:
                log.warning("Таймаут: API не відповів за 20с")
        except Exception as e:
            log.error(f"Помилка навігації: {e}")
        finally:
            await page.close()

        return result

    async def close(self):
        if self.context:  await self.context.close()
        if self._browser: await self._browser.close()
        if self._pw:      await self._pw.stop()


# ─────────────────────────────────────────────
# МОНІТОР
# ─────────────────────────────────────────────
class UZMonitor:
    def __init__(self):
        self.fetcher   = UZFetcher()
        self.prev      : dict[str, dict] = {}
        self.first_run = True

    async def start(self):
        await self.fetcher.start()
        log.info(
            f"Моніторинг: {STATION_FROM}→{STATION_TO} "
            f"| {SEARCH_DATE} | інтервал {CHECK_INTERVAL}с"
        )
        try:
            while True:
                await self.check()
                log.info(f"Наступна перевірка через {CHECK_INTERVAL}с")
                await asyncio.sleep(CHECK_INTERVAL)
        except asyncio.CancelledError:
            pass
        finally:
            await self.fetcher.close()

    async def check(self):
        log.info("─── Перевірка ───")
        raw = await self.fetcher.fetch()
        if raw is None:
            log.warning("Дані не отримано")
            return

        trains = parse_trains(raw)
        log.info(f"Поїздів: {len(trains)}")
        for t in trains:
            s = ", ".join(f"{v['title']}: {v['seats']}" for v in t["seats"].values()) or "нема місць"
            log.info(f"  {t['number']}  {t['departure']}→{t['arrival']}  |  {s}")

        alerts, flags = [], []
        for t in trains:
            prev      = self.prev.get(t["number"])
            had       = any(v["seats"] > 0 for v in prev["seats"].values()) if prev else None
            has       = any(v["seats"] > 0 for v in t["seats"].values())
            is_new    = prev is None
            appeared  = had is False and has
            if has and (is_new or appeared):
                alerts.append(t)
                flags.append(is_new)

        self.prev = {t["number"]: t for t in trains}

        if alerts:
            log.info(f"🔔 Алерт: {len(alerts)} поїздів з місцями")
            await send_telegram(build_alert(alerts, flags))
        else:
            log.info("Нових місць немає")

        if self.first_run:
            self.first_run = False
            await send_telegram(build_summary(trains))


# ─────────────────────────────────────────────
async def main():
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN":
        log.warning("⚠️  Telegram не налаштовано — вивід лише в консоль")
    await UZMonitor().start()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Зупинено.")
