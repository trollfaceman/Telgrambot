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
scheduler = AsyncIOScheduler()

# Логирование
logging.basicConfig(level=logging.INFO)

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
        [InlineKeyboardButton(text=f"{hour}:00", callback_data=f"reminder_{hour}")] for hour in range(17, 22)
    ])
    await message.answer("⏰ Выбери время напоминания:", reply_markup=keyboard)


@dp.callback_query(lambda c: c.data.startswith("reminder_"))
async def set_reminder(callback: types.CallbackQuery):
    hour = callback.data.replace("reminder_", "")
    remind_time = f"{hour}:00"  # Добавляем ":00" для полноты формата

    cur.execute("INSERT INTO reminders (user_id, remind_time) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET remind_time = %s",
                (callback.from_user.id, remind_time, remind_time))
    conn.commit()

    await callback.message.edit_text(f"✅ Напоминание установлено на {remind_time}!")


# 📌 Команда /report
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
            [InlineKeyboardButton(text="✏️ Заменить", callback_data=f"replace_{text}")]
        ])
        await message.answer(f"⚠️ Отчёт за сегодня уже существует:\n{existing_record[0]}\n\nЧто сделать?", reply_markup=keyboard)
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{text}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
        ])
        await message.answer(f"📌 Ты хочешь записать:\n{text}\n\nПодтвердить запись?", reply_markup=keyboard)


@dp.callback_query(lambda c: c.data.startswith("confirm_"))
async def confirm_report(callback: types.CallbackQuery):
    text = callback.data.replace("confirm_", "")
    date_today = datetime.now().strftime("%Y-%m-%d")

    cur.execute("INSERT INTO reports (user_id, username, text, date) VALUES (%s, %s, %s, %s)",
                (callback.from_user.id, callback.from_user.username, text, date_today))
    conn.commit()
    
    await callback.message.edit_text("✅ Отчёт записан!")

# 📌 Запрос отчёта /get
async def get_report_command(message: Message):
    cur.execute("SELECT DISTINCT username FROM reports WHERE username IS NOT NULL")
    users = cur.fetchall()

    if not users:
        await message.answer("❌ Нет пользователей с отчётами.")
        return

    buttons = [InlineKeyboardButton(text=f"@{user[0]}", callback_data=f"user_{user[0]}") for user in users]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])

    await message.answer("👤 Выбери пользователя:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data.startswith("user_"))
async def select_user(callback: types.CallbackQuery):
    username = callback.data.replace("user_", "")

    dates = [(datetime.now() - timedelta(days=i)).strftime("%d %b") for i in range(7)]
    buttons = [InlineKeyboardButton(text=date, callback_data=f"date_{username}_{(datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')}") for i, date in enumerate(dates)]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])

    await callback.message.edit_text(f"📅 Выбран пользователь: @{username}\nТеперь выбери дату:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data.startswith("date_"))
async def select_date(callback: types.CallbackQuery):
    try:
        parts = callback.data.split("_")
        if len(parts) < 3:
            return await callback.answer("Ошибка при выборе даты!")

        _, username, date = parts
    except ValueError:
        return await callback.answer("Ошибка при разборе даты!")

    cur.execute("SELECT text FROM reports WHERE username=%s AND date=%s", (username, date))
    record = cur.fetchone()

    if record:
        await callback.message.edit_text(f"📝 Отчёт @{username} за {date}:\n{record[0]}")
    else:
        await callback.message.edit_text(f"❌ Нет отчётов @{username} за {date}.")


# 📌 Отправка напоминаний
async def send_reminders():
    now = datetime.now().strftime("%H:%M")
    
    cur.execute("SELECT user_id FROM reminders WHERE remind_time = %s", (now,))
    users = cur.fetchall()

    for user_id in users:
        await bot.send_message(user_id[0], "📝 Время заполнить отчёт! Напиши /report")

# 📌 Запуск бота
async def main():
    try:
        dp.message.register(start_command, Command("start"))
        dp.message.register(help_command, Command("help"))
        dp.message.register(report_command, Command("report"))
        dp.message.register(get_report_command, Command("get"))
        dp.message.register(reminder_command, Command("reminder"))

        dp.message.register(handle_report_text, F.text)

        dp.callback_query.register(confirm_report)
        dp.callback_query.register(set_reminder)
        dp.callback_query.register(select_user)
        dp.callback_query.register(select_date)

        # 🔴 Добавляем обработчики дополнения и замены отчёта
        dp.callback_query.register(append_report)
        dp.callback_query.register(replace_report)

        scheduler.add_job(send_reminders, "cron", minute="*", second=0)
        scheduler.start()

        logging.info("Бот успешно запущен!")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, drop_pending_updates=True)
    
    except Exception as e:
        logging.error(f"Ошибка при запуске бота: {e}")


if __name__ == "__main__":
    asyncio.run(main())
