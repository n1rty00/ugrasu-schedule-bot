import nest_asyncio
nest_asyncio.apply()  # фикс для IDE с уже запущенным event loop

import asyncio
from datetime import date, timedelta, datetime
import aiohttp
import aiosqlite
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# === Конфигурация ===
import os
from dotenv import load_dotenv
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN") # <-- вставь сюда токен своего бота
DB_PATH = "schedule.db"
GROUP_ID = 8861  # ID вашей группы в API УГРАСУ

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

# ======== Получение расписания с API ========
async def fetch_schedule(from_date: str, to_date: str):
    url = "https://www.ugrasu.ru/api/directory/lessons"
    params = {
        "fromdate": from_date,
        "todate": to_date,
        "groupOid": GROUP_ID
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as response:
            if response.status != 200:
                print("Ошибка при запросе:", response.status)
                return []
            return await response.json()

# ======== Кэширование расписания в SQLite ========
async def cache_schedule():
    start_date = (date.today() - timedelta(days=14)).isoformat()
    end_date = (date.today() + timedelta(days=14)).isoformat()
    lessons = await fetch_schedule(start_date, end_date)
    if not lessons:
        print("Расписание пустое")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cutoff = (date.today() - timedelta(days=14)).isoformat()
        await db.execute("DELETE FROM Schedule WHERE date < ?", (cutoff,))
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
                lesson.get("kindOfWork", "")  # тип занятия (лекция, практика и т.д.)
            ))
        await db.commit()
    print("✅ Расписание обновлено")

# ======== Получение расписания из базы ========
async def get_schedule_for_day(target_date: date):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT discipline, time, room, kind FROM Schedule WHERE date = ? ORDER BY time",
            (target_date.isoformat(),)
        ) as cursor:
            return await cursor.fetchall()

async def get_schedule_for_week(start_date: date):
    monday = start_date - timedelta(days=start_date.weekday())
    saturday = monday + timedelta(days=5)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT date, discipline, time, room, kind FROM Schedule WHERE date BETWEEN ? AND ? ORDER BY date, time",
            (monday.isoformat(), saturday.isoformat())
        ) as cursor:
            return await cursor.fetchall()

# ======== Сопоставление типов занятий с эмодзи ========
def get_kind_emoji(kind: str) -> str:
    if not kind:
        return ""
    kind = kind.lower()
    if "лекц" in kind:
        return "🎓 Лекция"
    elif "практ" in kind or "семин" in kind:
        return "💬 Практика"
    elif "лаб" in kind:
        return "🧪 Лабораторная"
    elif "физ" in kind:
        return "🏋️ Физическая культура"
    else:
        return f"📘 {kind.capitalize()}"

# ======== Команды бота ========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 Привет! Я бот расписания вашей группы.\n\n"
        "📘 Команды:\n"
        "• /schedule_today — расписание на сегодня\n"
        "• /schedule_tomorrow — расписание на завтра\n"
        "• /schedule_week — расписание с понедельника по субботу"
    )
    await update.message.reply_text(text)

async def schedule_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cache_schedule()
    rows = await get_schedule_for_day(date.today())
    if not rows:
        await update.message.reply_text("🎉 На сегодня занятий нет!")
        return

    text = f"📅 <b>Расписание на сегодня ({date.today().strftime('%d.%m.%Y')})</b>\n\n"
    for i, (discipline, time_str, room, kind) in enumerate(rows, start=1):
        kind_display = get_kind_emoji(kind)
        text += f"{i}. <b>{discipline}</b>\n{kind_display}\n🕒 {time_str}\n🏫 {room}\n\n"
    await update.message.reply_html(text.strip())

async def schedule_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cache_schedule()
    target = date.today() + timedelta(days=1)
    rows = await get_schedule_for_day(target)
    if not rows:
        await update.message.reply_text("🎉 На завтра занятий нет!")
        return

    text = f"📅 <b>Расписание на завтра ({target.strftime('%d.%m.%Y')})</b>\n\n"
    for i, (discipline, time_str, room, kind) in enumerate(rows, start=1):
        kind_display = get_kind_emoji(kind)
        text += f"{i}. <b>{discipline}</b>\n{kind_display}\n🕒 {time_str}\n🏫 {room}\n\n"
    await update.message.reply_html(text.strip())

async def schedule_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cache_schedule()
    rows = await get_schedule_for_week(date.today())
    if not rows:
        await update.message.reply_text("🎉 На этой неделе занятий нет!")
        return

    text = "📅 <b>Расписание на неделю (Пн–Сб)</b>\n\n"
    current_date = ""

    weekdays = {
        0: "Понедельник",
        1: "Вторник",
        2: "Среда",
        3: "Четверг",
        4: "Пятница",
        5: "Суббота",
        6: "Воскресенье"
    }

    for d, discipline, time_str, room, kind in rows:
        d_obj = datetime.strptime(d, "%Y-%m-%d").date()
        weekday_name = weekdays[d_obj.weekday()]
        if d != current_date:
            text += f"\n<b>📆 {weekday_name}, {d_obj.strftime('%d.%m')}</b>\n"
            text += "━━━━━━━━━━━━━━━━━━━━━━\n"
            current_date = d
        kind_display = get_kind_emoji(kind)
        text += f"• <b>{discipline}</b>\n{kind_display} — {time_str} ({room})\n"
    await update.message.reply_html(text.strip())

# ======== Основная функция ========
async def main_async():
    await init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("schedule_today", schedule_today))
    app.add_handler(CommandHandler("schedule_tomorrow", schedule_tomorrow))
    app.add_handler(CommandHandler("schedule_week", schedule_week))

    print("✅ Бот запущен и готов к работе!")
    await app.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main_async())
