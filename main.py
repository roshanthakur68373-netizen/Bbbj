import os, time, sqlite3, requests, re, logging
from keep_alive import keep_alive
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
PAYMENTS_GROUP_ID = int(os.getenv("PAYMENTS_GROUP_ID"))

ETHERSCAN_KEY = os.getenv("ETHERSCAN_KEY")
BSCSCAN_KEY = os.getenv("BSCSCAN_KEY")

MIN_USDT = 5.0
FEE_PERCENT = 2
EXPIRY_SECONDS = 15 * 60
# ========================================

logging.basicConfig(level=logging.INFO)

USDT_CONTRACTS = {
    "ERC20": "0xdac17f958d2ee523a2206206994597c13d831ec7",
    "BEP20": "0x55d398326f99059ff775485246999027b3197955"
}

# ================= DATABASE =================
db = sqlite3.connect("swap.db", check_same_thread=False)
cur = db.cursor()

cur.execute("""CREATE TABLE IF NOT EXISTS deposit (chain TEXT PRIMARY KEY, address TEXT)""")
cur.execute("""
CREATE TABLE IF NOT EXISTS orders (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER,
 usdt REAL,
 inr REAL,
 chain TEXT,
 address TEXT,
 tx_hash TEXT,
 tx_link TEXT,
 payout TEXT,
 utr TEXT,
 status TEXT,
 created INTEGER
)
""")
db.commit()

admin_waiting_utr = {}

# ================= HELPERS =================
def rate():
    return requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids":"tether","vs_currencies":"inr"},
        timeout=10
    ).json()["tether"]["inr"]

def extract_hash(link, chain):
    p = {
        "TRC20": r"transaction/([a-fA-F0-9]+)",
        "ERC20": r"tx/([a-fA-F0-9]+)",
        "BEP20": r"tx/([a-fA-F0-9]+)"
    }
    m = re.search(p[chain], link)
    return m.group(1) if m else None

def verify_trc20(tx, amt, addr):
    r = requests.get(
        f"https://apilist.tronscan.org/api/transaction-info?hash={tx}",
        timeout=10
    ).json()
    for t in r.get("trc20TransferInfo", []):
        if t["symbol"]=="USDT" and t["to_address"].lower()==addr.lower() and float(t["amount"])==amt:
            return True
    return False

def verify_evm(tx, amt, addr, chain):
    api = "https://api.etherscan.io/api" if chain=="ERC20" else "https://api.bscscan.com/api"
    key = ETHERSCAN_KEY if chain=="ERC20" else BSCSCAN_KEY
    r = requests.get(api, params={
        "module":"account","action":"tokentx","txhash":tx,"apikey":key
    }).json()
    for t in r.get("result", []):
        if t["to"].lower()==addr.lower() and float(t["value"])/(10**int(t["tokenDecimal"]))==amt:
            return True
    return False

# ================= TIMER =================
async def countdown(context):
    d = context.job.data
    left = EXPIRY_SECONDS - (int(time.time()) - d["start"])

    if left <= 0:
        await context.bot.edit_message_text(
            chat_id=d["chat"],
            message_id=d["msg"],
            text="⛔ *Order expired*\nNo transaction received in time.",
            parse_mode="Markdown"
        )
        await context.bot.send_message(
            ADMIN_ID,
            f"⛔ ORDER EXPIRED\nUser: {d['user']}\nUSDT: {d['usdt']}\nChain: {d['chain']}"
        )
        context.job.schedule_removal()
        return

    m,s = divmod(left,60)
    await context.bot.edit_message_text(
        chat_id=d["chat"],
        message_id=d["msg"],
        text=f"⏳ *Time Remaining*\n\n⏱ `{m:02d}:{s:02d}`",
        parse_mode="Markdown"
    )

# ================= UI =================
def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Swap USDT → INR", callback_data="swap")],
        [InlineKeyboardButton("📊 My Orders", callback_data="status")],
        [InlineKeyboardButton("👤 Profile", callback_data="profile")],
        [InlineKeyboardButton("🆘 Support", callback_data="support")]
    ])

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "💱 *USDT → INR Swap Service*\n\n"
        "• Minimum: 5 USDT\n"
        "• Fee: 2%\n"
        "• Manual verification\n\n"
        "Choose an option below:",
        parse_mode="Markdown",
        reply_markup=menu()
    )

