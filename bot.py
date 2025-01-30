import os
import logging
import psycopg2
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# 🔧 Читаем переменные окружения
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# Подключаемся к базе данных PostgreSQL
conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

# Создаем таблицу, если её нет
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
dp = Dispatcher(bot)
scheduler = AsyncIOScheduler()

# Логирование
logging.basicConfig(level=logging.INFO)

# Список пользователей
users = set()

# 📌 Команда /start
@dp.message_handler(commands=['start'])
async def start_command(message: types.Message):
    users.add(message.from_user.id)
    await message.reply("Привет! Я буду спрашивать тебя каждый день, что ты делал.")

# 📌 Команда /report для отправки отчёта
@dp.message_handler(commands=['report'])
async def report_command(message: types.Message):
    text = message.text.replace("/report", "").strip()
    if not text:
        await message.reply("Напиши, что ты сегодня делал, например: /report Работал над проектом")
        return

    cur.execute("INSERT INTO reports (user_id, username, text, date) VALUES (%s, %s, %s, %s)",
                (message.from_user.id, message.from_user.username, text, datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    await message.reply("Записал!")

# 📌 Команда /get для запроса данных о пользователе
@dp.message_handler(commands=['get'])
async def get_report(message: types.Message):
    args = message.text.split()
    if len(args) < 3:
        await message.reply("Использование: /get @username YYYY-MM-DD")
        return

    username = args[1].replace("@", "")
    date = args[2]
    
    cur.execute("SELECT text FROM reports WHERE username=%s AND date=%s", (username, date))
    record = cur.fetchone()

    if record:
        await message.reply(f"{username} {date} делал:\n{record[0]}")
    else:
        await message.reply("Нет записей на эту дату.")

# 📌 Функция отправки ежедневного запроса
async def daily_task():
    for user_id in users:
        await bot.send_message(user_id, "Что ты сегодня делал? Напиши /report [твой ответ]")

# Запускаем ежедневное напоминание в 18:00
scheduler.add_job(daily_task, "cron", hour=18)
scheduler.start()

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
