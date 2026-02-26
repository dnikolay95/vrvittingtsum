# -*- coding: utf-8 -*-
"""
Telegram-бот для виртуальной примерки (на базе демки).
Запуск: python telegram_bot.py
"""
import asyncio
import logging
import os
from dotenv import load_dotenv
load_dotenv()
import re
import threading
import time
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import NetworkError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

import bot_db
import tsum_link_utils
from tryon_processor import TsumTryOnProcessor, TryOnValidationError

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN env variable is not set")
MAX_PHOTO_BYTES = 10 * 1024 * 1024  # 10 MB
BOT_PHOTOS_DIR = Path(__file__).parent / "bot_photos"
BOT_TEMP_DIR = Path(__file__).parent / "bot_temp"
BOT_RESULT_DIR = Path(__file__).parent / "bot_result"
PROMPTS_FILE = str(Path(__file__).parent / "prompts.txt")
OUTPUT_DIR = str(Path(__file__).parent / "photoresult")
TEMP_DIR = str(Path(__file__).parent / "temp_photos")

for d in (BOT_PHOTOS_DIR, BOT_TEMP_DIR, BOT_RESULT_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Состояния
AWAIT_NAME, AWAIT_PHOTO, MENU = 1, 2, 3
SINGLE_AWAIT_LINK, SINGLE_CONFIRM = 10, 11
MULTI_AWAIT_LINK, MULTI_CONFIRM = 20, 21
AWAIT_RATING, AWAIT_COMMENT, AFTER_RATING = 30, 31, 32

# Ключи в context.user_data
UD_USER_ID = "user_id"
UD_FIRST_NAME = "first_name"
UD_LAST_NAME = "last_name"
UD_PERSON_PHOTO_PATH = "person_photo_path"
UD_PRODUCT_INFO = "product_info"  # single: dict
UD_MULTI_PRODUCTS = "multi_products"  # list of dicts
UD_LAST_TRYON_ID = "last_tryon_id"
UD_LAST_TRYON_TYPE = "last_tryon_type"
UD_PENDING_RATING_TRYON_ID = "pending_rating_tryon_id"

MULTI_MAX_PRODUCTS = 5  # максимум товаров в одной мульти-примерке (как на вкладке «Наборы товаров для мульти-примерки»)


VALIDATION_FAILURE_MESSAGES = {
    "legs_not_visible": "На фото не видны ноги",
    "body_not_visible": "На фото не видно тело",
    "face_not_visible": "На фото не видно лицо",
    "multiple_people": "На фото несколько человек",
    "too_close": "Человек слишком близко к камере",
    "too_far": "Человек слишком далеко от камеры",
}


def _validation_error_text(failures: list) -> str:
    parts = [VALIDATION_FAILURE_MESSAGES.get(f, f) for f in failures]
    return "Фото не подходит для этого типа примерки: " + "; ".join(parts) + ".\nПопробуйте другое фото (полный рост, один человек)."


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Заменить фото человека", callback_data="replace_photo")],
        [InlineKeyboardButton("Примерка одной вещи", callback_data="single_tryon")],
        [InlineKeyboardButton("Примерка нескольких вещей", callback_data="multi_tryon")],
    ])


def _after_rating_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Повторная примерка", callback_data="repeat_tryon")],
        [InlineKeyboardButton("Новая примерка", callback_data="new_tryon")],
        [InlineKeyboardButton("Новое фото человека", callback_data="replace_photo")],
    ])


