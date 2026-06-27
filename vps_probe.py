#!/usr/bin/env python3
"""Проба: чи пускає Cloudflare/UZ цей IP (для перевірки VPS перед переносом).

Запуск на VPS (Ubuntu/Debian):
    sudo apt update && sudo apt install -y python3-pip
    pip3 install playwright
    python3 -m playwright install --with-deps chromium
    python3 vps_probe.py

Результат:
    ✅ SUCCESS — UZ віддав дані, IP чистий → можна хостити парсер тут.
    ❌ CLOUDFLARE — той самий челендж, цей IP теж ріжуть.
"""
import asyncio
from playwright.async_api import async_playwright

FROM, TO, DATE = "2200001", "2218000", "2026-07-12"   # Київ → Львів
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36")
URL = f"https://booking.uz.gov.ua/search-trips/{FROM}/{TO}/list?startDate={DATE}"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = await browser.new_context(locale="uk-UA", user_agent=UA)
        page = await ctx.new_page()

        got = asyncio.Event()
        data = {}

        async def on_resp(r):
            if "/api/v3/trips" in r.url and "station_from_id" in r.url:
                if r.status == 200:
                    try:
                        j = await r.json()
                        data["trains"] = len((j or {}).get("direct") or [])
                    except Exception:
                        data["trains"] = "?"
                got.set()

        page.on("response", on_resp)
        print(f"Відкриваю {URL} …")
        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
            try:
                await asyncio.wait_for(got.wait(), timeout=40)
            except asyncio.TimeoutError:
                pass
        except Exception as e:
            print("Навігація впала:", e)

        if "trains" in data:
            print(f"\n✅ SUCCESS — UZ віддав дані. Поїздів у відповіді: {data['trains']}")
            print("   IP чистий, тут можна хостити парсер.")
        else:
            title = await page.title()
            body = (await page.content()).lower()
            cf = [m for m in ("cloudflare", "challenge", "cf-", "трохи зачекайте",
                              "just a moment") if m in (title.lower() + body)]
            print(f"\n❌ CLOUDFLARE / таймаут. title={title!r}  маркери={cf or 'немає'}")
            print("   Цей IP теж ріжуть — парсер тут не запрацює.")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
