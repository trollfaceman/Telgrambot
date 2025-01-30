import os
import logging
import psycopg2
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ChatMemberUpdated


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
menu_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📢 Сообщить отчёт", callback_data="report")],
    [InlineKeyboardButton(text="📊 Запросить отчёт", callback_data="get")],
    [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")]
])



class ReportState(StatesGroup):
    waiting_for_confirmation = State()
    waiting_for_report = State()



# 📌 Команда /start
async def start_command(message: Message):
    logging.info(f"Бот получил /start в чате {message.chat.id} (тип: {message.chat.type})")
    await message.answer(
        "Привет! Я буду спрашивать тебя каждый день, что ты делал.\n\nВыбери команду ниже:",
        reply_markup=menu_keyboard
    )





# 📌 Команда /report (или кнопка "📢 Сообщить отчёт")
async def report_command(message: Message, state: FSMContext):
    # Убрана проверка на тип чата, всегда используем menu_keyboard
    await message.answer("✏️ Напиши, что ты сегодня делал...", reply_markup=menu_keyboard)
    await state.set_state(ReportState.waiting_for_report)



async def handle_report_text(message: Message, state: FSMContext):
    state_data = await state.get_state()
    
    # ✅ Если бот НЕ находится в ожидании отчёта — игнорируем сообщение
    if state_data != ReportState.waiting_for_report.state:
        return
    
    user_data = await state.get_data()
    append_mode = user_data.get("append_mode", False)

    # Проверяем, есть ли уже отчёт за сегодня
    cur.execute("SELECT text FROM reports WHERE user_id = %s AND date = %s", 
                (message.from_user.id, datetime.now().strftime("%Y-%m-%d")))
    existing_report = cur.fetchone()

    if append_mode and existing_report:
        new_text = user_data.get("report_text") + "\n" + message.text.strip()
        cur.execute("UPDATE reports SET text = %s WHERE user_id = %s AND date = %s", 
                    (new_text, message.from_user.id, datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        await message.answer("✅ Твой отчёт дополнен!", reply_markup=menu_keyboard)
        await state.clear()
        return

    if existing_report:
        edit_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить отчёт", callback_data="edit_existing_report")],
            [InlineKeyboardButton(text="➕ Добавить к отчёту", callback_data="add_to_report")]
        ])
        await message.answer("⚠️ У тебя уже есть отчёт за сегодня. Что ты хочешь сделать?", reply_markup=edit_keyboard)
        return

    text = message.text.strip()
    await state.update_data(report_text=text, append_mode=False)

    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_report")],
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data="edit_report")]
    ])

    await message.answer(f"📄 Твой отчёт:\n\n{text}\n\nТы подтверждаешь?", reply_markup=confirm_keyboard)
    await state.set_state(ReportState.waiting_for_confirmation)


# 📌 Команда /get (или кнопка "📊 Запросить отчёт")
async def get_report_command(message: Message):
    # Получаем пользователей из БД вместо переменной users
    cur.execute("SELECT DISTINCT username FROM reports WHERE username IS NOT NULL")
    users_from_db = cur.fetchall()
    
    if not users_from_db:
        await message.answer("❌ Нет доступных пользователей.")
        return

    buttons = [InlineKeyboardButton(text=f"@{user[0]}", callback_data=f"user_{user[0]}") for user in users_from_db]
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


@dp.callback_query(lambda c: c.data == "confirm_report")
async def confirm_report(callback: types.CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    new_text = user_data.get("report_text")
    append_mode = user_data.get("append_mode", False)  # Проверяем, дополняем ли мы отчёт

    cur.execute("SELECT text FROM reports WHERE user_id = %s AND date = %s", 
                (callback.from_user.id, datetime.now().strftime("%Y-%m-%d")))
    existing_report = cur.fetchone()

    if existing_report and append_mode:
        # Дописываем новый текст к старому
        updated_text = existing_report[0] + "\n" + new_text
        cur.execute("UPDATE reports SET text = %s WHERE user_id = %s AND date = %s", 
                    (updated_text, callback.from_user.id, datetime.now().strftime("%Y-%m-%d")))
    else:
        # Записываем новый отчёт
        cur.execute("INSERT INTO reports (user_id, username, text, date) VALUES (%s, %s, %s, %s)",
                    (callback.from_user.id, callback.from_user.username, new_text, datetime.now().strftime("%Y-%m-%d")))

    conn.commit()

    await callback.message.answer("✅ Отчёт записан!", reply_markup=menu_keyboard)
    await state.clear()
    await callback.answer()






@dp.callback_query(lambda c: c.data == "edit_report")
async def edit_report(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("✏️ Напиши новый отчёт:")
    await state.clear()
    await callback.answer()


@dp.callback_query(lambda c: c.data == "edit_existing_report")
async def edit_existing_report(callback: types.CallbackQuery, state: FSMContext):
    cur.execute("DELETE FROM reports WHERE user_id = %s AND date = %s",
                (callback.from_user.id, datetime.now().strftime("%Y-%m-%d")))
    conn.commit()

    await state.clear()  # Удаляем все состояния, чтобы не зависнуть в старых данных
    await callback.message.answer("✏️ Напиши новый отчёт:")
    await state.set_state(ReportState.waiting_for_confirmation)
    await callback.answer()



@dp.callback_query(lambda c: c.data == "add_to_report")
async def add_to_report(callback: types.CallbackQuery, state: FSMContext):
    cur.execute("SELECT text FROM reports WHERE user_id = %s AND date = %s",
                (callback.from_user.id, datetime.now().strftime("%Y-%m-%d")))
    existing_report = cur.fetchone()

    if existing_report:
        await state.update_data(report_text=existing_report[0], append_mode=True)  # Устанавливаем режим добавления
        await callback.message.answer("✏️ Напиши, что хочешь добавить к отчёту:")
        await state.set_state(ReportState.waiting_for_confirmation)
    else:
        await callback.message.answer("⚠️ Ошибка: нет отчёта для дополнения.")
    
    await callback.answer()





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


@dp.callback_query(lambda c: c.data == "report")
async def report_callback(callback: types.CallbackQuery):
    await report_command(callback.message)

@dp.callback_query(lambda c: c.data == "get")
async def get_callback(callback: types.CallbackQuery):
    await get_report_command(callback.message)

@dp.callback_query(lambda c: c.data == "help")
async def help_callback(callback: types.CallbackQuery):
    await help_command(callback.message)


@dp.chat_member()
async def bot_added_to_group(event: ChatMemberUpdated):
    if event.new_chat_member and event.new_chat_member.user.id == bot.id:
        logging.info(f"Бот добавлен в группу: {event.chat.id}")
        await bot.send_message(
            event.chat.id,
            "Привет! Теперь ты можешь отправлять отчёты прямо из группы. Выбери команду ниже:",
            reply_markup=menu_keyboard  # Используем инлайн-меню
        )

async def keep_awake():
    while True:
        try:
            logging.info("🔄 Keep-alive ping")
            await bot.get_me()  # Запрос к Telegram API (любой метод)
        except Exception as e:
            logging.error(f"❌ Keep-alive error: {e}")
        await asyncio.sleep(300)  # Ждать 5 минут


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
    dp.callback_query.register(confirm_report)
    dp.callback_query.register(edit_report)
    dp.callback_query.register(edit_existing_report)
    dp.callback_query.register(add_to_report)

    scheduler.add_job(daily_task, "cron", hour=18)
    scheduler.start()

    asyncio.create_task(keep_awake())

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())