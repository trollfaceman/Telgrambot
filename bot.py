import os
import asyncio
import asyncpg
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# 🔹 Читаем токен бота и URL базы данных из переменных окружения
BOT_TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# 🔹 Создаем бота и диспетчер
bot = Bot(token=TOKEN)
dp = Dispatcher()

# 🔹 Создаём пул соединений с PostgreSQL
async def create_db_pool():
    return await asyncpg.create_pool(DATABASE_URL)

db_pool = None

# 🔹 Функция для создания таблицы, если её нет
async def setup_database():
    global db_pool
    db_pool = await create_db_pool()
    async with db_pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                username TEXT,
                report TEXT,
                created_at TIMESTAMP DEFAULT now()
            )
        ''')

# 🔹 Команда /start
@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer("Привет! Я буду спрашивать тебя каждый день, что ты делал. Используй /help для справки.")

# 🔹 Команда /help
@dp.message(Command("help"))
async def help_command(message: types.Message):
    help_text = """
    📌 Доступные команды:
    ✅ /report - Создать или обновить отчёт
    ✅ /get_report - Запросить отчёт
    ✅ /help - Показать справку
    """
    await message.answer(help_text)

# 🔹 Команда /report (создать или обновить отчёт)
@dp.message(Command("report"))
async def create_report(message: types.Message):
    await message.answer("Напиши, что ты сделал сегодня:")

    @dp.message()
    async def save_report(report_message: types.Message):
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO reports (user_id, username, report) VALUES ($1, $2, $3)",
                report_message.from_user.id, report_message.from_user.username, report_message.text
            )
        await report_message.answer("✅ Отчёт сохранён!")

# 🔹 Команда /get_report (запрос отчёта)
@dp.message(Command("get_report"))
async def get_report(message: types.Message):
    async with db_pool.acquire() as conn:
        users = await conn.fetch("SELECT DISTINCT user_id, username FROM reports")

    if not users:
        await message.answer("❌ Нет доступных отчётов.")
        return

    keyboard = InlineKeyboardMarkup()
    for user in users:
        keyboard.add(InlineKeyboardButton(user["username"], callback_data=f"user_{user['user_id']}"))

    await message.answer("Выбери пользователя:", reply_markup=keyboard)

# 🔹 Обработчик выбора пользователя в отчёте
@dp.callback_query(lambda call: call.data.startswith("user_"))
async def select_user(call: types.CallbackQuery):
    user_id = int(call.data.split("_")[1])
    async with db_pool.acquire() as conn:
        reports = await conn.fetch("SELECT report, created_at FROM reports WHERE user_id=$1 ORDER BY created_at DESC LIMIT 5", user_id)

    if not reports:
        await call.message.answer("❌ У этого пользователя нет отчетов.")
        return

    report_text = "\n".join([f"{r['created_at']}: {r['report']}" for r in reports])
    await call.message.answer(f"📜 Последние отчеты:\n{report_text}")

# 🔹 Ежедневное напоминание "Че делаешь?"
async def daily_reminder():
    while True:
        async with db_pool.acquire() as conn:
            users = await conn.fetch("SELECT DISTINCT user_id FROM reports")

        for user in users:
            try:
                await bot.send_message(user["user_id"], "Че делаешь? 🧐")
            except Exception as e:
                logging.error(f"Ошибка отправки сообщения: {e}")

        await asyncio.sleep(86400)  # Ждём 24 часа

# 🔹 Главная асинхронная функция (запуск бота)
async def main():
    await setup_database()
    asyncio.create_task(daily_reminder())  # Запускаем ежедневное напоминание
    await dp.start_polling(bot)

# 🔹 Запуск
if __name__ == "__main__":
    asyncio.run(main())
