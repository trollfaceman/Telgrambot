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
from datetime import datetime, timedelta
import locale

# Устанавливаем русский язык для дней недели
locale.setlocale(locale.LC_TIME, "ru_RU.UTF-8")

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
# Меню для ЛС (inline-кнопки)
menu_keyboard_private = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📢 Сообщить отчёт", callback_data="report")],
    [InlineKeyboardButton(text="📊 Запросить отчёт", callback_data="get")],
    [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")]
])

# Меню для групп (обычные кнопки)
menu_keyboard_group = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📢 Сообщить отчёт"), KeyboardButton(text="📊 Запросить отчёт")],
        [KeyboardButton(text="ℹ️ Помощь")]
    ],
    resize_keyboard=True
)



class ReportState(StatesGroup):
    waiting_for_confirmation = State()
    waiting_for_report = State()



# 📌 Команда /start
async def start_command(message: Message):
    keyboard = menu_keyboard_private if message.chat.type == "private" else menu_keyboard_group
    await message.answer("Привет! Я буду спрашивать тебя каждый день, что ты делал.\n\nВыбери команду ниже:", reply_markup=keyboard)




# 📌 Команда /report (или кнопка "📢 Сообщить отчёт")
async def report_command(message: Message, state: FSMContext):
    keyboard = menu_keyboard_private if message.chat.type == "private" else menu_keyboard_group
    await message.answer("✏️ Напиши, что ты сегодня делал...", reply_markup=keyboard)
    await state.set_state(ReportState.waiting_for_report)



async def handle_report_text(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state != ReportState.waiting_for_report.state:
        return

    user_data = await state.get_data()
    append_mode = user_data.get("append_mode", False)

    cur.execute("SELECT text FROM reports WHERE user_id = %s AND date = %s", 
                (message.from_user.id, datetime.now().strftime("%Y-%m-%d")))
    existing_report = cur.fetchone()

    if append_mode and existing_report:
        new_text = existing_report[0] + "\n" + message.text.strip()  # 🟢 Добавляем к существующему отчёту
        cur.execute("UPDATE reports SET text = %s WHERE user_id = %s AND date = %s", 
                    (new_text, message.from_user.id, datetime.now().strftime("%Y-%m-%d")))
        conn.commit()

        keyboard = menu_keyboard_private if message.chat.type == "private" else menu_keyboard_group
        await message.answer("✅ Твой отчёт дополнен!", reply_markup=keyboard)
        await state.clear()  # 🟢 Сбрасываем состояние после добавления
        return

    # 🟢 Клавиатура выбора действия (изменить, добавить, отмена)
    edit_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить отчёт", callback_data="edit_existing_report")],
        [InlineKeyboardButton(text="➕ Добавить к отчёту", callback_data="add_to_report")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_report")]
    ])

    if existing_report:
        await message.answer("⚠️ У тебя уже есть отчёт за сегодня. Что ты хочешь сделать?", reply_markup=edit_keyboard)
        return

    # 🟢 Создаём новый отчёт
    text = message.text.strip()
    await state.update_data(report_text=text, append_mode=False)

    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_report")],
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data="edit_report")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_report")]
    ])

    await message.answer(f"📄 Твой отчёт:\n\n{text}\n\nТы подтверждаешь?", reply_markup=confirm_keyboard)
    await state.set_state(ReportState.waiting_for_confirmation)


# 📌 Команда /get (или кнопка "📊 Запросить отчёт")
async def get_report_command(message: Message):
    try:
        cur.execute("SELECT DISTINCT username FROM reports WHERE username IS NOT NULL")
        users_from_db = cur.fetchall()

        if not users_from_db:
            await message.answer("❌ Нет доступных пользователей.")
            return

        buttons = [InlineKeyboardButton(text=f"@{user[0]}", callback_data=f"user_{user[0]}") for user in users_from_db]
        keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])
        await message.answer("👤 Выбери пользователя:", reply_markup=keyboard)

    except Exception as e:
        logging.error(f"Ошибка БД: {e}")
        await message.answer("⚠️ Произошла ошибка. Попробуйте позже.")


# 📌 Обработчик выбора пользователя
@dp.callback_query(lambda c: c.data.startswith("user_"))
async def select_user(callback: types.CallbackQuery):
    username = callback.data.replace("user_", "")

    # Генерируем кнопки с последними 7 датами
    dates = [(datetime.now() - timedelta(days=i)) for i in range(7)]
    buttons = [
        InlineKeyboardButton(
            text=date.strftime("%d %b (%a)"),  # Формат: "31 Янв (Ср)"
            callback_data=f"date_{username}_{date.strftime('%Y-%m-%d')}"
        )
        for date in dates
    ]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])
    await callback.message.answer(
        f"📅 Выбран пользователь: @{username}\nВыбери дату отчёта:", 
        reply_markup=keyboard
    )

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

    keyboard = menu_keyboard_private if callback.message.chat.type == "private" else menu_keyboard_group
    await callback.message.answer("✅ Отчёт записан!", reply_markup=keyboard)
    await state.clear()
    await callback.answer()



