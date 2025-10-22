import os
os.system("pip install python-telegram-bot==13.15 --force-reinstall > /dev/null")


import logging
import aiohttp
import aiosqlite
import asyncio
from datetime import date, timedelta, datetime
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from dotenv import load_dotenv
import nest_asyncio

nest_asyncio.apply()
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = "schedule.db"
GROUP_ID = 8861  # замени при необходимости

logging.basicConfig(level=logging.INFO)

# ======== Инициализация базы данных ========
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS Schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                discipline TEXT NOT NULL,
                time TEXT,
                room TEXT,
                kind TEXT
            )
        """)
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_schedule_unique
            ON Schedule(date, discipline, time, room, kind)
        """)
        await db.commit()

# ======== Получение расписания ========
async def fetch_schedule(from_date, to_date):
    url = "https://www.ugrasu.ru/api/directory/lessons"
    params = {"fromdate": from_date, "todate": to_date, "groupOid": GROUP_ID}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return []
            return await resp.json()

async def cache_schedule():
    start_date = (date.today() - timedelta(days=14)).isoformat()
    end_date = (date.today() + timedelta(days=14)).isoformat()
    lessons = await fetch_schedule(start_date, end_date)
    if not lessons:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM Schedule WHERE date < ?", ((date.today() - timedelta(days=14)).isoformat(),))
        for lesson in lessons:
            lesson_date = lesson.get("date", "").replace(".", "-")
            await db.execute("""
                INSERT OR IGNORE INTO Schedule (date, discipline, time, room, kind)
                VALUES (?, ?, ?, ?, ?)
            """, (
                lesson_date,
                lesson.get("discipline", ""),
                f"{lesson.get('beginLesson','')} - {lesson.get('endLesson','')}",
                lesson.get("auditorium", ""),
                lesson.get("kindOfWork", "")
            ))
        await db.commit()

async def get_schedule_for_day(target_date):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT discipline, time, room, kind FROM Schedule WHERE date = ? ORDER BY time",
                              (target_date.isoformat(),)) as cur:
            return await cur.fetchall()

async def get_schedule_for_week(start_date):
    monday = start_date - timedelta(days=start_date.weekday())
    saturday = monday + timedelta(days=5)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT date, discipline, time, room, kind FROM Schedule WHERE date BETWEEN ? AND ? ORDER BY date, time",
            (monday.isoformat(), saturday.isoformat())
        ) as cur:
            return await cur.fetchall()

def get_kind_emoji(kind):
    if not kind:
        return ""
    k = kind.lower()
    if "лекц" in k:
        return "🎓 Лекция"
    if "практ" in k:
        return "💬 Практика"
    if "лаб" in k:
        return "🧪 Лабораторная"
    if "физ" in k:
        return "🏋️ Физическая культура"
    return f"📘 {kind.capitalize()}"

# ======== Команды ========
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Привет! Я бот расписания.\n\n"
        "📘 Команды:\n"
        "/schedule_today — расписание на сегодня\n"
        "/schedule_tomorrow — расписание на завтра\n"
        "/schedule_week — расписание на неделю"
    )

def schedule_today(update: Update, context: CallbackContext):
    asyncio.run(cache_schedule())
    rows = asyncio.run(get_schedule_for_day(date.today()))
    if not rows:
        update.message.reply_text("🎉 На сегодня занятий нет!")
        return
    text = f"📅 Расписание на сегодня ({date.today().strftime('%d.%m.%Y')})\n\n"
    for i, (disc, time_str, room, kind) in enumerate(rows, 1):
        text += f"{i}. {disc}\n{get_kind_emoji(kind)}\n🕒 {time_str}\n🏫 {room}\n\n"
    update.message.reply_text(text.strip())

def schedule_tomorrow(update: Update, context: CallbackContext):
    asyncio.run(cache_schedule())
    target = date.today() + timedelta(days=1)
    rows = asyncio.run(get_schedule_for_day(target))
    if not rows:
        update.message.reply_text("🎉 На завтра занятий нет!")
        return
    text = f"📅 Расписание на завтра ({target.strftime('%d.%m.%Y')})\n\n"
    for i, (disc, time_str, room, kind) in enumerate(rows, 1):
        text += f"{i}. {disc}\n{get_kind_emoji(kind)}\n🕒 {time_str}\n🏫 {room}\n\n"
    update.message.reply_text(text.strip())

def schedule_week(update: Update, context: CallbackContext):
    asyncio.run(cache_schedule())
    rows = asyncio.run(get_schedule_for_week(date.today()))
    if not rows:
        update.message.reply_text("🎉 На этой неделе занятий нет!")
        return

    text = "📅 Расписание на неделю:\n\n"
    current_date = ""
    for d, disc, time_str, room, kind in rows:
        if d != current_date:
            d_obj = datetime.strptime(d, "%Y-%m-%d").date()
            text += f"\n📆 {d_obj.strftime('%A, %d.%m')}\n━━━━━━━━━━━\n"
            current_date = d
        text += f"• {disc}\n{get_kind_emoji(kind)} — {time_str} ({room})\n"
    update.message.reply_text(text.strip())

# ======== Основной запуск ========
def main():
    asyncio.run(init_db())
    updater = Updater(BOT_TOKEN)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("schedule_today", schedule_today))
    dp.add_handler(CommandHandler("schedule_tomorrow", schedule_tomorrow))
    dp.add_handler(CommandHandler("schedule_week", schedule_week))

    print("✅ Бот запущен!")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
