# UZ Tickets Monitor — Telegram Mini App

Моніторинг появи місць на потяги Укрзалізниці + сповіщення в Telegram.
Фаза 1: **уведомлення** (без автовикупу).

## Структура

```
backend/    Python: Playwright-парсер + бот + API (деплой на Railway)
frontend/   Telegram Mini App, статика (деплой на Vercel)
uz_bot.py   стара одно-файлова версія (залишена для довідки)
```

## Як працює

1. **Frontend (Vercel)** — Mini App у Telegram: пошук станцій, маршрути, статус.
2. **Backend (Railway)** — постійний воркер: керує браузером (Playwright),
   парсить рейси, щохвилини перевіряє маршрути й шле сповіщення; віддає API для Mini App.
3. Маршрути зберігаються в SQLite (не зникають при рестарті).

---

## Крок 1. Створити бота

1. У Telegram → [@BotFather](https://t.me/BotFather) → `/newbot` → отримати **токен**.

## Крок 2. Деплой backend на Railway

1. Залий папку `backend/` у GitHub-репозиторій (або весь проєкт).
2. На [railway.app](https://railway.app) → New Project → Deploy from GitHub → вибери репо,
   корінь сервісу — папка `backend`.
3. У **Variables** додай:
   - `TELEGRAM_BOT_TOKEN` — токен від BotFather
   - `WEBAPP_URL` — поки лиши порожнім, заповниш після кроку 3
   - (решта має дефолти; `PORT` Railway виставить сам)
4. Railway збудує за `Dockerfile` (Chromium уже в образі) і запустить.
5. Settings → Networking → **Generate Domain** → отримаєш URL, напр.
   `https://uz-tickets-production.up.railway.app`.

## Крок 3. Деплой frontend на Vercel

1. У `frontend/config.js` встав свій Railway-URL у `window.API_BASE`.
2. На [vercel.com](https://vercel.com) → New Project → імпорт того ж репо,
   **Root Directory = `frontend`**, framework preset — *Other* (статика).
3. Отримаєш URL, напр. `https://uz-tickets.vercel.app`.

## Крок 4. Зв'язати

1. У Railway встав `WEBAPP_URL = https://uz-tickets.vercel.app` і `CORS_ORIGINS` = той самий URL.
2. У @BotFather → `/mybots` → твій бот → Bot Settings → Menu Button → встав той самий Vercel-URL
   (щоб кнопка меню відкривала Mini App).
3. У боті надішли `/start` → кнопка **Відкрити застосунок**.

---

## Локальний запуск (для розробки)

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt
playwright install chromium
copy .env.example .env                               # заповни TELEGRAM_BOT_TOKEN, HEADLESS=false
python -m app.main
```

Mini App локально зручно гонять через `ngrok http 8080` + тимчасовий публічний URL
у `frontend/config.js`, бо Telegram пускає в Mini App лише https.

## Що далі (наступні фази)

- Автовикуп: авто-резерв місця в заказ + ссилка на оплату (потрібен UZ-акаунт).
- Більше фільтрів (час відправлення, ціна), кілька дат одразу.
