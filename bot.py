import asyncio
import logging
import json
import os
import time
import sqlite3
from pathlib import Path
from html import escape

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, WebAppInfo,
    InlineKeyboardMarkup, InlineKeyboardButton
)

logging.basicConfig(level=logging.INFO)

# ====== НАСТРОЙКИ ======
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN не найден. Добавь переменную окружения BOT_TOKEN.")

# ✅ username твоего бота (без @) — Grand_Cru_bot
BOT_USERNAME = os.getenv("BOT_USERNAME", "Grand_Cru_bot").replace("@", "")

# ✅ твой Telegram ID (админ)
ADMIN_ID = int(os.getenv("ADMIN_ID", "6013591658"))

# ✅ канал (если нужен пост с кнопкой) — при необходимости замени на свой канал
CHANNEL_ID = os.getenv("CHANNEL_ID", "@GRAND_CRU")

# ✅ GitHub Pages WebApp
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://tahirovdd-lang.github.io/grand-cru/?v=1")

# Persistent SQLite order history. Mount /app/data as persistent storage in Bothost.
DB_PATH = os.getenv("DB_PATH", "/app/data/grandcru.db")
def db_connect():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.execute("CREATE TABLE IF NOT EXISTS customers (telegram_id INTEGER PRIMARY KEY, username TEXT, display_name TEXT, phone TEXT, email TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS orders (order_id TEXT PRIMARY KEY, telegram_id INTEGER NOT NULL, items_json TEXT NOT NULL, total INTEGER NOT NULL, address TEXT, phone TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()
    return conn

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# ====== АНТИ-ДУБЛЬ START ======
_last_start: dict[int, float] = {}

def allow_start(user_id: int, ttl: float = 2.0) -> bool:
    now = time.time()
    prev = _last_start.get(user_id, 0.0)
    if now - prev < ttl:
        return False
    _last_start[user_id] = now
    return True

# ====== КНОПКИ ======
BTN_OPEN_MULTI = "Ochish • Открыть • Open"

def kb_webapp_reply() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_OPEN_MULTI, web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True
    )

def kb_channel_deeplink() -> InlineKeyboardMarkup:
    deeplink = f"https://t.me/{BOT_USERNAME}?startapp=menu"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=BTN_OPEN_MULTI, url=deeplink)]]
    )

# ====== ТЕКСТ ======
def welcome_text() -> str:
    return (
        "🇷🇺 Добро пожаловать в <b>GRAND CRU</b>! 👋 "
        "Выберите продукцию и оформите заявку — нажмите «Открыть» ниже.\n\n"
        "🇺🇿 <b>GRAND CRU</b> ga xush kelibsiz! 👋 "
        "Mahsulotlarni tanlang va buyurtma bering — pastdagi «Ochish» tugmasini bosing.\n\n"
        "🇬🇧 Welcome to <b>GRAND CRU</b>! 👋 "
        "Choose products and place an order — tap “Open” below."
    )

# ====== /start ======
@dp.message(CommandStart())
async def start(message: types.Message):
    if not allow_start(message.from_user.id):
        return
    await message.answer(welcome_text(), reply_markup=kb_webapp_reply())

@dp.message(Command("startapp"))
async def startapp(message: types.Message):
    if not allow_start(message.from_user.id):
        return
    await message.answer(welcome_text(), reply_markup=kb_webapp_reply())