@dp.callback_query(lambda c: c.data == "cancel_report")
async def cancel_report(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()  # 🟢 Сбрасываем состояние
    keyboard = menu_keyboard_private if callback.message.chat.type == "private" else menu_keyboard_group
    await callback.message.answer("🚫 Действие отменено.", reply_markup=keyboard)
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
        await state.update_data(report_text=existing_report[0], append_mode=True)  # 🟢 Устанавливаем режим добавления
        await callback.message.answer("✏️ Напиши, что хочешь добавить к отчёту:")
        await state.set_state(ReportState.waiting_for_report)  # 🟢 Устанавливаем правильное состояние
    else:
        await callback.message.answer("⚠️ Ошибка: нет отчёта для дополнения.")

    await callback.answer()






# 📌 Команда /help
async def help_command(message: Message):
    keyboard = menu_keyboard_private if message.chat.type == "private" else menu_keyboard_group
    await message.answer("📌 Доступные команды:\n"
                         "/report – Записать отчёт о дне\n"
                         "/get – Запросить отчёт (выбор кнопками)\n"
                         "/start – Перезапустить бота", reply_markup=keyboard)



# 📌 Функция отправки ежедневного запроса
async def daily_task():
    cur.execute("SELECT DISTINCT user_id FROM reports")
    users_from_db = cur.fetchall()
    for user in users_from_db:
        try:
            await bot.send_message(user[0], "📝 Что ты сегодня делал? Напиши /report")
        except Exception as e:
            logging.error(f"Ошибка отправки уведомления: {e}")


@dp.callback_query(F.data == "report")
async def report_callback(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()  # Чтобы убрать "часики" загрузки
    await report_command(callback.message, state)

@dp.callback_query(F.data == "get")
async def get_callback(callback: types.CallbackQuery):
    await callback.answer()
    await get_report_command(callback.message)

@dp.callback_query(F.data == "help")
async def help_callback(callback: types.CallbackQuery):
    await callback.answer()
    await help_command(callback.message)


@dp.message(F.text == "📢 Сообщить отчёт")
async def report_text_command(message: Message, state: FSMContext):
    await report_command(message, state)

@dp.message(F.text == "📊 Запросить отчёт")
async def get_text_command(message: Message):
    await get_report_command(message)

@dp.message(F.text == "ℹ️ Помощь")
async def help_text_command(message: Message):
    await help_command(message)




@dp.chat_member()
async def bot_added_to_group(event: ChatMemberUpdated):
    if event.new_chat_member and event.new_chat_member.user.id == bot.id:
        logging.info(f"Бот добавлен в группу: {event.chat.id}")
        await bot.send_message(
            event.chat.id,
            "Привет! Теперь ты можешь отправлять отчёты прямо из группы. Выбери команду ниже:",
            reply_markup=menu_keyboard_group  # 💡 Исправил!
        )

async def keep_awake():
    while True:
        try:
            logging.info("🔄 Keep-alive ping")
            await bot.get_me()  # Запрос к Telegram API (любой метод)
        except Exception as e:
            logging.error(f"❌ Keep-alive error: {e}")
        await asyncio.sleep(300)  # Ждать 5 минут


async def on_shutdown():
    cur.close()
    conn.close()
    logging.info("Бот остановлен. Соединение с БД закрыто.")

async def main():
    dp.message.register(start_command, Command("start"))
    dp.message.register(report_command, Command("report"))
    dp.message.register(get_report_command, Command("get"))
    dp.message.register(help_command, Command("help"))
    dp.message.register(handle_report_text, ReportState.waiting_for_report)  # 💡 Хендлер состояния теперь определён

    dp.callback_query.register(select_user)
    dp.callback_query.register(select_date)
    dp.callback_query.register(confirm_report)
    dp.callback_query.register(edit_report)
    dp.callback_query.register(edit_existing_report)
    dp.callback_query.register(add_to_report)

    # 📌 Логирование всех обновлений, чтобы видеть, что получает бот
    @dp.update()
    async def handle_all_updates(update: types.Update):
        logging.info(f"🔹 Получено обновление: {update}")

    scheduler.add_job(daily_task, "cron", hour=18)
    scheduler.start()

    asyncio.create_task(keep_awake())

    # Логирование зарегистрированных хендлеров (проверка)
    logging.info(f"✅ Зарегистрированные хендлеры: {dp.message.handlers}")

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, drop_pending_updates=True)

    try:
        await dp.start_polling(bot)
    finally:
        await on_shutdown()


if __name__ == "__main__":
    asyncio.run(main())