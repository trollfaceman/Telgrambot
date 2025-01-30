import os
import logging
import psycopg2
from fastapi import FastAPI
import uvicorn
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.executors.pool import ThreadPoolExecutor

# ✅ Читаем переменные окружения
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not TOKEN or not DATABASE_URL:
    raise ValueError("Не заданы переменные окружения TOKEN и DATABASE_URL")

# Подключаемся к PostgreSQL
conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

# Создаём таблицы, если их нет
cur.execute("""
    CREATE TABLE IF NOT EXISTS reports (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        username TEXT,
        text TEXT,
        date TEXT
    )
""")

cur.execute("""
    CREATE TABLE IF NOT EXISTS reminders (
        user_id BIGINT PRIMARY KEY,
        remind_time TEXT
    )
""")
conn.commit()

# Инициализируем бота
bot = Bot(token=TOKEN)
dp = Dispatcher()

# 📌 Настраиваем APScheduler
scheduler = AsyncIOScheduler(
    executors={
        "default": ThreadPoolExecutor(1),  # Запускает задачи в потоках
        "asyncio": AsyncIOExecutor()  # Позволяет выполнять асинхронные задачи
    }
)

# 📌 Главное меню кнопок
menu_keyboard = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="📢 Сообщить отчёт"), KeyboardButton(text="📊 Запросить отчёт")],
    [KeyboardButton(text="⏰ Установить напоминание"), KeyboardButton(text="ℹ️ Помощь")]
], resize_keyboard=True)

# 📌 Команда /start
async def start_command(message: Message):
    await message.answer("Привет! Я буду спрашивать тебя каждый день, что ты делал.\n\nВыбери команду ниже:", reply_markup=menu_keyboard)

# 📌 Команда /help
async def help_command(message: Message):
    await message.answer(
        "📌 Доступные команды:\n"
        "📢 /report – Записать отчёт о дне\n"
        "📊 /get – Запросить отчёт (выбор кнопками)\n"
        "⏰ /reminder – Установить время напоминания\n"
        "ℹ️ /help – Список команд\n"
        "🔄 /start – Перезапустить бота",
        reply_markup=menu_keyboard
    )

# 📌 Установка напоминания
async def reminder_command(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{hour}:00", callback_data=f"reminder_{hour}")] for hour in range(0, 24)
    ])
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="✏️ Ввести вручную", callback_data="reminder_manual")])
    await message.answer("⏰ Выбери время напоминания или введи вручную:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data == "reminder_manual")
async def manual_reminder(callback: types.CallbackQuery):
    await callback.message.edit_text("Введите время в формате ЧЧ:ММ (например, 14:30):")

@dp.callback_query(lambda c: c.data.startswith("reminder_"))
async def set_reminder(callback: types.CallbackQuery):
    hour = callback.data.replace("reminder_", "")
    remind_time = f"{hour}:00"
    cur.execute("INSERT INTO reminders (user_id, remind_time) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET remind_time = %s",
                (callback.from_user.id, remind_time, remind_time))
    conn.commit()
    await callback.message.edit_text(f"✅ Напоминание установлено на {remind_time}!")

# 📌 Сообщение отчёта
async def report_command(message: Message):
    await message.answer("✏️ Напиши, что ты сегодня делал, и я запишу это как отчёт.")

async def handle_report_text(message: Message):
    text = message.text.strip()
    if text.startswith("/") or text in ["📊 Запросить отчёт", "📢 Сообщить отчёт", "⏰ Установить напоминание", "ℹ️ Помощь"]:
        return
    date_today = datetime.now().strftime("%Y-%m-%d")
    cur.execute("SELECT text FROM reports WHERE user_id=%s AND date=%s", (message.from_user.id, date_today))
    existing_record = cur.fetchone()

    if existing_record:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Дополнить", callback_data=f"append_{text}")],
            [InlineKeyboardButton(text="✏️ Заменить", callback_data=f"replace_{text}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_report")]
        ])
        await message.answer(f"⚠️ Отчёт за сегодня уже существует. Что сделать?", reply_markup=keyboard)
    else:
        cur.execute("INSERT INTO reports (user_id, username, text, date) VALUES (%s, %s, %s, %s)",
                    (message.from_user.id, message.from_user.username or message.from_user.first_name, text, date_today))
        conn.commit()
        await message.answer("✅ Отчёт записан!")

# 📌 Запрос отчёта
async def get_report_command(message: Message):
    cur.execute("SELECT DISTINCT username FROM reports WHERE username IS NOT NULL")
    users = cur.fetchall()
    if not users:
        await message.answer("❌ Нет пользователей с отчётами.")
        return
    buttons = [[InlineKeyboardButton(text=f"@{user[0]}", callback_data=f"user_{user[0]}")] for user in users]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("👤 Выбери пользователя:", reply_markup=keyboard)

# 📌 Напоминания пользователям
async def send_reminders():
    now = datetime.now().strftime("%H:%M")
    cur.execute("SELECT user_id FROM reminders WHERE remind_time = %s", (now,))
    users = cur.fetchall()
    if not users:
        return
    for user_id in users:
        await bot.send_message(user_id[0], "📝 Время заполнить отчёт! Напиши /report")

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Bot is running"}

async def start_bot():
    loop = asyncio.get_event_loop()
    loop.create_task(main())  # Запускаем бота в фоне



# 📌 Запуск бота
async def main():
    dp.message.register(start_command, Command("start"))
    dp.message.register(help_command, Command("help"))
    dp.message.register(report_command, Command("report"))
    dp.message.register(get_report_command, Command("get"))
    dp.message.register(reminder_command, Command("reminder"))

    dp.message.register(handle_report_text, F.text)

    # ✅ Запускаем `send_reminders` через `run_coroutine_threadsafe`
    loop = asyncio.get_event_loop()
    scheduler.add_job(lambda: asyncio.run_coroutine_threadsafe(send_reminders(), loop), "cron", minute="*", second=0)
    scheduler.start()

    logging.info("Бот успешно запущен!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(start_bot())  # Запуск бота

    # Запускаем FastAPI сервер
    try:
        uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
    except KeyboardInterrupt:
        print("Бот остановлен")


