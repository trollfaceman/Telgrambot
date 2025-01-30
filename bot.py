import os
import logging
import psycopg2
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
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
    await message.answer("Напиши, что ты сегодня делал, например: /report Работал над проектом")

async def handle_report_text(message: Message):
    text = message.text.strip()
    cur.execute("INSERT INTO reports (user_id, username, text, date) VALUES (%s, %s, %s, %s)",
                (message.from_user.id, message.from_user.username, text, datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    await message.answer("✅ Отчёт записан!", reply_markup=menu_keyboard)

# 📌 Команда /get (или кнопка "📊 Запросить отчёт")
async def get_report_command(message: Message):
    await message.answer("Напиши дату и ник в формате:\n/get @username YYYY-MM-DD")

# 📌 Команда /help
async def help_command(message: Message):
    await message.answer("📌 Доступные команды:\n"
                         "/report – Записать отчёт о дне\n"
                         "/get @username YYYY-MM-DD – Получить отчёт по дате\n"
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

    # Кнопки без команд
    dp.message.register(report_command, F.text == "📢 Сообщить отчёт")
    dp.message.register(get_report_command, F.text == "📊 Запросить отчёт")
    dp.message.register(help_command, F.text == "ℹ️ Помощь")
    dp.message.register(handle_report_text, F.text)

    scheduler.add_job(daily_task, "cron", hour=18)  # Планируем задачу
    scheduler.start()  # Запускаем планировщик

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
