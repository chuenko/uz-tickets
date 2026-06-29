"""Клієнт до UZ: пошук станцій (HTTP API) + отримання рейсів (Playwright).

UZ закритий Cloudflare/reCAPTCHA, тому список рейсів дістаємо через справжній
браузер (Playwright), перехоплюючи відповідь /api/v3/trips. Пошук станцій
працює простим HTTP-запитом до app.uz.gov.ua.
"""
import asyncio
import logging
from typing import Optional
from urllib.parse import urlparse, quote

import aiohttp
from playwright.async_api import async_playwright, BrowserContext

from . import config

log = logging.getLogger(__name__)


def _proxy_opts() -> Optional[dict]:
    """Готує dict проксі для Playwright з PROXY_* (пріоритет) або PROXY-URL."""
    # Варіант 2 — окремі змінні (надійніше для спецсимволів у паролі)
    if config.PROXY_SERVER:
        server = config.PROXY_SERVER
        if "://" not in server:
            server = "http://" + server
        opts = {"server": server}
        if config.PROXY_USERNAME:
            opts["username"] = config.PROXY_USERNAME
        if config.PROXY_PASSWORD:
            opts["password"] = config.PROXY_PASSWORD
        return opts
    # Варіант 1 — один рядок-URL
    if not config.PROXY:
        return None
    u = urlparse(config.PROXY)
    if not u.hostname:
        log.warning("PROXY заданий, але не розпарсився: %r", config.PROXY)
        return None
    scheme = u.scheme or "http"
    opts = {"server": f"{scheme}://{u.hostname}:{u.port}" if u.port else f"{scheme}://{u.hostname}"}
    if u.username:
        opts["username"] = u.username
    if u.password:
        opts["password"] = u.password
    return opts

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Safari/537.36"
)

SEARCH_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "uk-UA,uk;q=0.9",
    "Origin": config.BASE_URL,
    "Referer": f"{config.BASE_URL}/",
    "X-Client-Locale": "uk",
    "X-User-Agent": "UZ/2 Web/1 User/guest",
    "User-Agent": _UA,
}


async def search_stations(query: str) -> list[dict]:
    """Пошук станцій через UZ API. Повертає [{id, name}]."""
    query = (query or "").strip()
    if len(query) < 2:
        return []
    url = f"{config.API_BASE}/api/stations?search={query}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                url, headers=SEARCH_HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    log.warning("stations search %s for %r", resp.status, query)
                    return []
                data = await resp.json(content_type=None)
                return _normalize_stations(data)
    except Exception as e:
        log.error("search_stations error: %s", e)
        return []


def _normalize_stations(data) -> list[dict]:
    items = data if isinstance(data, list) else (
        data.get("data") or data.get("stations") or data.get("items") or []
    )
    out = []
    for item in items[:12]:
        sid = str(item.get("id") or item.get("station_id") or item.get("code") or "")
        name = item.get("name") or item.get("title") or item.get("station_name") or ""
        if sid and name:
            out.append({"id": sid, "name": name})
    return out


