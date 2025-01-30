import os
import logging
import psycopg2
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ✅ Читаем переменные окружения
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not TOKEN or not DATABASE_URL:
    raise ValueError("Не заданы переменные окружения TOKEN и DATABASE_URL")

# Подключаемся к PostgreSQL
conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

# Создаём таблицу, если её нет
cur.execute("""
    CREATE TABLE IF NOT EXISTS reports (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        username TEXT,
        text TEXT,
        date TEXT
    )
""")
conn.commit()

# Инициализируем бота
bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# Логирование
logging.basicConfig(level=logging.INFO)

# Список пользователей
users = set()

# 📌 Главное меню кнопок
menu_keyboard = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="📢 Сообщить отчёт"), KeyboardButton(text="📊 Запросить отчёт")],
    [KeyboardButton(text="ℹ️ Помощь")]
], resize_keyboard=True)

# 📌 Команда /start
async def start_command(message: Message):
    users.add(message.from_user.id)
    await message.answer("Привет! Я буду спрашивать тебя каждый день, что ты делал.\n\nВыбери команду ниже:", reply_markup=menu_keyboard)

# 📌 Команда /help
async def help_command(message: Message):
    await message.answer(
        "📌 Доступные команды:\n"
        "📢 /report – Записать отчёт о дне\n"
        "📊 /get – Запросить отчёт (выбор кнопками)\n"
        "ℹ️ /help – Список команд\n"
        "🔄 /start – Перезапустить бота",
        reply_markup=menu_keyboard
    )

# 📌 Команда /report (или кнопка "📢 Сообщить отчёт")
async def report_command(message: Message):
    await message.answer("✏️ Напиши, что ты сегодня делал, и я запишу это как отчёт.")

async def handle_report_text(message: Message):
    text = message.text.strip()
    date_today = datetime.now().strftime("%Y-%m-%d")

    # Проверяем, есть ли уже запись за сегодня
    cur.execute("SELECT text FROM reports WHERE user_id=%s AND date=%s", (message.from_user.id, date_today))
    existing_record = cur.fetchone()

    if existing_record:
        # Если отчёт уже есть, предлагаем дополнить или заменить
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Дополнить", callback_data=f"append_{text}")],
            [InlineKeyboardButton(text="✏️ Заменить", callback_data=f"replace_{text}")]
        ])
        await message.answer(f"⚠️ Отчёт за сегодня уже существует:\n{existing_record[0]}\n\nЧто сделать?", reply_markup=keyboard)
    else:
        # Если отчёта нет, предлагаем записать новый
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{text}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
        ])
        await message.answer(f"📌 Ты хочешь записать:\n{text}\n\nПодтвердить запись?", reply_markup=keyboard)

# 📌 Обработчик подтверждения записи
@dp.callback_query(lambda c: c.data.startswith("confirm_"))
async def confirm_report(callback: types.CallbackQuery):
    text = callback.data.replace("confirm_", "")
    date_today = datetime.now().strftime("%Y-%m-%d")

    cur.execute("INSERT INTO reports (user_id, username, text, date) VALUES (%s, %s, %s, %s)",
                (callback.from_user.id, callback.from_user.username, text, date_today))
    conn.commit()
    await callback.message.answer("✅ Отчёт записан!", reply_markup=menu_keyboard)

# 📌 Обработчик дополнения отчёта
@dp.callback_query(lambda c: c.data.startswith("append_"))
async def append_report(callback: types.CallbackQuery):
    new_text = callback.data.replace("append_", "")
    date_today = datetime.now().strftime("%Y-%m-%d")

    cur.execute("SELECT text FROM reports WHERE user_id=%s AND date=%s", (callback.from_user.id, date_today))
    existing_record = cur.fetchone()

    updated_text = existing_record[0] + "\n➕ " + new_text
    cur.execute("UPDATE reports SET text=%s WHERE user_id=%s AND date=%s", (updated_text, callback.from_user.id, date_today))
    conn.commit()
    await callback.message.answer("✅ Отчёт дополнен!", reply_markup=menu_keyboard)

# 📌 Обработчик замены отчёта
@dp.callback_query(lambda c: c.data.startswith("replace_"))
async def replace_report(callback: types.CallbackQuery):
    new_text = callback.data.replace("replace_", "")
    date_today = datetime.now().strftime("%Y-%m-%d")

    cur.execute("UPDATE reports SET text=%s WHERE user_id=%s AND date=%s", (new_text, callback.from_user.id, date_today))
    conn.commit()
    await callback.message.answer("✅ Отчёт обновлён!", reply_markup=menu_keyboard)

# 📌 Команда /get (или кнопка "📊 Запросить отчёт")
async def get_report_command(message: Message):
    # Получаем всех пользователей, которые уже отправляли отчёты
    cur.execute("SELECT DISTINCT username FROM reports WHERE username IS NOT NULL")
    users = cur.fetchall()

    if not users:
        await message.answer("❌ Нет доступных пользователей.")
        return

    # Создаём кнопки с именами пользователей
    buttons = [InlineKeyboardButton(text=f"@{user[0]}", callback_data=f"user_{user[0]}") for user in users]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])

    await message.answer("👤 Выбери пользователя:", reply_markup=keyboard)

# 📌 Функция отправки ежедневного запроса
async def daily_task():
    for user_id in users:
        await bot.send_message(user_id, "📝 Что ты сегодня делал? Напиши /report [твой ответ]")

async def main():
    dp.message.register(start_command, Command("start"))
    dp.message.register(report_command, Command("report"))
    dp.message.register(get_report_command, Command("get"))
    dp.message.register(help_command, Command("help"))  # ✅ Добавлено

    dp.message.register(handle_report_text, F.text)

    dp.callback_query.register(confirm_report)
    dp.callback_query.register(append_report)
    dp.callback_query.register(replace_report)

    scheduler.add_job(daily_task, "cron", hour=18)
    scheduler.start()

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