# ====== ПОСТ В КАНАЛ ======
@dp.message(Command("post_menu"))
async def post_menu(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔️ Нет доступа.")

    text = (
        "🇷🇺 <b>GRAND CRU</b>\nНажмите кнопку ниже, чтобы открыть меню.\n\n"
        "🇺🇿 <b>GRAND CRU</b>\nPastdagi tugma orqali menyuni oching.\n\n"
        "🇬🇧 <b>GRAND CRU</b>\nTap the button below to open the menu."
    )

    try:
        sent = await bot.send_message(CHANNEL_ID, text, reply_markup=kb_channel_deeplink())
        try:
            await bot.pin_chat_message(CHANNEL_ID, sent.message_id, disable_notification=True)
            await message.answer("✅ Пост отправлен в канал и закреплён.")
        except Exception:
            await message.answer(
                "✅ Пост отправлен в канал.\n"
                "⚠️ Не удалось закрепить — дай боту право «Закреплять сообщения»."
            )
    except Exception as e:
        logging.exception("CHANNEL POST ERROR")
        await message.answer(f"❌ Ошибка отправки в канал: <code>{escape(str(e))}</code>")

# ====== ВСПОМОГАТЕЛЬНЫЕ ======
def fmt_sum(n: int) -> str:
    try:
        n = int(n)
    except Exception:
        n = 0
    return f"{n:,}".replace(",", " ")

def tg_label(u: types.User) -> str:
    return f"@{u.username}" if u.username else u.full_name

def clean_str(v) -> str:
    return ("" if v is None else str(v)).strip()

def safe_int(v, default=0) -> int:
    try:
        if v is None or isinstance(v, bool):
            return default
        if isinstance(v, (int, float)):
            return int(v)
        s = str(v).strip().replace(" ", "")
        if s == "":
            return default
        return int(float(s))
    except Exception:
        return default

# ====== ЧТЕНИЕ ЗАКАЗА ======
def build_order_lines(data: dict) -> list[str]:
    raw_items = data.get("items")
    lines: list[str] = []

    if isinstance(raw_items, list):
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            name = clean_str(it.get("name_lang")) or clean_str(it.get("name_ru")) or clean_str(it.get("id")) or "—"
            qty = safe_int(it.get("qty"), 0)
            if qty <= 0:
                continue
            price = safe_int(it.get("price"), 0)
            if price > 0:
                lines.append(f"• {escape(name)} × {qty} = {fmt_sum(price * qty)} сум")
            else:
                lines.append(f"• {escape(name)} × {qty}")

    if not lines:
        lines = ["⚠️ Корзина пустая"]

    return lines

# ====== ЗАКАЗ ИЗ WEBAPP ======
@dp.message(F.web_app_data)
async def webapp_data(message: types.Message):
    raw = message.web_app_data.data
    await message.answer("⏳ <b>Обрабатываю заказ…</b>")

    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}

    if not isinstance(data, dict):
        return await message.answer("❌ Неверный формат заказа.")
    lines = build_order_lines(data)
    if lines == ["⚠️ Корзина пустая"]:
        return await message.answer("❌ Корзина пустая. Добавьте товары.")

    total_str = clean_str(data.get("total_with_delivery")) or clean_str(data.get("total_items")) or "0"
    payment = clean_str(data.get("payment")) or "—"
    order_type = clean_str(data.get("type")) or "—"
    address = clean_str(data.get("address")) or "—"
    phone = clean_str(data.get("phone")) or "—"
    comment = clean_str(data.get("comment"))
    order_id = clean_str(data.get("order_id")) or "—"

    admin_text = (
        "🚨 <b>НОВЫЙ ЗАКАЗ GRAND CRU</b>\n"
        f"🆔 <b>{escape(order_id)}</b>\n\n"
        + "\n".join(lines) +
        f"\n\n💰 <b>Сумма:</b> {escape(total_str)} сум"
        f"\n🚚 <b>Тип:</b> {escape(order_type)}"
        f"\n💳 <b>Оплата:</b> {escape(payment)}"
        f"\n📍 <b>Адрес:</b> {escape(address)}"
        f"\n📞 <b>Телефон:</b> {escape(phone)}"
        f"\n👤 <b>Telegram:</b> {escape(tg_label(message.from_user))}"
    )

    if comment:
        admin_text += f"\n💬 <b>Комментарий:</b> {escape(comment)}"

    try:
        await bot.send_message(ADMIN_ID, admin_text)
    except Exception:
        logging.exception("ORDER DELIVERY ERROR")
        return await message.answer("⚠️ Не удалось передать заказ менеджеру. Попробуйте позже или свяжитесь с компанией напрямую.")

    # Record only successfully delivered orders. Database failures must not block Telegram confirmations.
    try:
        u = message.from_user
        with db_connect() as conn:
            conn.execute("INSERT INTO customers (telegram_id, username, display_name, phone) VALUES (?,?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username, display_name=excluded.display_name, phone=excluded.phone, updated_at=CURRENT_TIMESTAMP", (u.id, u.username, u.full_name, phone))
            conn.execute("INSERT OR IGNORE INTO orders (order_id, telegram_id, items_json, total, address, phone) VALUES (?,?,?,?,?,?)", (order_id, u.id, json.dumps(data.get("items", []), ensure_ascii=False), safe_int(data.get("total_num")), address, phone))
    except Exception:
        logging.exception("ORDER DATABASE SAVE ERROR")

    await message.answer(
        "✅ <b>Ваш заказ принят!</b>\n"
        "🙏 Спасибо, мы скоро свяжемся с вами."
    )

# ====== ЗАПУСК ======
async def main():
    # Polling does not require deleting webhook on every start. Keep pending orders.
    # Set RESET_WEBHOOK=1 only when migrating from webhook mode to polling.
    if os.getenv("RESET_WEBHOOK", "0") == "1":
        for attempt in range(5):
            try:
                await bot.delete_webhook(drop_pending_updates=False)
                break
            except Exception:
                logging.exception("Webhook reset failed (attempt %s/5)", attempt + 1)
                if attempt == 4:
                    raise
                await asyncio.sleep(min(2 ** attempt, 20))
    try:
        await dp.start_polling(bot, polling_timeout=20, handle_signals=True)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
