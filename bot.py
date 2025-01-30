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

# 📌 Команда /report (или кнопка "📢 Сообщить отчёт")
async def report_command(message: Message):
    await message.answer("✏️ Напиши, что ты сегодня делал, и я запишу это как отчёт.")

async def handle_report_text(message: Message):
    text = message.text.strip()
    cur.execute("INSERT INTO reports (user_id, username, text, date) VALUES (%s, %s, %s, %s)",
                (message.from_user.id, message.from_user.username, text, datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    await message.answer("✅ Отчёт записан!", reply_markup=menu_keyboard)

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

# 📌 Обработчик выбора пользователя
@dp.callback_query(lambda c: c.data.startswith("user_"))
async def select_user(callback: types.CallbackQuery):
    username = callback.data.replace("user_", "")

    # Создаём кнопки с последними 7 датами
    dates = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    buttons = [InlineKeyboardButton(text=date, callback_data=f"date_{username}_{date}") for date in dates]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])

    await callback.message.answer(f"📅 Выбран пользователь: @{username}\nТеперь выбери дату:", reply_markup=keyboard)

# 📌 Обработчик выбора даты
@dp.callback_query(lambda c: c.data.startswith("date_"))
async def select_date(callback: types.CallbackQuery):
    _, username, date = callback.data.split("_")

    cur.execute("SELECT text FROM reports WHERE username=%s AND date=%s", (username, date))
    record = cur.fetchone()

    if record:
        await callback.message.answer(f"📝 Отчёт @{username} за {date}:\n{record[0]}")
    else:
        await callback.message.answer(f"❌ Нет отчётов @{username} за {date}.")

# 📌 Команда /help
async def help_command(message: Message):
    await message.answer("📌 Доступные команды:\n"
                         "/report – Записать отчёт о дне\n"
                         "/get – Запросить отчёт (выбор кнопками)\n"
                         "/start – Перезапустить бота", reply_markup=menu_keyboard)

# 📌 Функция отправки ежедневного запроса
async def daily_task():
    for user_id in users:
        await bot.send_message(user_id, "📝 Что ты сегодня делал? Напиши /report [твой ответ]")

async def main():
    dp.message.register(start_command, Command("start"))
    dp.message.register(report_command, Command("report"))
    dp.message.register(get_report_command, Command("get"))
    dp.message.register(help_command, Command("help"))

    dp.message.register(report_command, F.text == "📢 Сообщить отчёт")
    dp.message.register(get_report_command, F.text == "📊 Запросить отчёт")
    dp.message.register(help_command, F.text == "ℹ️ Помощь")
    dp.message.register(handle_report_text, F.text)

    dp.callback_query.register(select_user)
    dp.callback_query.register(select_date)

    scheduler.add_job(daily_task, "cron", hour=18)
    scheduler.start()

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
