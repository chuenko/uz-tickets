"""FastAPI для Telegram Mini App.

Авторизація — через перевірку initData (підпис Telegram WebApp), щоб бекенд
міг дёргати лише власник, що відкрив Mini App у Telegram.
"""
import hashlib
import hmac
import json
import logging
from urllib.parse import parse_qsl

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import config, storage, uz_client

log = logging.getLogger(__name__)


def _verify_init_data(init_data: str) -> dict:
    """Перевіряє підпис Telegram WebApp initData. Повертає user dict або кидає 401."""
    if not init_data:
        log.warning("initData ПОРОЖНІЙ (len=0) — застосунок відкрито не як Mini App?")
        raise HTTPException(401, "no initData")
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        log.warning("bad initData (len=%s): %.60s", len(init_data), init_data)
        raise HTTPException(401, "bad initData")
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        log.warning("no hash | keys=%s | len=%s", sorted(pairs), len(init_data))
        raise HTTPException(401, "no hash")
    # Нове поле signature (Ed25519) не входить у легасі HMAC-hash — виключаємо.
    pairs.pop("signature", None)

    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", config.TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received_hash):
        log.warning(
            "bad signature | keys=%s | token_len=%s | recv=%s… | calc=%s…",
            sorted(pairs), len(config.TELEGRAM_BOT_TOKEN),
            received_hash[:10], calc[:10],
        )
        raise HTTPException(401, "bad signature")

    try:
        user = json.loads(pairs.get("user", "{}"))
    except json.JSONDecodeError:
        raise HTTPException(401, "bad user")
    if not user.get("id"):
        raise HTTPException(401, "no user id")
    return user


def create_app(monitor) -> FastAPI:
    app = FastAPI(title="UZ Tickets API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    async def auth(x_init_data: str = Header(default="", alias="X-Init-Data")) -> int:
        user = _verify_init_data(x_init_data)
        return int(user["id"])

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/api/stations")
    async def stations(q: str, x_init_data: str = Header(default="", alias="X-Init-Data")):
        _verify_init_data(x_init_data)
        return {"stations": await uz_client.search_stations(q)}

    @app.get("/api/routes")
    async def routes(x_init_data: str = Header(default="", alias="X-Init-Data")):
        chat_id = await auth(x_init_data)
        return {"routes": storage.list_routes(chat_id), "max": config.MAX_ROUTES}

    class AddRouteBody(BaseModel):
        from_id: str
        from_name: str
        to_id: str
        to_name: str
        date: str
        wagon_filter: str = ""

    @app.post("/api/routes")
    async def add_route(body: AddRouteBody, x_init_data: str = Header(default="", alias="X-Init-Data")):
        chat_id = await auth(x_init_data)
        ok, result = storage.add_route(
            chat_id, body.from_id, body.from_name,
            body.to_id, body.to_name, body.date, body.wagon_filter,
        )
        if not ok:
            raise HTTPException(400, result)
        return {"ok": True, "key": result}

    @app.delete("/api/routes/{key}")
    async def delete_route(key: str, x_init_data: str = Header(default="", alias="X-Init-Data")):
        chat_id = await auth(x_init_data)
        route = storage.get_route(key)
        if not route or route["chat_id"] != chat_id:
            raise HTTPException(404, "not found")
        storage.delete_route(key)
        return {"ok": True}

    @app.post("/api/routes/{key}/active")
    async def toggle(key: str, active: bool, x_init_data: str = Header(default="", alias="X-Init-Data")):
        chat_id = await auth(x_init_data)
        route = storage.get_route(key)
        if not route or route["chat_id"] != chat_id:
            raise HTTPException(404, "not found")
        storage.set_active(key, active)
        return {"ok": True}

    @app.get("/api/routes/{key}/status")
    async def status(key: str, x_init_data: str = Header(default="", alias="X-Init-Data")):
        chat_id = await auth(x_init_data)
        route = storage.get_route(key)
        if not route or route["chat_id"] != chat_id:
            raise HTTPException(404, "not found")
        return await monitor.fetch_status_json(route)

    return app
