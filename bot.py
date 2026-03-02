import logging
import os
from datetime import datetime, time
import pytz
import calendar

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
import gspread
from google.oauth2.service_account import Credentials
import requests
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

def get_credentials():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    raw = os.environ.get("GOOGLE_CREDENTIALS")
    if raw:
        info = json.loads(raw)
        return Credentials.from_service_account_info(info, scopes=scopes)
    return Credentials.from_service_account_file("credentials.json", scopes=scopes)

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8168748545:AAFEDlPWsN9j_-9iGvhbzrFjV5T5eTRjjDc")
EXCHANGE_API_KEY = os.environ.get("EXCHANGE_API_KEY", "b6ede9248d619da184f4d560")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "1jC7H3yj-MIEJ7r97gRfrLorhHaWGt_raUmZGjUn65Go")
ALLOWED_USERS = [365300344, 508703203]

USER_NAMES = {
    365300344: "Абай",
    508703203: "Жанэля"
}
ASTANA_TZ = pytz.timezone("Asia/Almaty")

CATEGORIES = [
    "продукты", "аптека", "такси", "бензин", "еда", "кофе",
    "развлечения", "ребенок", "гости", "подарки", "одежда",
    "путешествия", "подписки", "ком услуги", "интернет",
    "связь", "косметика и уход", "штрафы"
]
CURRENCIES = ["KZT", "USD", "EUR", "RUB"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── GOOGLE SHEETS ────────────────────────────────────────────────────────────
def get_sheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = get_credentials()
    client = gspread.authorize(creds)
    sh = client.open_by_key(SPREADSHEET_ID)
    try:
        ws = sh.worksheet("Траты")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="Траты", rows=10000, cols=10)
        ws.append_row(["date", "time", "user", "amount_orig", "currency", "amount_kzt", "category", "note"])
    return ws

def get_categories_sheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = get_credentials()
    client = gspread.authorize(creds)
    sh = client.open_by_key(SPREADSHEET_ID)
    try:
        ws = sh.worksheet("Категории")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="Категории", rows=100, cols=1)
        ws.append_row(["category"])
        for cat in CATEGORIES:
            ws.append_row([cat])
    return ws

def load_categories():
    try:
        ws = get_categories_sheet()
        values = ws.col_values(1)[1:]
        return [v for v in values if v]
    except Exception:
        return CATEGORIES[:]

def save_expense(user_id, amount_orig, currency, amount_kzt, category, note):
    ws = get_sheet()
    now = datetime.now(ASTANA_TZ)
    ws.append_row([
        now.strftime("%Y-%m-%d"),
        now.strftime("%H:%M"),
        USER_NAMES.get(user_id, str(user_id)),
        amount_orig, currency, amount_kzt, category, note
    ])

def get_all_expenses():
    ws = get_sheet()
    return ws.get_all_records()

def delete_last_expense(user_id):
    ws = get_sheet()
    username = USER_NAMES.get(user_id, str(user_id))
    all_values = ws.get_all_values()
    for i in range(len(all_values) - 1, 0, -1):
        if all_values[i][2] == username:
            ws.delete_rows(i + 1)
            return all_values[i]
    return None

def update_last_expense(user_id, field_index, new_value):
    ws = get_sheet()
    username = USER_NAMES.get(user_id, str(user_id))
    all_values = ws.get_all_values()
    for i in range(len(all_values) - 1, 0, -1):
        if all_values[i][2] == username:
            ws.update_cell(i + 1, field_index + 1, new_value)
            return True
    return False

# ─── CURRENCY ─────────────────────────────────────────────────────────────────
def convert_to_kzt(amount, currency):
    if currency == "KZT":
        return amount
    try:
        url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_API_KEY}/latest/{currency}"
        data = requests.get(url, timeout=5).json()
        return round(amount * data["conversion_rates"]["KZT"])
    except Exception:
        return amount

# ─── AUTH ─────────────────────────────────────────────────────────────────────
def is_allowed(update: Update):
    return update.effective_user.id in ALLOWED_USERS

