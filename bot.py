import os
import logging
import psycopg2
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message
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

# 📌 Команда /start
async def start_command(message: Message):
    users.add(message.from_user.id)
    await message.answer("Привет! Я буду спрашивать тебя каждый день, что ты делал.")

# 📌 Команда /report для отправки отчёта
async def report_command(message: Message):
    text = message.text.replace("/report", "").strip()
    if not text:
        await message.answer("Напиши, что ты сегодня делал, например: /report Работал над проектом")
        return

    cur.execute("INSERT INTO reports (user_id, username, text, date) VALUES (%s, %s, %s, %s)",
                (message.from_user.id, message.from_user.username, text, datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    await message.answer("Записал!")

# 📌 Команда /get для запроса данных о пользователе
async def get_report(message: Message):
    args = message.text.split()
    if len(args) < 3:
        await message.answer("Использование: /get @username YYYY-MM-DD")
        return

    username = args[1].replace("@", "")
    date = args[2]
    
    cur.execute("SELECT text FROM reports WHERE username=%s AND date=%s", (username, date))
    record = cur.fetchone()

    if record:
        await message.answer(f"{username} {date} делал:\n{record[0]}")
    else:
        await message.answer("Нет записей на эту дату.")

# 📌 Функция отправки ежедневного запроса
async def daily_task():
    for user_id in users:
        await bot.send_message(user_id, "Что ты сегодня делал? Напиши /report [твой ответ]")

async def main():
    dp.message.register(start_command, Command("start"))
    dp.message.register(report_command, Command("report"))
    dp.message.register(get_report, Command("get"))

    scheduler.add_job(daily_task, "cron", hour=18)  # Планируем задачу
    scheduler.start()  # ✅ Запускаем в правильном месте

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