# ================= CALLBACKS =================
async def cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data=="swap":
        context.user_data["step"]="amount"
        await q.message.edit_text("Enter USDT amount (min 5):")

    elif q.data in ["TRC20","ERC20","BEP20"]:
        cur.execute("SELECT address FROM deposit WHERE chain=?", (q.data,))
        addr = cur.fetchone()[0]
        context.user_data.update({
            "chain":q.data,"address":addr,"step":"tx","start":int(time.time())
        })

        await q.message.edit_text(
            f"Send *{context.user_data['usdt']} USDT* on *{q.data}*\n\n"
            f"`{addr}`\n\n"
            "Then paste the transaction link.",
            parse_mode="Markdown"
        )

        tmsg = await q.message.reply_text("⏳ Timer starting...")
        job = context.job_queue.run_repeating(
            countdown,1,data={
                "chat":q.message.chat_id,
                "msg":tmsg.message_id,
                "start":context.user_data["start"],
                "user":q.from_user.id,
                "usdt":context.user_data["usdt"],
                "chain":q.data
            }
        )
        context.user_data["timer"]=job

    elif q.data=="status":
        cur.execute("SELECT id,status FROM orders WHERE user_id=?", (q.from_user.id,))
        rows = cur.fetchall()
        await q.message.edit_text(
            "\n".join([f"#{i} → {s}" for i,s in rows]) or "No orders yet.",
            reply_markup=menu()
        )

    elif q.data=="profile":
        cur.execute("SELECT COUNT(*),IFNULL(SUM(usdt),0) FROM orders WHERE user_id=?", (q.from_user.id,))
        c,s = cur.fetchone()
        await q.message.edit_text(f"Orders: {c}\nUSDT Swapped: {s}", reply_markup=menu())

    elif q.data=="support":
        await q.message.edit_text(
            "Always send exact amount on correct network.\n"
            "Orders expire after 15 minutes.",
            reply_markup=menu()
        )

# ================= MESSAGES =================
async def msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("step")

    if step=="amount":
        usdt = float(update.message.text)
        if usdt < MIN_USDT:
            await update.message.reply_text("Minimum is 5 USDT.")
            return

        r = rate()
        net = usdt - (usdt*FEE_PERCENT/100)
        inr = round(net*r,2)
        context.user_data["usdt"]=usdt

        await update.message.reply_text(
            f"You will receive approx ₹{inr}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(c,callback_data=c)] for c in ["TRC20","ERC20","BEP20"]]
            )
        )
        context.user_data["step"]=None

    elif step=="tx":
        context.user_data["timer"].schedule_removal()
        tx = extract_hash(update.message.text, context.user_data["chain"])

        ok = verify_trc20(tx,context.user_data["usdt"],context.user_data["address"]) \
            if context.user_data["chain"]=="TRC20" else \
            verify_evm(tx,context.user_data["usdt"],context.user_data["address"],context.user_data["chain"])

        if not ok:
            await update.message.reply_text("Verification failed.")
            return

        cur.execute(
            "INSERT INTO orders VALUES (NULL,?,?,?,?,?,?,?,?,?,?)",
            (update.message.from_user.id,context.user_data["usdt"],0,
             context.user_data["chain"],context.user_data["address"],
             tx,update.message.text,"UPI",None,"pending",int(time.time()))
        )
        db.commit()

        oid = cur.lastrowid

        await context.bot.send_message(
            ADMIN_ID,
            f"🆕 New Order #{oid}\nUSDT: {context.user_data['usdt']}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Approve",callback_data=f"a_{oid}"),
                 InlineKeyboardButton("❌ Reject",callback_data=f"r_{oid}")]
            ])
        )

        await update.message.reply_text("Order submitted.", reply_markup=menu())
        context.user_data.clear()

# ================= ADMIN =================
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    act,oid = q.data.split("_")
    oid=int(oid)

    if act=="a":
        admin_waiting_utr[q.from_user.id]=oid
        await q.message.edit_text("Send UTR or type `skip`",parse_mode="Markdown")
    else:
        cur.execute("UPDATE orders SET status='rejected' WHERE id=?", (oid,))
        db.commit()
        await q.message.edit_text("Rejected.")

async def admin_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.from_user.id in admin_waiting_utr:
        oid = admin_waiting_utr.pop(update.from_user.id)
        utr = update.message.text if update.message.text.lower()!="skip" else None

        cur.execute("UPDATE orders SET status='approved',utr=? WHERE id=?", (utr,oid))
        db.commit()

        await context.bot.send_message(
            PAYMENTS_GROUP_ID,
            f"✅ Order #{oid} completed\nUTR: {utr or 'N/A'}"
        )

        await update.message.reply_text("Approved.")

# ================= RUN =================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(admin, pattern="^[ar]_"))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_msg))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg))
    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    keep_alive()
    main()
