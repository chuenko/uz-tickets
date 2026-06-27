"""Конфігурація з .env."""
import os
from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# ── Telegram ──────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
# куди слати сповіщення за замовчуванням (можна лишити 0 — візьмемо chat_id юзера)
TELEGRAM_CHAT_ID = _int("TELEGRAM_CHAT_ID", 0)

# ── Mini App ──────────────────────────────────
# Публічний URL фронтенду на Vercel, напр. https://uz-tickets.vercel.app
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
# Дозволені Origin для CORS (через кому). Порожньо = WEBAPP_URL.
CORS_ORIGINS = [
    o.strip().rstrip("/")
    for o in os.getenv("CORS_ORIGINS", WEBAPP_URL).split(",")
    if o.strip()
]

# ── Моніторинг ────────────────────────────────
CHECK_INTERVAL = _int("CHECK_INTERVAL", 60)   # секунди між перевірками
MAX_ROUTES = _int("MAX_ROUTES", 10)           # максимум активних маршрутів на юзера
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

# Проксі для браузера (щоб обійти Cloudflare з чистого IP).
# Працює лише РЕЗИДЕНТНИЙ/мобільний; датацентровий Cloudflare ріже так само.
# Варіант 1 — одним рядком: PROXY=http://user:pass@host:port  (або socks5://...)
# Варіант 2 — окремо (зручно, якщо у паролі спецсимволи):
#   PROXY_SERVER=http://host:port   PROXY_USERNAME=...   PROXY_PASSWORD=...
PROXY = os.getenv("PROXY", "").strip()
PROXY_SERVER = os.getenv("PROXY_SERVER", "").strip()
PROXY_USERNAME = os.getenv("PROXY_USERNAME", "").strip()
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD", "")

# ── Сервер API ────────────────────────────────
PORT = _int("PORT", 8080)                      # Railway передає PORT автоматично
HOST = os.getenv("HOST", "0.0.0.0")

# ── Сховище ───────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "uz_bot.db")
# Постійний профіль браузера (зберігає логін UZ + клиренс Cloudflare).
USER_DATA_DIR = os.getenv("USER_DATA_DIR", "uz_profile")

# ── UZ ────────────────────────────────────────
BASE_URL = "https://booking.uz.gov.ua"
API_BASE = "https://app.uz.gov.ua"


def validate() -> list[str]:
    """Повертає список критичних проблем конфігу (порожній = можна стартувати)."""
    problems = []
    if not TELEGRAM_BOT_TOKEN:
        problems.append("TELEGRAM_BOT_TOKEN не заданий")
    return problems  # WEBAPP_URL потрібен лише для Mini App, для бота — ні