class UZFetcher:
    """Один спільний браузер на весь процес."""

    def __init__(self):
        self._pw = None
        self.context: Optional[BrowserContext] = None
        self._start_lock = asyncio.Lock()
        self._login_pages: dict[int, object] = {}

    async def start(self):
        async with self._start_lock:
            if self.context is not None:
                return
            self._pw = await async_playwright().start()
            # Постійний профіль: зберігає логін UZ і clearance Cloudflare між запусками.
            kwargs = {
                "user_data_dir": config.USER_DATA_DIR,
                "headless": config.HEADLESS,
                "args": ["--no-sandbox", "--disable-dev-shm-usage"],
                "locale": "uk-UA",
                "user_agent": _UA,
            }
            proxy = _proxy_opts()
            if proxy:
                kwargs["proxy"] = proxy
                log.info("Браузер через проксі: %s", proxy["server"])
            self.context = await self._pw.chromium.launch_persistent_context(**kwargs)
            log.info("Браузер запущено (профіль=%s, headless=%s, proxy=%s)",
                     config.USER_DATA_DIR, config.HEADLESS, bool(proxy))

    async def fetch(self, from_id: str, to_id: str, date: str) -> Optional[dict]:
        """Повертає сирий JSON відповіді /api/v3/trips або None."""
        if self.context is None:
            await self.start()
        page = await self.context.new_page()
        result: Optional[dict] = None
        got = asyncio.Event()

        async def on_response(response):
            nonlocal result
            url = response.url
            if "/api/v3/trips" in url and "station_from_id" in url and f"date={date}" in url:
                try:
                    if response.status == 200:
                        result = await response.json()
                        log.info("API OK [%s→%s %s]", from_id, to_id, date)
                    else:
                        log.warning("API %s [%s→%s]", response.status, from_id, to_id)
                except Exception as e:
                    log.error("Читання відповіді: %s", e)
                finally:
                    got.set()

        page.on("response", on_response)
        url = f"{config.BASE_URL}/search-trips/{from_id}/{to_id}/list?startDate={date}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            try:
                await asyncio.wait_for(got.wait(), timeout=40)
            except asyncio.TimeoutError:
                log.warning("Таймаут [%s→%s %s]", from_id, to_id, date)
                await self._diagnose(page)
        except Exception as e:
            log.error("Навігація: %s", e)
        finally:
            await page.close()
        return result

    async def fetch_seat_map(
        self, from_id: str, to_id: str, date: str, trip_id: str, class_code: str
    ) -> Optional[dict]:
        """Перехоплює авторизаційні заголовки trips і ними запитує живу карту вагонів."""
        if self.context is None:
            await self.start()
        page = await self.context.new_page()
        api_headers: dict | None = None
        got = asyncio.Event()

        async def on_response(response):
            nonlocal api_headers
            if (
                "/api/v3/trips?" in response.url
                and "station_from_id" in response.url
                and f"date={date}" in response.url
            ):
                api_headers = await response.request.all_headers()
                got.set()

        page.on("response", on_response)
        search_url = f"{config.BASE_URL}/search-trips/{from_id}/{to_id}/list?startDate={date}"
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=45_000)
            await asyncio.wait_for(got.wait(), timeout=40)
            endpoint = (
                f"{config.API_BASE}/api/v3/trips/{quote(str(trip_id), safe='')}/"
                f"wagons-by-class/{quote(class_code, safe='')}"
            )
            response = await page.request.get(endpoint, headers=api_headers or {}, timeout=25_000)
            if response.status == 200:
                return await response.json()
            log.warning("Карта вагонів API %s [trip=%s class=%s]",
                        response.status, trip_id, class_code)
        except Exception as e:
            log.error("Карта вагонів: %s", e)
        finally:
            await page.close()
        return None

    async def begin_login(self, chat_id: int, phone: str) -> bool:
        """Надсилає SMS через офіційну форму УЗ; сторінку тримає до введення коду."""
        if self.context is None:
            await self.start()
        old = self._login_pages.pop(chat_id, None)
        if old:
            await old.close()
        page = await self.context.new_page()
        try:
            await page.goto(config.BASE_URL, wait_until="domcontentloaded", timeout=45_000)
            login = page.get_by_role("button", name="Увійти", exact=True)
            if await login.count() < 1:
                raise RuntimeError("Кнопку входу не знайдено")
            await login.first.click(force=True)
            field = page.get_by_role("textbox", name="Номер телефону", exact=True)
            await field.wait_for(state="visible", timeout=10_000)
            digits = "".join(ch for ch in phone if ch.isdigit())
            if digits.startswith("380"):
                digits = digits[3:]
            if len(digits) != 9:
                raise ValueError("Номер має містити 9 цифр після +380")
            await field.fill(digits)
            confirm = page.get_by_role("button", name="Підтвердити", exact=True)
            await confirm.click()
            self._login_pages[chat_id] = page
            return True
        except Exception:
            await page.close()
            raise

    async def finish_login(self, chat_id: int, code: str) -> bool:
        """Вводить SMS-код. Авторизована сесія лишається в persistent profile."""
        page = self._login_pages.get(chat_id)
        if not page:
            return False
        try:
            otp = page.locator('input[autocomplete="one-time-code"]')
            if await otp.count() != 1:
                fields = page.get_by_role("textbox")
                count = await fields.count()
                otp = fields.nth(count - 1)
            await otp.wait_for(state="visible", timeout=15_000)
            await otp.fill("".join(ch for ch in code if ch.isdigit()))
            confirm = page.get_by_role("button", name="Підтвердити", exact=True)
            await confirm.click()
            await page.wait_for_timeout(2_000)
            success = await page.get_by_role("textbox", name="Номер телефону", exact=True).count() == 0
            if success:
                self._login_pages.pop(chat_id, None)
                await page.close()
            return success
        except Exception:
            return False

    async def _diagnose(self, page) -> None:
        """На таймауті — з'ясувати, чи це Cloudflare-челендж."""
        try:
            title = await page.title()
            body = (await page.content()).lower()
            markers = ("cloudflare", "checking your browser", "challenge",
                       "captcha", "attention required", "cf-")
            hit = [m for m in markers if m in body]
            log.warning("Diag: url=%s | title=%r | cloudflare-маркери=%s",
                        page.url, title, hit or "немає")
        except Exception as e:
            log.warning("Diag не вдалось: %s", e)

    async def close(self):
        for page in list(self._login_pages.values()):
            await page.close()
        self._login_pages.clear()
        if self.context:
            await self.context.close()
        if self._pw:
            await self._pw.stop()
        self.context = None
