"""Telegram-бот: точка входу в Mini App + сповіщення (шле монітор)."""
import logging

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo,
)

from . import config

log = logging.getLogger(__name__)


def _kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚂 Відкрити застосунок", web_app=WebAppInfo(url=config.WEBAPP_URL))
    ]])


def setup_handlers(dp: Dispatcher):
    @dp.message(Command("start"))
    async def cmd_start(msg: Message):
        text = (
            "👋 <b>UZ Tickets Monitor</b>\n\n"
            "Стежу за появою місць на потрібні рейси й одразу сповіщаю.\n\n"
            "Натисни кнопку нижче, щоб додавати маршрути та дивитися статус."
        )
        if config.WEBAPP_URL:
            await msg.answer(text, parse_mode="HTML", reply_markup=_kb())
        else:
            await msg.answer(text + "\n\n⚠️ WEBAPP_URL ще не налаштований.", parse_mode="HTML")

    @dp.message(Command("id"))
    async def cmd_id(msg: Message):
        await msg.answer(f"Твій chat_id: <code>{msg.chat.id}</code>", parse_mode="HTML")