# ─── KEYBOARDS ────────────────────────────────────────────────────────────────
def main_menu_keyboard():
    return ReplyKeyboardMarkup([
        ["➕ Добавить", "⚡️ Быстро"],
        ["📊 Анализ", "📋 Категории"],
        ["✏️ Редактировать", "🗑 Удалить"],
        ["📤 Экспорт", "❓ Помощь"]
    ], resize_keyboard=True, is_persistent=True)

def categories_inline(cats):
    buttons, row = [], []
    for cat in cats:
        row.append(InlineKeyboardButton(cat, callback_data=f"cat_{cat}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("✏️ Своя категория", callback_data="cat_custom")])
    return InlineKeyboardMarkup(buttons)

def currencies_inline():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(c, callback_data=f"cur_{c}") for c in CURRENCIES],
        [InlineKeyboardButton("Другая", callback_data="cur_other")]
    ])

def period_inline():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Сегодня", callback_data="period_day"),
         InlineKeyboardButton("Неделя", callback_data="period_week")],
        [InlineKeyboardButton("Месяц", callback_data="period_month"),
         InlineKeyboardButton("Год", callback_data="period_year")],
        [InlineKeyboardButton("Свои даты", callback_data="period_custom")]
    ])

def edit_fields_inline():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Сумма", callback_data="ef_3"),
        InlineKeyboardButton("Категория", callback_data="ef_6"),
        InlineKeyboardButton("Заметка", callback_data="ef_7")
    ]])

def cat_actions_inline():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить", callback_data="ca_add"),
         InlineKeyboardButton("🗑 Удалить", callback_data="ca_delete")],
        [InlineKeyboardButton("✏️ Переименовать", callback_data="ca_rename"),
         InlineKeyboardButton("📋 Список", callback_data="ca_list")]
    ])

def cat_list_inline(cats, prefix):
    return InlineKeyboardMarkup([[InlineKeyboardButton(c, callback_data=f"{prefix}{c}")] for c in cats])

# ─── ANALYSIS ─────────────────────────────────────────────────────────────────
def analyze_expenses(records, date_from, date_to, label):
    filtered = [r for r in records if date_from <= r["date"] <= date_to]
    if not filtered:
        return f"📊 *{label}*\nТрат не найдено."
    total = sum(r["amount_kzt"] for r in filtered)
    by_cat, by_user = {}, {}
    for r in filtered:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + r["amount_kzt"]
        by_user[r["user"]] = by_user.get(r["user"], 0) + r["amount_kzt"]
    sorted_cats = sorted(by_cat.items(), key=lambda x: x[1], reverse=True)
    text = f"📊 *{label}*\n💰 Всего: *{total:,.0f} ₸*\n\n"
    text += "👤 По людям:\n" + "".join(f"  • {u}: {a:,.0f} ₸\n" for u, a in by_user.items())
    text += "\n📂 По категориям:\n"
    for cat, amt in sorted_cats:
        text += f"  • {cat}: {amt:,.0f} ₸ ({round(amt/total*100)}%)\n"
    text += "\n🏆 Топ-3:\n"
    for i, (cat, amt) in enumerate(sorted_cats[:3], 1):
        text += f"  {i}. {cat}: {amt:,.0f} ₸\n"
    return text

def get_date_range(period):
    now = datetime.now(ASTANA_TZ)
    today = now.strftime("%Y-%m-%d")
    if period == "day":
        return today, today, f"Сегодня ({today})"
    elif period == "week":
        from datetime import timedelta
        start = now - timedelta(days=now.weekday())
        return start.strftime("%Y-%m-%d"), today, "Эта неделя"
    elif period == "month":
        return now.strftime("%Y-%m-01"), today, now.strftime("%B %Y")
    elif period == "year":
        return f"{now.year}-01-01", today, f"{now.year} год"
    return None, None, None

# ─── STATE HELPERS ────────────────────────────────────────────────────────────
def set_state(context, state):
    context.user_data["state"] = state

def get_state(context):
    return context.user_data.get("state")

def clear_state(context):
    context.user_data.clear()