def parse_name(text: str) -> Optional[tuple]:
    """(first_name, last_name) или None если не подходит."""
    text = (text or "").strip()
    parts = text.split(None, 1)
    if len(parts) < 2:
        return None
    return (parts[0].strip(), parts[1].strip())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Старт команды:
    - если пользователь уже есть в БД, больше НЕ просим имя/фамилию,
      а сразу восстанавливаем user_id и показываем меню (или просим только фото);
    - если новый — просим ввести Имя Фамилию один раз.
    """
    tg_id = update.effective_user.id
    existing = bot_db.get_user_by_telegram_id(tg_id)
    if existing:
        user_id, first_name, last_name = existing
        context.user_data[UD_USER_ID] = user_id
        context.user_data[UD_FIRST_NAME] = first_name
        context.user_data[UD_LAST_NAME] = last_name

        # Проверяем, есть ли уже фото человека
        person_path = bot_db.get_latest_user_photo_path(user_id)
        if not person_path or not os.path.exists(person_path):
            await update.message.reply_text(
                f"Снова привет, {first_name} {last_name}! Загрузите фото человека (до 10 МБ)."
            )
            return AWAIT_PHOTO

        context.user_data[UD_PERSON_PHOTO_PATH] = person_path
        # Как при обычном старте: даём меню с заменой фото и новыми примерками.
        keyboard = [
            [InlineKeyboardButton("Заменить фото человека", callback_data="replace_photo")],
            [InlineKeyboardButton("Примерка одной вещи", callback_data="single_tryon")],
            [InlineKeyboardButton("Примерка нескольких вещей", callback_data="multi_tryon")],
        ]
        await update.message.reply_text(
            f"Снова привет, {first_name} {last_name}! Фото человека уже есть. Выберите действие:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return MENU

    # Новый пользователь — просим Имя Фамилию один раз
    await update.message.reply_text(
        "Добро пожаловать в виртуальную примерку.\n"
        "Введите ваши Имя и Фамилию через пробел, например: Иван Петров"
    )
    return AWAIT_NAME


async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    parsed = parse_name(update.message.text)
    if not parsed:
        await update.message.reply_text("Введите именно Имя и Фамилию через пробел, например: Иван Петров")
        return AWAIT_NAME
    first_name, last_name = parsed
    telegram_id = update.effective_user.id
    user_id = bot_db.upsert_user(telegram_id, first_name, last_name)
    context.user_data[UD_USER_ID] = user_id
    context.user_data[UD_FIRST_NAME] = first_name
    context.user_data[UD_LAST_NAME] = last_name
    await update.message.reply_text("Загрузите фото человека (до 10 МБ).")
    return AWAIT_PHOTO


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = context.user_data.get(UD_USER_ID)
    if not user_id:
        # Восстанавливаемся из БД, если бот перезапускался и user_data потерялась
        existing = bot_db.get_user_by_telegram_id(update.effective_user.id)
        if existing:
            user_id, first_name, last_name = existing
            context.user_data[UD_USER_ID] = user_id
            context.user_data[UD_FIRST_NAME] = first_name
            context.user_data[UD_LAST_NAME] = last_name
        else:
            await update.message.reply_text("Сначала введите имя и фамилию командой /start")
            return AWAIT_NAME

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    file_size = photo.file_size or 0
    if file_size > MAX_PHOTO_BYTES:
        await update.message.reply_text(
            f"Фото превышает 10 МБ ({file_size / (1024*1024):.1f} МБ). Загрузите фото до 10 МБ."
        )
        return AWAIT_PHOTO

    user_dir = BOT_PHOTOS_DIR / str(update.effective_user.id)
    user_dir.mkdir(parents=True, exist_ok=True)
    ext = "jpg"
    local_path = user_dir / f"person_{photo.file_unique_id}.{ext}"
    await file.download_to_drive(local_path)
    path_str = str(local_path)
    bot_db.save_user_photo(user_id, path_str, photo.file_id)
    context.user_data[UD_PERSON_PHOTO_PATH] = path_str

    keyboard = [
        [InlineKeyboardButton("Заменить фото человека", callback_data="replace_photo")],
        [InlineKeyboardButton("Примерка одной вещи", callback_data="single_tryon")],
        [InlineKeyboardButton("Примерка нескольких вещей", callback_data="multi_tryon")],
    ]
    await update.message.reply_text(
        "Фото сохранено. Выберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return MENU


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    data = q.data
    user_id = context.user_data.get(UD_USER_ID)
    person_path = context.user_data.get(UD_PERSON_PHOTO_PATH) or (bot_db.get_latest_user_photo_path(user_id) if user_id else None)
    if not person_path or not os.path.exists(person_path):
        await q.edit_message_text("Сначала загрузите фото человека. Отправьте фото в чат.")
        return AWAIT_PHOTO

    if data == "replace_photo":
        await q.edit_message_text("Загрузите новое фото человека (до 10 МБ).")
        return AWAIT_PHOTO
    if data == "single_tryon":
        context.user_data[UD_PRODUCT_INFO] = None
        await q.edit_message_text("Отправьте ссылку на товар Tsum (одну).")
        return SINGLE_AWAIT_LINK
    if data == "multi_tryon":
        context.user_data[UD_MULTI_PRODUCTS] = []
        await q.edit_message_text(
            f"Отправляйте ссылки на товары по одной (максимум {MULTI_MAX_PRODUCTS}). "
            "Когда добавите хотя бы 2 — можно нажать «Примерка». Или «Очистить список» и начать заново."
        )
        return await _send_multi_keyboard(q, context)

    return MENU


def _multi_keyboard(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    products = context.user_data.get(UD_MULTI_PRODUCTS) or []
    rows = []
    if len(products) >= 2:
        rows.append([InlineKeyboardButton("Примерка", callback_data="multi_do_tryon")])
    rows.append([InlineKeyboardButton("Очистить список", callback_data="multi_clear")])
    rows.append([InlineKeyboardButton("В меню", callback_data="back_menu")])
    return InlineKeyboardMarkup(rows)


async def _send_multi_keyboard(q, context: ContextTypes.DEFAULT_TYPE) -> int:
    products = context.user_data.get(UD_MULTI_PRODUCTS) or []
    text = f"Товаров в списке: {len(products)}/{MULTI_MAX_PRODUCTS}.\n"
    if products:
        for i, p in enumerate(products, 1):
            text += f"{i}. {p.get('title', '')} ({p.get('brand', '')})\n"
    text += "\nОтправьте ещё ссылку или нажмите кнопку ниже."
    await q.edit_message_text(text, reply_markup=_multi_keyboard(context))
    return MULTI_AWAIT_LINK


async def single_receive_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    link = (update.message.text or "").strip()
    info = tsum_link_utils.get_product_id_and_info(link)
    if not info:
        await update.message.reply_text("Не удалось определить товар по ссылке. Проверьте ссылку Tsum и попробуйте снова.")
        return SINGLE_AWAIT_LINK

    context.user_data[UD_PRODUCT_INFO] = info
    title = info.get("title", "")
    brand = info.get("brand", "")
    pid = info.get("product_id", "")
    text = f"Товар: {title}\nБренд: {brand}\nID: {pid}"
    await update.message.reply_text(text)

    if info.get("w2000_1"):
        await update.message.reply_photo(photo=info["w2000_1"])

    keyboard = [
        [InlineKeyboardButton("Примерить", callback_data="single_do_tryon"), InlineKeyboardButton("Заменить товар", callback_data="single_replace")],
    ]
    await update.message.reply_text("Подтвердите или замените товар:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SINGLE_CONFIRM


async def single_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "single_replace":
        await q.edit_message_text("Отправьте ссылку на товар Tsum (одну).")
        return SINGLE_AWAIT_LINK
    if q.data != "single_do_tryon":
        return SINGLE_CONFIRM

    user_id = context.user_data.get(UD_USER_ID)
    person_path = context.user_data.get(UD_PERSON_PHOTO_PATH) or bot_db.get_latest_user_photo_path(user_id)
    product_info = context.user_data.get(UD_PRODUCT_INFO)
    if not person_path or not os.path.exists(person_path):
        await q.edit_message_text("Фото человека не найдено. Загрузите фото заново.")
        return AWAIT_PHOTO
    if not product_info or not product_info.get("w2000_1"):
        await q.edit_message_text("Ошибка: нет данных о товаре.")
        return SINGLE_CONFIRM

    await q.edit_message_text("Идёт примерка, подождите…")

    def run_tryon():
        processor = TsumTryOnProcessor(prompts_file=PROMPTS_FILE)
        product_url = product_info["w2000_1"]
        product_path = processor.download_product_image(product_url, 1, BOT_TEMP_DIR)
        if not product_path:
            return None, None
        result_path = processor.process_tryon(
            person_image_path=person_path,
            product_image_path=product_path,
            product_id=int(product_info["product_id"]),
            body_part="upper",
            adapter="banana",
            product_info={"title": product_info.get("title"), "category_title": "", "color_title": "", "composition": ""},
        )
        return result_path, product_path

    loop = asyncio.get_event_loop()
    try:
        result_path, product_path = await loop.run_in_executor(None, run_tryon)
    except TryOnValidationError as ve:
        bot_db.insert_tryon(
            user_id=user_id, tryon_type="single", previous_tryon_id=None,
            person_photo_path=person_path,
            product_links=[product_info.get("product_link", "")],
            product_titles=[product_info.get("title", "")],
            product_brands=[product_info.get("brand", "")],
            product_photos_paths=[], result_photo_path=None, result_photo_url=None,
        )
        await context.bot.send_message(
            chat_id=q.message.chat_id,
            text=_validation_error_text(ve.failures),
            reply_markup=_menu_keyboard(),
        )
        return MENU

    if not result_path or not os.path.exists(result_path):
        bot_db.insert_tryon(
            user_id=user_id, tryon_type="single", previous_tryon_id=None,
            person_photo_path=person_path,
            product_links=[product_info.get("product_link", "")],
            product_titles=[product_info.get("title", "")],
            product_brands=[product_info.get("brand", "")],
            product_photos_paths=[product_path or ""],
            result_photo_path=None, result_photo_url=None,
        )
        await context.bot.send_message(
            chat_id=q.message.chat_id,
            text="Примерка не удалась. Попробуйте другое фото или товар.",
            reply_markup=_menu_keyboard(),
        )
        return MENU

    # Сохраняем результат и пишем в БД
    result_dir = Path(BOT_RESULT_DIR) / str(q.message.chat_id)
    result_dir.mkdir(parents=True, exist_ok=True)
    import shutil
    from datetime import datetime
    fn = f"tryon_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
    saved_result = result_dir / fn
    shutil.copy2(result_path, saved_result)
    result_photo_path = str(saved_result)
    tryon_id = bot_db.insert_tryon(
        user_id=context.user_data[UD_USER_ID],
        tryon_type="single",
        previous_tryon_id=None,
        person_photo_path=person_path,
        product_links=[product_info.get("product_link", "")],
        product_titles=[product_info.get("title", "")],
        product_brands=[product_info.get("brand", "")],
        product_photos_paths=[product_path or ""],
        result_photo_path=result_photo_path,
        result_photo_url=None,
    )
    context.user_data[UD_LAST_TRYON_ID] = tryon_id
    context.user_data[UD_LAST_TRYON_TYPE] = "single"
    context.user_data[UD_PENDING_RATING_TRYON_ID] = tryon_id

    with open(saved_result, "rb") as f:
        await context.bot.send_photo(chat_id=q.message.chat_id, photo=f, caption="Результат примерки")

    keyboard = [[InlineKeyboardButton("Оценить примерку", callback_data="rate_tryon")]]
    for i in range(1, 6):
        keyboard.append([InlineKeyboardButton(f"{i} ★", callback_data=f"stars_{i}")])
    await context.bot.send_message(
        chat_id=q.message.chat_id,
        text="Оцените примерку (1–5 звёзд):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return AWAIT_RATING


async def multi_receive_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    link = (update.message.text or "").strip()
    # Аккуратно парсим ссылку, чтобы при ошибке не падал весь обработчик.
    try:
        info = tsum_link_utils.get_product_id_and_info(link)
    except Exception:
        logger.exception("Ошибка при разборе ссылки для мульти-примерки: %s", link)
        await update.message.reply_text(
            "Произошла ошибка при обработке ссылки. Проверьте ссылку Tsum и попробуйте ещё раз."
        )
        return MULTI_AWAIT_LINK

    if not info:
        await update.message.reply_text(
            "Не удалось определить товар по ссылке. Проверьте ссылку Tsum и попробуйте ещё раз."
        )
        return MULTI_AWAIT_LINK

    products = context.user_data.get(UD_MULTI_PRODUCTS) or []
    if len(products) >= MULTI_MAX_PRODUCTS:
        await update.message.reply_text(f"Уже добавлено максимум {MULTI_MAX_PRODUCTS} товара. Нажмите «Примерка» или «Очистить список».")
        return MULTI_AWAIT_LINK

    products.append(info)
    context.user_data[UD_MULTI_PRODUCTS] = products
    await update.message.reply_text(f"Добавлено: {info.get('title', '')} ({info.get('brand', '')})")
    keyboard = _multi_keyboard(context)
    await update.message.reply_text(
        f"Товаров: {len(products)}/{MULTI_MAX_PRODUCTS}. Отправьте ещё ссылку или нажмите кнопку.",
        reply_markup=keyboard,
    )
    return MULTI_AWAIT_LINK


async def multi_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "multi_clear":
        context.user_data[UD_MULTI_PRODUCTS] = []
        await q.edit_message_text("Список очищен. Отправляйте ссылки на товары по одной.")
        return await _send_multi_keyboard(q, context)
    if q.data == "back_menu":
        return await back_to_menu_from_callback(q, context)
    if q.data != "multi_do_tryon":
        return MULTI_AWAIT_LINK

    products = context.user_data.get(UD_MULTI_PRODUCTS) or []
    if len(products) < 2:
        await q.edit_message_text("Нужно минимум 2 товара. Добавьте ещё ссылку.")
        return await _send_multi_keyboard(q, context)

    user_id = context.user_data.get(UD_USER_ID)
    person_path = context.user_data.get(UD_PERSON_PHOTO_PATH) or bot_db.get_latest_user_photo_path(user_id)
    if not person_path or not os.path.exists(person_path):
        await q.edit_message_text("Фото человека не найдено. Загрузите фото заново.")
        return AWAIT_PHOTO

    await q.edit_message_text("Идёт примерка нескольких вещей, подождите…")

    def run_multi():
        processor = TsumTryOnProcessor(prompts_file=PROMPTS_FILE)
        from enrich_products import fetch_product, extract_product_info
        product_image_paths = []
        product_ids = []
        for i, info in enumerate(products[:MULTI_MAX_PRODUCTS], 1):
            raw = fetch_product(info["product_id"])
            if not raw:
                continue
            ext_info = extract_product_info(raw)
            url = ext_info.get("w2000_1")
            if not url:
                continue
            path = processor.download_product_image(url, 1000 + i, BOT_TEMP_DIR)
            if path:
                product_image_paths.append(path)
                product_ids.append(info["product_id"])
        if len(product_image_paths) < 2:
            return None, None, None, None
        result_path = processor.process_tryon_multi(
            person_image_path=person_path,
            product_image_paths=product_image_paths,
            product_ids=product_ids,
            body_part="upper",
            adapter="banana",
            product_info={"title": products[0].get("title"), "category_title": "", "color_title": "", "composition": ""},
        )
        return result_path, product_image_paths, product_ids, products

    loop = asyncio.get_event_loop()
    try:
        result_path, product_paths, product_ids, products_used = await loop.run_in_executor(None, run_multi)
    except TryOnValidationError as ve:
        bot_db.insert_tryon(
            user_id=context.user_data[UD_USER_ID], tryon_type="multi", previous_tryon_id=None,
            person_photo_path=person_path,
            product_links=[p.get("product_link", "") for p in products],
            product_titles=[p.get("title", "") for p in products],
            product_brands=[p.get("brand", "") for p in products],
            product_photos_paths=[], result_photo_path=None, result_photo_url=None,
        )
        await context.bot.send_message(
            chat_id=q.message.chat_id,
            text=_validation_error_text(ve.failures),
            reply_markup=_menu_keyboard(),
        )
        return MENU

    if not result_path or not os.path.exists(result_path):
        bot_db.insert_tryon(
            user_id=context.user_data[UD_USER_ID], tryon_type="multi", previous_tryon_id=None,
            person_photo_path=person_path,
            product_links=[p.get("product_link", "") for p in products],
            product_titles=[p.get("title", "") for p in products],
            product_brands=[p.get("brand", "") for p in products],
            product_photos_paths=[], result_photo_path=None, result_photo_url=None,
        )
        await context.bot.send_message(
            chat_id=q.message.chat_id,
            text="Примерка не удалась. Попробуйте снова.",
            reply_markup=_menu_keyboard(),
        )
        return MENU

    result_dir = Path(BOT_RESULT_DIR) / str(q.message.chat_id)
    result_dir.mkdir(parents=True, exist_ok=True)
    import shutil
    from datetime import datetime
    fn = f"tryon_multi_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
    saved_result = result_dir / fn
    shutil.copy2(result_path, saved_result)
    result_photo_path = str(saved_result)
    tryon_id = bot_db.insert_tryon(
        user_id=context.user_data[UD_USER_ID],
        tryon_type="multi",
        previous_tryon_id=None,
        person_photo_path=person_path,
        product_links=[p.get("product_link", "") for p in products_used],
        product_titles=[p.get("title", "") for p in products_used],
        product_brands=[p.get("brand", "") for p in products_used],
        product_photos_paths=product_paths or [],
        result_photo_path=result_photo_path,
        result_photo_url=None,
    )
    context.user_data[UD_LAST_TRYON_ID] = tryon_id
    context.user_data[UD_LAST_TRYON_TYPE] = "multi"
    context.user_data[UD_PENDING_RATING_TRYON_ID] = tryon_id

    with open(saved_result, "rb") as f:
        await context.bot.send_photo(chat_id=q.message.chat_id, photo=f, caption="Результат примерки (несколько вещей)")

    keyboard = [[InlineKeyboardButton("Оценить примерку", callback_data="rate_tryon")]]
    for i in range(1, 6):
        keyboard.append([InlineKeyboardButton(f"{i} ★", callback_data=f"stars_{i}")])
    await context.bot.send_message(
        chat_id=q.message.chat_id,
        text="Оцените примерку (1–5 звёзд):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return AWAIT_RATING


async def rating_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    tryon_id = context.user_data.get(UD_PENDING_RATING_TRYON_ID)
    if not tryon_id:
        # Если бот перезапускался и контекст потерян — просто возвращаем пользователя в меню,
        # без повторного ввода имени и без сброса всего диалога.
        keyboard = [
            [InlineKeyboardButton("Заменить фото человека", callback_data="replace_photo")],
            [InlineKeyboardButton("Примерка одной вещи", callback_data="single_tryon")],
            [InlineKeyboardButton("Примерка нескольких вещей", callback_data="multi_tryon")],
        ]
        await q.edit_message_text(
            "Сессия оценки потеряна, но ваши примерки сохранены. Выберите действие:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return MENU

    if q.data == "rate_tryon":
        return AWAIT_RATING

    if q.data and q.data.startswith("stars_"):
        try:
            stars = int(q.data.split("_")[1])
        except Exception:
            stars = 0
        if stars < 1 or stars > 5:
            return AWAIT_RATING
        if stars <= 4:
            bot_db.insert_rating(tryon_id, stars, "")
            await q.edit_message_text("Напишите комментарий к оценке (текстом).")
            context.user_data["_pending_stars"] = stars
            context.user_data["_pending_tryon_id"] = tryon_id
            return AWAIT_COMMENT
        else:
            bot_db.insert_rating(tryon_id, stars, None)
            context.user_data.pop(UD_PENDING_RATING_TRYON_ID, None)
        keyboard = [
            [InlineKeyboardButton("Повторная примерка", callback_data="repeat_tryon")],
            [InlineKeyboardButton("Новая примерка", callback_data="new_tryon")],
            [InlineKeyboardButton("Новое фото человека", callback_data="replace_photo")],
        ]
        await q.edit_message_text("Спасибо за оценку! Что дальше?", reply_markup=InlineKeyboardMarkup(keyboard))
        return AFTER_RATING
    return AWAIT_RATING


async def receive_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    tryon_id = context.user_data.get("_pending_tryon_id")
    stars = context.user_data.get("_pending_stars", 0)
    comment = (update.message.text or "").strip()
    if tryon_id:
        bot_db.insert_rating(tryon_id, stars, comment)
    context.user_data.pop("_pending_tryon_id", None)
    context.user_data.pop("_pending_stars", None)
    context.user_data.pop(UD_PENDING_RATING_TRYON_ID, None)

    keyboard = [
        [InlineKeyboardButton("Повторная примерка", callback_data="repeat_tryon")],
        [InlineKeyboardButton("Новая примерка", callback_data="new_tryon")],
        [InlineKeyboardButton("Новое фото человека", callback_data="replace_photo")],
    ]
    await update.message.reply_text("Спасибо за комментарий! Что дальше?", reply_markup=InlineKeyboardMarkup(keyboard))
    return AFTER_RATING


async def after_rating_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "replace_photo":
        await q.edit_message_text("Загрузите новое фото человека (до 10 МБ).")
        return AWAIT_PHOTO
    if q.data == "new_tryon":
        keyboard = [
            [InlineKeyboardButton("Заменить фото человека", callback_data="replace_photo")],
            [InlineKeyboardButton("Примерка одной вещи", callback_data="single_tryon")],
            [InlineKeyboardButton("Примерка нескольких вещей", callback_data="multi_tryon")],
        ]
        await q.edit_message_text("Выберите действие:", reply_markup=InlineKeyboardMarkup(keyboard))
        return MENU
    if q.data == "repeat_tryon":
        last_id = context.user_data.get(UD_LAST_TRYON_ID)
        last_type = context.user_data.get(UD_LAST_TRYON_TYPE)
        if not last_id:
            await q.edit_message_text("Нет предыдущей примерки для повтора.")
            return AFTER_RATING
        # Повторная примерка: те же данные, новая запись с previous_tryon_id
        tryon_row = bot_db.get_tryon(last_id)
        if not tryon_row:
            await q.edit_message_text("Данные примерки не найдены.")
            return AFTER_RATING
        person_path = tryon_row["person_photo_path"]
        if not os.path.exists(person_path):
            await q.edit_message_text("Фото человека больше не найдено. Загрузите новое.")
            return AWAIT_PHOTO
        context.user_data[UD_PERSON_PHOTO_PATH] = person_path
        await q.edit_message_text("Идёт повторная примерка…")
        # Запуск примерки в фоне с теми же product_links/titles/brands
        if last_type == "single":
            # один товар: стараемся переиспользовать уже скачанное фото товара
            links = tryon_row["product_links"] or []
            link = links[0] if links else None
            if not link:
                await context.bot.send_message(
                    chat_id=q.message.chat_id, text="Не удалось повторить: товар не найден.",
                    reply_markup=_after_rating_keyboard(),
                )
                return AFTER_RATING

            saved_paths = tryon_row["product_photos_paths"] or []
            saved_product_path = saved_paths[0] if saved_paths else None
            if saved_product_path and not os.path.exists(saved_product_path):
                saved_product_path = None

            info = tsum_link_utils.get_product_id_and_info(link)
            if not info:
                await context.bot.send_message(
                    chat_id=q.message.chat_id, text="Не удалось повторить: товар не найден.",
                    reply_markup=_after_rating_keyboard(),
                )
                return AFTER_RATING

            def run():
                processor = TsumTryOnProcessor(prompts_file=PROMPTS_FILE)
                product_path = saved_product_path
                # Если локальный файл утерян — скачиваем заново
                if not product_path:
                    url = info.get("w2000_1")
                    if not url:
                        return None, None
                    product_path = processor.download_product_image(url, 1, BOT_TEMP_DIR)
                    if not product_path:
                        return None, None
                result = processor.process_tryon(
                    person_image_path=person_path,
                    product_image_path=product_path,
                    product_id=int(info["product_id"]),
                    body_part="upper",
                    adapter="banana",
                    product_info={},
                )
                return result, product_path
            loop = asyncio.get_event_loop()
            try:
                res, prod_path = await loop.run_in_executor(None, run)
            except TryOnValidationError as ve:
                bot_db.insert_tryon(
                    user_id=tryon_row["user_id"], tryon_type="repeat", previous_tryon_id=last_id,
                    person_photo_path=person_path, product_links=tryon_row["product_links"],
                    product_titles=tryon_row["product_titles"], product_brands=tryon_row["product_brands"],
                    product_photos_paths=tryon_row["product_photos_paths"],
                    result_photo_path=None, result_photo_url=None,
                )
                await context.bot.send_message(
                    chat_id=q.message.chat_id,
                    text=_validation_error_text(ve.failures),
                    reply_markup=_after_rating_keyboard(),
                )
                return AFTER_RATING
            if not res:
                bot_db.insert_tryon(
                    user_id=tryon_row["user_id"], tryon_type="repeat", previous_tryon_id=last_id,
                    person_photo_path=person_path, product_links=tryon_row["product_links"],
                    product_titles=tryon_row["product_titles"], product_brands=tryon_row["product_brands"],
                    product_photos_paths=tryon_row["product_photos_paths"],
                    result_photo_path=None, result_photo_url=None,
                )
                await context.bot.send_message(
                    chat_id=q.message.chat_id,
                    text="Повторная примерка не удалась.",
                    reply_markup=_after_rating_keyboard(),
                )
                return AFTER_RATING
            result_dir = Path(BOT_RESULT_DIR) / str(q.message.chat_id)
            result_dir.mkdir(parents=True, exist_ok=True)
            import shutil
            from datetime import datetime
            fn = f"tryon_repeat_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
            saved = result_dir / fn
            shutil.copy2(res, saved)
            new_tryon_id = bot_db.insert_tryon(
                user_id=tryon_row["user_id"],
                tryon_type="repeat",
                previous_tryon_id=last_id,
                person_photo_path=person_path,
                product_links=tryon_row["product_links"],
                product_titles=tryon_row["product_titles"],
                product_brands=tryon_row["product_brands"],
                product_photos_paths=tryon_row["product_photos_paths"],
                result_photo_path=str(saved),
                result_photo_url=None,
            )
            with open(saved, "rb") as f:
                await context.bot.send_photo(chat_id=q.message.chat_id, photo=f, caption="Повторная примерка")
            context.user_data[UD_LAST_TRYON_ID] = new_tryon_id
            # Чтобы оценка после повторной примерки работала так же, как после первой,
            # сохраняем tryon_id в UD_PENDING_RATING_TRYON_ID.
            context.user_data[UD_PENDING_RATING_TRYON_ID] = new_tryon_id
            keyboard = [[InlineKeyboardButton(f"{i} ★", callback_data=f"stars_{i}")] for i in range(1, 6)]
            await context.bot.send_message(chat_id=q.message.chat_id, text="Оцените примерку:", reply_markup=InlineKeyboardMarkup(keyboard))
            return AWAIT_RATING
        elif last_type == "multi":
            # multi repeat: делаем ту же мульти-примерку по сохранённым ссылкам,
            # стараясь максимально переиспользовать уже скачанные фото товаров.
            product_links = tryon_row["product_links"] or []
            saved_paths = tryon_row["product_photos_paths"] or []
            if len(product_links) < 2:
                await context.bot.send_message(
                    chat_id=q.message.chat_id,
                    text="Не удалось повторить мульти-примерку.",
                    reply_markup=_after_rating_keyboard(),
                )
                return AFTER_RATING

            def run_multi_repeat():
                processor = TsumTryOnProcessor(prompts_file=PROMPTS_FILE)
                paths, ids = [], []
                # Берём все ссылки из исходной мульти-примерки (до MULTI_MAX_PRODUCTS),
                # чтобы повторять набор полностью, а не только первые 2.
                for i, link in enumerate(product_links[:MULTI_MAX_PRODUCTS], 1):
                    # Сначала берём сохранённый локальный путь, если он ещё существует
                    saved_path = saved_paths[i - 1] if i - 1 < len(saved_paths) else None
                    if saved_path and not os.path.exists(saved_path):
                        saved_path = None

                    # Берём актуальный product_id (без скачивания файла)
                    info = tsum_link_utils.get_product_id_and_info(link)
                    if not info or not info.get("product_id"):
                        continue

                    product_path = saved_path
                    # Если локальный файл утерян — скачиваем заново по w2000_1
                    if not product_path:
                        url = info.get("w2000_1")
                        if not url:
                            continue
                        product_path = processor.download_product_image(url, 2000 + i, BOT_TEMP_DIR)
                        if not product_path:
                            continue

                    paths.append(product_path)
                    ids.append(info["product_id"])

                if len(paths) < 2:
                    return None, paths

                return processor.process_tryon_multi(
                    person_image_path=person_path,
                    product_image_paths=paths,
                    product_ids=ids,
                    body_part="upper",
                    adapter="banana",
                    product_info={},
                ), paths

            loop = asyncio.get_event_loop()
            try:
                res, paths = await loop.run_in_executor(None, run_multi_repeat)
            except TryOnValidationError as ve:
                bot_db.insert_tryon(
                    user_id=tryon_row["user_id"], tryon_type="repeat", previous_tryon_id=last_id,
                    person_photo_path=person_path, product_links=tryon_row["product_links"],
                    product_titles=tryon_row["product_titles"], product_brands=tryon_row["product_brands"],
                    product_photos_paths=[], result_photo_path=None, result_photo_url=None,
                )
                await context.bot.send_message(
                    chat_id=q.message.chat_id,
                    text=_validation_error_text(ve.failures),
                    reply_markup=_after_rating_keyboard(),
                )
                return AFTER_RATING
            if not res:
                bot_db.insert_tryon(
                    user_id=tryon_row["user_id"], tryon_type="repeat", previous_tryon_id=last_id,
                    person_photo_path=person_path, product_links=tryon_row["product_links"],
                    product_titles=tryon_row["product_titles"], product_brands=tryon_row["product_brands"],
                    product_photos_paths=paths or [], result_photo_path=None, result_photo_url=None,
                )
                await context.bot.send_message(
                    chat_id=q.message.chat_id,
                    text="Повторная мульти-примерка не удалась.",
                    reply_markup=_after_rating_keyboard(),
                )
                return AFTER_RATING

            result_dir = Path(BOT_RESULT_DIR) / str(q.message.chat_id)
            result_dir.mkdir(parents=True, exist_ok=True)
            import shutil
            from datetime import datetime
            fn = f"tryon_multi_repeat_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
            saved = result_dir / fn
            shutil.copy2(res, saved)
            new_tryon_id = bot_db.insert_tryon(
                user_id=tryon_row["user_id"],
                tryon_type="repeat",
                previous_tryon_id=last_id,
                person_photo_path=person_path,
                product_links=tryon_row["product_links"],
                product_titles=tryon_row["product_titles"],
                product_brands=tryon_row["product_brands"],
                product_photos_paths=paths,
                result_photo_path=str(saved),
                result_photo_url=None,
            )
            with open(saved, "rb") as f:
                await context.bot.send_photo(chat_id=q.message.chat_id, photo=f, caption="Повторная примерка (несколько вещей)")
            context.user_data[UD_LAST_TRYON_ID] = new_tryon_id
            context.user_data[UD_PENDING_RATING_TRYON_ID] = new_tryon_id
            keyboard = [[InlineKeyboardButton(f"{i} ★", callback_data=f"stars_{i}")] for i in range(1, 6)]
            await context.bot.send_message(chat_id=q.message.chat_id, text="Оцените примерку:", reply_markup=InlineKeyboardMarkup(keyboard))
            return AWAIT_RATING
    return AFTER_RATING


async def back_to_menu_from_callback(q, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = context.user_data.get(UD_USER_ID)
    person_path = context.user_data.get(UD_PERSON_PHOTO_PATH) or (bot_db.get_latest_user_photo_path(user_id) if user_id else None)
    if not person_path or not os.path.exists(person_path):
        await q.edit_message_text("Загрузите фото человека. Отправьте фото в чат.")
        return AWAIT_PHOTO
    keyboard = [
        [InlineKeyboardButton("Заменить фото человека", callback_data="replace_photo")],
        [InlineKeyboardButton("Примерка одной вещи", callback_data="single_tryon")],
        [InlineKeyboardButton("Примерка нескольких вещей", callback_data="multi_tryon")],
    ]
    await q.edit_message_text("Выберите действие:", reply_markup=InlineKeyboardMarkup(keyboard))
    return MENU


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("До свидания. Для начала введите /start")
    return ConversationHandler.END


def main():
    bot_db.init_db()
    # Авто-перезапуск при сетевых обрывах Telegram API.
    while True:
        try:
            app = Application.builder().token(BOT_TOKEN).build()

            conv = ConversationHandler(
                entry_points=[CommandHandler("start", start)],
                states={
                    AWAIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name)],
                    AWAIT_PHOTO: [MessageHandler(filters.PHOTO, receive_photo)],
                    MENU: [CallbackQueryHandler(menu_callback)],
                    SINGLE_AWAIT_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, single_receive_link)],
                    SINGLE_CONFIRM: [CallbackQueryHandler(single_confirm_callback)],
                    MULTI_AWAIT_LINK: [
                        MessageHandler(filters.TEXT & ~filters.COMMAND, multi_receive_link),
                        CallbackQueryHandler(multi_callback),
                    ],
                    MULTI_CONFIRM: [],
                    AWAIT_RATING: [CallbackQueryHandler(rating_callback)],
                    AWAIT_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_comment)],
                    AFTER_RATING: [CallbackQueryHandler(after_rating_callback)],
                },
                fallbacks=[
                    CommandHandler("cancel", cancel),
                    # Позволяем /start сработать в любом состоянии и перезапустить диалог
                    CommandHandler("start", start),
                ],
                allow_reentry=True,
            )
            app.add_handler(conv)
            logger.info("Bot started (polling)")
            app.run_polling(allowed_updates=Update.ALL_TYPES)
            # Нормальное завершение (например, Ctrl+C) — выходим из цикла.
            break
        except NetworkError as e:
            logger.error("Сетевой сбой Telegram API: %s. Перезапускаю через 5 секунд.", e)
            time.sleep(5)
            continue


if __name__ == "__main__":
    main()
