from keep_alive import keep_alive
keep_alive()

import os, sys, json, time, re, subprocess, signal
import psutil

from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from telegram.request import HTTPXRequest
from telegram.error import TimedOut

# ================= CONFIG (ENV) =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN env not set")

UPDATES_CHANNEL = os.getenv("UPDATES_CHANNEL", "https://t.me/your_channel")

BOT_DIR = "bots"
LOG_DIR = "logs"
PID_FILE = "pids.json"
USER_FILE = "users.json"

START_TIME = time.time()
LAST_UPLOAD_SPEED = 0

os.makedirs(BOT_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# ================= STORAGE =================

def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

PROCESSES = load_json(PID_FILE)
USERS = load_json(USER_FILE)

def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except:
        return False

# cleanup dead processes
for b, p in list(PROCESSES.items()):
    if not pid_alive(p):
        PROCESSES.pop(b)
save_json(PID_FILE, PROCESSES)

# ================= UI =================

def main_menu():
    return ReplyKeyboardMarkup(
        [
            ["📤 Upload", "📂 Check Files"],
            ["📊 Status", "📢 Updates Channel"]
        ],
        resize_keyboard=True
    )

def bot_list_keyboard():
    bots = [f for f in os.listdir(BOT_DIR) if f.endswith(".py")]
    if not bots:
        return None

    rows = []
    for b in bots:
        pid = PROCESSES.get(b)
        icon = "🟢" if pid and pid_alive(pid) else "🔴"
        rows.append([
            InlineKeyboardButton(f"{icon} {b}", callback_data=f"select|{b}")
        ])
    return InlineKeyboardMarkup(rows)

def bot_actions(bot):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶ Start", callback_data=f"start|{bot}"),
            InlineKeyboardButton("⏹ Stop", callback_data=f"stop|{bot}")
        ],
        [
            InlineKeyboardButton("📄 Logs", callback_data=f"logs|{bot}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"delete|{bot}")
        ]
    ])

# ================= PROCESS =================

def start_bot(bot):
    log = open(f"{LOG_DIR}/{bot}.log", "a")
    p = subprocess.Popen(
        [sys.executable, f"{BOT_DIR}/{bot}"],
        stdout=log,
        stderr=log,
        preexec_fn=os.setsid
    )
    PROCESSES[bot] = p.pid
    save_json(PID_FILE, PROCESSES)
    return p.pid

def stop_bot(bot):
    pid = PROCESSES.get(bot)
    if pid:
        try:
            os.killpg(pid, signal.SIGTERM)
        except:
            pass
        PROCESSES.pop(bot, None)
        save_json(PID_FILE, PROCESSES)

def auto_install(bot):
    log_path = f"{LOG_DIR}/{bot}.log"
    if not os.path.exists(log_path):
        return None

    text = open(log_path).read()
    m = re.search(r"No module named ['\"]([^'\"]+)['\"]", text)
    if not m:
        return None

    module = m.group(1)
    subprocess.call([sys.executable, "-m", "pip", "install", module])
    return module

# ================= ERROR HANDLER =================

async def error_handler(update, context):
    if isinstance(context.error, TimedOut):
        print("Telegram timeout handled")
    else:
        print(context.error)

# ================= HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    USERS[uid] = True
    save_json(USER_FILE, USERS)

    context.user_data.clear()
    await update.message.reply_text(
        "🤖 Python Host Bot\nReady to host.",
        reply_markup=main_menu()
    )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global LAST_UPLOAD_SPEED
    txt = update.message.text

    if txt == "📤 Upload":
        context.user_data["upload"] = True
        await update.message.reply_text("Send your `.py` file")

    elif txt == "📂 Check Files":
        kb = bot_list_keyboard()
        if not kb:
            await update.message.reply_text("No bots uploaded.")
            return
        await update.message.reply_text("Select a bot:", reply_markup=kb)

    elif txt == "📢 Updates Channel":
        await update.message.reply_text(
            "Updates:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Open Channel", url=UPDATES_CHANNEL)]
            ])
        )

    elif txt == "📊 Status":
        active = sum(1 for p in PROCESSES.values() if pid_alive(p))
        await update.message.reply_text(
            f"👥 Users: {len(USERS)}\n"
            f"📁 Files: {len(os.listdir(BOT_DIR))}\n"
            f"🟢 Active Bots: {active}\n"
            f"⚡ Last Upload Speed: {LAST_UPLOAD_SPEED} ms"
        )

async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global LAST_UPLOAD_SPEED

    if not context.user_data.get("upload"):
        return

    doc = update.message.document
    if not doc.file_name.endswith(".py"):
        await update.message.reply_text("Only .py files allowed.")
        return

    start_t = time.time()
    msg = await update.message.reply_text("⏳ Processing...")
    file = await doc.get_file()
    await file.download_to_drive(f"{BOT_DIR}/{doc.file_name}")

    pid = start_bot(doc.file_name)
    LAST_UPLOAD_SPEED = round((time.time() - start_t) * 1000, 2)

    context.user_data.clear()
    await msg.edit_text(
        f"✅ Started\nBot: `{doc.file_name}`\nPID: `{pid}`\nSpeed: {LAST_UPLOAD_SPEED} ms",
        parse_mode="Markdown"
    )

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action, bot = q.data.split("|", 1)

    if action == "select":
        await q.message.reply_text(
            f"⚙ {bot}",
            reply_markup=bot_actions(bot)
        )

    elif action == "start":
        pid = start_bot(bot)
        await q.message.reply_text(f"▶ Started `{pid}`", parse_mode="Markdown")

    elif action == "stop":
        stop_bot(bot)
        await q.message.reply_text("⏹ Stopped")

    elif action == "logs":
        mod = auto_install(bot)
        if mod:
            pid = start_bot(bot)
            await q.message.reply_text(
                f"📦 Installed `{mod}` → Restarted `{pid}`",
                parse_mode="Markdown"
            )
            return

        log = f"{LOG_DIR}/{bot}.log"
        txt = "".join(open(log).readlines()[-25:]) if os.path.exists(log) else "No logs"
        await q.message.reply_text(txt)

    elif action == "delete":
        stop_bot(bot)
        os.remove(f"{BOT_DIR}/{bot}")
        await q.message.reply_text("🗑 Deleted")

# ================= MAIN =================

def main():
    request = HTTPXRequest(
        connect_timeout=20,
        read_timeout=20,
        write_timeout=20,
        pool_timeout=20
    )

    app = ApplicationBuilder().token(BOT_TOKEN).request(request).build()

    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(error_handler)

    print("✅ Host Bot running on Render (Flask + ENV)")
    app.run_polling()

if __name__ == "__main__":
    main()