# ─── COMMANDS ─────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    clear_state(context)
    await update.message.reply_text("👋 Привет! Выбери действие:", reply_markup=main_menu_keyboard())

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "📖 *Команды:*\n"
        "➕ Добавить — пошаговое добавление\n"
        "⚡️ Быстро — `/quick 2500 кофе латте`\n"
        "📊 Анализ — анализ расходов\n"
        "📋 Категории — управление\n"
        "✏️ Редактировать — последняя запись\n"
        "🗑 Удалить — последняя запись\n"
        "📤 Экспорт — ссылка на таблицу",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

async def quick_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Формат: /quick 2500 кофе заметка")
        return
    try:
        amount = float(args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Первый аргумент должен быть числом.")
        return
    category = args[1]
    note = " ".join(args[2:]) if len(args) > 2 else ""
    user_id = update.effective_user.id
    amount_kzt = convert_to_kzt(amount, "KZT")
    save_expense(user_id, amount, "KZT", amount_kzt, category, note)
    note_str = f" — {note}" if note else ""
    await update.message.reply_text(
        f"✅ {category} — {amount:,.0f} ₸{note_str} ({USER_NAMES.get(user_id, '')})",
        reply_markup=main_menu_keyboard()
    )

async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    url = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"
    await update.message.reply_text(f"📊 [Открыть таблицу]({url})", parse_mode="Markdown")

async def skip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    if get_state(context) == "add_note":
        await _finish_add(update, context, "")

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    clear_state(context)
    await update.message.reply_text("❌ Отменено.", reply_markup=main_menu_keyboard())

# ─── FINISH ADD ───────────────────────────────────────────────────────────────
async def _finish_add(update, context, note):
    amount = context.user_data["amount"]
    currency = context.user_data["currency"]
    category = context.user_data["category"]
    user_id = update.effective_user.id
    amount_kzt = convert_to_kzt(amount, currency)
    save_expense(user_id, amount, currency, amount_kzt, category, note)
    conv = f" ({amount:,.0f} {currency} → {amount_kzt:,.0f} ₸)" if currency != "KZT" else f" {amount_kzt:,.0f} ₸"
    note_str = f" — {note}" if note else ""
    clear_state(context)
    await update.message.reply_text(
        f"✅ Сохранено!\n📂 {category}{conv}{note_str}\n👤 {USER_NAMES.get(user_id, '')}",
        reply_markup=main_menu_keyboard()
    )

# ─── TEXT MESSAGE HANDLER ─────────────────────────────────────────────────────
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    text = update.message.text
    state = get_state(context)

    # Menu buttons
    if text == "➕ Добавить":
        clear_state(context)
        set_state(context, "add_amount")
        await update.message.reply_text("💸 Введи сумму:")
        return
    if text == "⚡️ Быстро":
        await update.message.reply_text("Формат: /quick 2500 кофе латте")
        return
    if text == "📊 Анализ":
        clear_state(context)
        await update.message.reply_text("📊 Выбери период:", reply_markup=period_inline())
        return
    if text == "📋 Категории":
        clear_state(context)
        await update.message.reply_text("📂 Управление категориями:", reply_markup=cat_actions_inline())
        return
    if text == "✏️ Редактировать":
        clear_state(context)
        await update.message.reply_text("✏️ Что редактировать?", reply_markup=edit_fields_inline())
        return
    if text == "🗑 Удалить":
        clear_state(context)
        row = delete_last_expense(update.effective_user.id)
        msg = f"🗑 Удалена: {row[0]} | {row[6]} — {row[3]} {row[4]}" if row else "Записей не найдено."
        await update.message.reply_text(msg, reply_markup=main_menu_keyboard())
        return
    if text == "📤 Экспорт":
        await export_cmd(update, context)
        return
    if text == "❓ Помощь":
        await help_cmd(update, context)
        return

    # State-based input
    if state == "add_amount":
        try:
            context.user_data["amount"] = float(text.replace(",", "."))
            set_state(context, "add_currency")
            await update.message.reply_text("💱 Выбери валюту:", reply_markup=currencies_inline())
        except ValueError:
            await update.message.reply_text("❌ Введи число, например: 2500")
        return

    if state == "add_custom_currency":
        context.user_data["currency"] = text.strip().upper()
        set_state(context, "add_category")
        await update.message.reply_text("📂 Выбери категорию:", reply_markup=categories_inline(load_categories()))
        return

    if state == "add_category":
        context.user_data["category"] = text.strip()
        set_state(context, "add_note")
        await update.message.reply_text("📝 Заметка? (или /skip)")
        return

    if state == "add_note":
        await _finish_add(update, context, text.strip())
        return

    if state == "edit_value":
        field_index = context.user_data.get("edit_field")
        new_value = text.strip()
        if field_index == 3:
            try:
                new_value = float(new_value.replace(",", "."))
            except ValueError:
                await update.message.reply_text("❌ Введи число.")
                return
        update_last_expense(update.effective_user.id, field_index, new_value)
        clear_state(context)
        await update.message.reply_text("✅ Обновлено!", reply_markup=main_menu_keyboard())
        return

    if state == "analyze_custom_from":
        context.user_data["analyze_from"] = text.strip()
        set_state(context, "analyze_custom_to")
        await update.message.reply_text("📅 Введи конечную дату (ГГГГ-ММ-ДД):")
        return

    if state == "analyze_custom_to":
        date_from = context.user_data.get("analyze_from")
        date_to = text.strip()
        records = get_all_expenses()
        result = analyze_expenses(records, date_from, date_to, f"{date_from} – {date_to}")
        clear_state(context)
        await update.message.reply_text(result, parse_mode="Markdown", reply_markup=main_menu_keyboard())
        return

    if state == "cat_new_name":
        ws = get_categories_sheet()
        ws.append_row([text.strip()])
        clear_state(context)
        await update.message.reply_text(f"✅ Категория «{text.strip()}» добавлена.", reply_markup=main_menu_keyboard())
        return

    if state == "cat_rename_new":
        old_name = context.user_data.get("cat_selected")
        new_name = text.strip()
        ws = get_categories_sheet()
        cell = ws.find(old_name)
        if cell:
            ws.update_cell(cell.row, 1, new_name)
        clear_state(context)
        await update.message.reply_text(f"✅ «{old_name}» → «{new_name}»", reply_markup=main_menu_keyboard())
        return

# ─── CALLBACK HANDLER ─────────────────────────────────────────────────────────
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("cur_"):
        currency = data.replace("cur_", "")
        if currency == "other":
            set_state(context, "add_custom_currency")
            await query.edit_message_text("✏️ Введи код валюты (например: GBP, CNY, AED):")
        else:
            context.user_data["currency"] = currency
            set_state(context, "add_category")
            await query.edit_message_text("📂 Выбери категорию:", reply_markup=categories_inline(load_categories()))
        return

    if data.startswith("cat_"):
        category = data.replace("cat_", "")
        if category == "custom":
            set_state(context, "add_category")
            await query.edit_message_text("✏️ Введи свою категорию:")
        else:
            context.user_data["category"] = category
            set_state(context, "add_note")
            await query.edit_message_text("📝 Заметка? (или /skip)")
        return

    if data.startswith("period_"):
        period = data.replace("period_", "")
        if period == "custom":
            set_state(context, "analyze_custom_from")
            await query.edit_message_text("📅 Введи начальную дату (ГГГГ-ММ-ДД):")
            return
        date_from, date_to, label = get_date_range(period)
        records = get_all_expenses()
        result = analyze_expenses(records, date_from, date_to, label)
        await query.edit_message_text(result, parse_mode="Markdown")
        return

    if data.startswith("ef_"):
        field_index = int(data.replace("ef_", ""))
        context.user_data["edit_field"] = field_index
        set_state(context, "edit_value")
        names = {3: "новую сумму", 6: "новую категорию", 7: "новую заметку"}
        await query.edit_message_text(f"Введи {names[field_index]}:")
        return

    if data.startswith("ca_"):
        action = data.replace("ca_", "")
        if action == "list":
            cats = load_categories()
            await query.edit_message_text("📋 Категории:\n" + "\n".join(f"  • {c}" for c in cats))
        elif action == "add":
            set_state(context, "cat_new_name")
            await query.edit_message_text("✏️ Введи название новой категории:")
        elif action == "delete":
            await query.edit_message_text("Выбери категорию для удаления:",
                                          reply_markup=cat_list_inline(load_categories(), "cdel_"))
        elif action == "rename":
            await query.edit_message_text("Выбери категорию для переименования:",
                                          reply_markup=cat_list_inline(load_categories(), "cren_"))
        return

    if data.startswith("cdel_"):
        cat = data.replace("cdel_", "")
        ws = get_categories_sheet()
        cell = ws.find(cat)
        if cell:
            ws.delete_rows(cell.row)
        await query.edit_message_text(f"🗑 Категория «{cat}» удалена.")
        return

    if data.startswith("cren_"):
        cat = data.replace("cren_", "")
        context.user_data["cat_selected"] = cat
        set_state(context, "cat_rename_new")
        await query.edit_message_text(f"✏️ Новое название для «{cat}»:")
        return

# ─── SCHEDULED JOBS ───────────────────────────────────────────────────────────
async def reminder_job(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(ASTANA_TZ).strftime("%Y-%m-%d")
    records = get_all_expenses()
    if not any(r["date"] == today for r in records):
        for uid in ALLOWED_USERS:
            await context.bot.send_message(uid, "⏰ Не забудь внести расходы за сегодня!")

async def daily_summary_job(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(ASTANA_TZ)
    today = now.strftime("%Y-%m-%d")
    records = get_all_expenses()
    text = analyze_expenses(records, today, today, f"Итог дня ({today})")
    for uid in ALLOWED_USERS:
        await context.bot.send_message(uid, text, parse_mode="Markdown")

async def weekly_summary_job(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(ASTANA_TZ)
    if now.weekday() != 6:
        return
    from datetime import timedelta
    start = now - timedelta(days=6)
    records = get_all_expenses()
    text = analyze_expenses(records, start.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"), "Итог недели")
    for uid in ALLOWED_USERS:
        await context.bot.send_message(uid, text, parse_mode="Markdown")

async def monthly_summary_job(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(ASTANA_TZ)
    if now.day != calendar.monthrange(now.year, now.month)[1]:
        return
    records = get_all_expenses()
    text = analyze_expenses(records, now.strftime("%Y-%m-01"), now.strftime("%Y-%m-%d"),
                            f"Итог месяца ({now.strftime('%B %Y')})")
    for uid in ALLOWED_USERS:
        await context.bot.send_message(uid, text, parse_mode="Markdown")

async def yearly_summary_job(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(ASTANA_TZ)
    if not (now.month == 12 and now.day == 31):
        return
    records = get_all_expenses()
    text = analyze_expenses(records, f"{now.year}-01-01", f"{now.year}-12-31", f"Итог {now.year} года")
    for uid in ALLOWED_USERS:
        await context.bot.send_message(uid, text, parse_mode="Markdown")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass  # suppress logs

def run_health_server():
    server = HTTPServer(("0.0.0.0", 8000), HealthHandler)
    server.serve_forever()

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("quick", quick_add))
    app.add_handler(CommandHandler("export", export_cmd))
    app.add_handler(CommandHandler("skip", skip_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    job_queue = app.job_queue
    job_queue.run_daily(reminder_job,        time=time(18, 30, tzinfo=pytz.utc))  # 23:30 Astana
    job_queue.run_daily(daily_summary_job,   time=time(18, 59, tzinfo=pytz.utc))  # 23:59 Astana
    job_queue.run_daily(weekly_summary_job,  time=time(18, 59, tzinfo=pytz.utc))
    job_queue.run_daily(monthly_summary_job, time=time(18, 59, tzinfo=pytz.utc))
    job_queue.run_daily(yearly_summary_job,  time=time(18, 59, tzinfo=pytz.utc))

    logger.info("Bot started.")
    app.run_polling()

if __name__ == "__main__":
    main()
