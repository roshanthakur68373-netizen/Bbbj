#!/usr/bin/env python3
# ============================================================
# USDT → INR SWAP BOT (FULL PROFESSIONAL SINGLE SCRIPT)
# python-telegram-bot v20+
# ============================================================
from keep_alive import keep_alive
from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is alive"

def run():
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()
    
    import logging
import sqlite3
import time
import requests
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# ================== CONFIG ==================
BOT_TOKEN = "8207532706:AAEAgl2NVdGK1YMkd0aoDZtvLN-rEWahK7w"
ADMIN_ID = 6567632240

ETHERSCAN_KEY = "YOUR_ETHERSCAN_KEY"
BSCSCAN_KEY = "YOUR_BSCSCAN_KEY"

FEE_PERCENT = 2
EXPIRY_SECONDS = 15 * 60

USDT_CONTRACTS = {
    "ERC20": "0xdac17f958d2ee523a2206206994597c13d831ec7",
    "BEP20": "0x55d398326f99059ff775485246999027b3197955"
}
# ===========================================

logging.basicConfig(level=logging.INFO)

# ================== DATABASE ==================
db = sqlite3.connect("swap.db", check_same_thread=False)
cur = db.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS deposit (
    chain TEXT PRIMARY KEY,
    address TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    usdt REAL,
    fee REAL,
    net_usdt REAL,
    rate REAL,
    inr REAL,
    chain TEXT,
    deposit_address TEXT,
    tx_hash TEXT UNIQUE,
    payout_method TEXT,
    payout_details TEXT,
    status TEXT,
    created_at INTEGER
)
""")
db.commit()

# ================== UI ==================
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Swap USDT", callback_data="swap")],
        [InlineKeyboardButton("📊 Status", callback_data="status")],
        [InlineKeyboardButton("👤 Profile", callback_data="profile")],
        [InlineKeyboardButton("🆘 Support", callback_data="support")]
    ])

def nav_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⬅️ Back", callback_data="menu"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="menu")
        ]
    ])

# ================== HELPERS ==================
def live_rate():
    r = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": "tether", "vs_currencies": "inr"},
        timeout=10
    ).json()
    return float(r["tether"]["inr"])

def verify_trc20(tx, amount, address):
    r = requests.get(
        f"https://apilist.tronscan.org/api/transaction-info?hash={tx}",
        timeout=10
    ).json()
    for t in r.get("trc20TransferInfo", []):
        if (
            t["symbol"] == "USDT"
            and t["to_address"].lower() == address.lower()
            and float(t["amount"]) == amount
        ):
            return True
    return False

def verify_evm(tx, amount, address, chain):
    api = "https://api.etherscan.io/api" if chain == "ERC20" else "https://api.bscscan.com/api"
    key = ETHERSCAN_KEY if chain == "ERC20" else BSCSCAN_KEY

    r = requests.get(api, params={
        "module": "account",
        "action": "tokentx",
        "txhash": tx,
        "apikey": key
    }, timeout=10).json()

    for t in r.get("result", []):
        if (
            t["contractAddress"].lower() == USDT_CONTRACTS[chain].lower()
            and t["to"].lower() == address.lower()
            and float(t["value"]) / (10 ** int(t["tokenDecimal"])) == amount
        ):
            return True
    return False

# ================== START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "💱 USDT → INR Swap Bot",
        reply_markup=main_menu()
    )

# ================== CALLBACKS ==================
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "menu":
        context.user_data.clear()
        await q.message.edit_text("🏠 Main Menu", reply_markup=main_menu())
        return

    if data == "swap":
        context.user_data["step"] = "amount"
        await q.message.edit_text("Enter USDT amount:", reply_markup=nav_menu())
        return

    if data.startswith("pay_"):
        context.user_data["payout_method"] = data.replace("pay_", "")
        context.user_data["step"] = "payout_details"
        await q.message.edit_text("Enter payout details:", reply_markup=nav_menu())
        return

    if data in ["TRC20", "ERC20", "BEP20"]:
        cur.execute("SELECT address FROM deposit WHERE chain=?", (data,))
        row = cur.fetchone()
        if not row:
            await q.message.edit_text("Deposit not configured.", reply_markup=main_menu())
            return

        context.user_data["chain"] = data
        context.user_data["deposit_address"] = row[0]
        context.user_data["start_time"] = int(time.time())
        context.user_data["step"] = "tx"

        await q.message.edit_text(
            f"Send {context.user_data['usdt']} USDT via {data}\n"
            f"Address:\n{row[0]}\n\n⏳ Time limit: 15 minutes",
            reply_markup=nav_menu()
        )

# ================== MESSAGES ==================
async def messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("step")

    if step == "amount":
        try:
            usdt = float(update.message.text)
        except:
            await update.message.reply_text("Enter a valid number", reply_markup=nav_menu())
            return

        rate = live_rate()
        fee = round(usdt * FEE_PERCENT / 100, 2)
        net = usdt - fee
        inr = round(net * rate, 2)

        context.user_data.update({
            "usdt": usdt,
            "fee": fee,
            "net_usdt": net,
            "rate": rate,
            "inr": inr
        })

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("💳 UPI", callback_data="pay_upi"),
                InlineKeyboardButton("🏦 Bank", callback_data="pay_bank")
            ]
        ])

        await update.message.reply_text(
            f"Entered: {usdt} USDT\n"
            f"Fee (2%): {fee} USDT\n"
            f"Net USDT: {net}\n"
            f"Rate: ₹{rate}\n\n"
            f"💰 You Receive: ₹{inr}",
            reply_markup=kb
        )
        context.user_data["step"] = None
        return

    if step == "payout_details":
        context.user_data["payout_details"] = update.message.text
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(c, callback_data=c)] for c in ["TRC20", "ERC20", "BEP20"]
        ])
        await update.message.reply_text("Select blockchain:", reply_markup=kb)
        context.user_data["step"] = None
        return

    if step == "tx":
        if time.time() - context.user_data["start_time"] > EXPIRY_SECONDS:
            await update.message.reply_text("⏳ Order expired", reply_markup=main_menu())
            context.user_data.clear()
            return

        tx = update.message.text
        chain = context.user_data["chain"]

        verified = (
            verify_trc20(tx, context.user_data["usdt"], context.user_data["deposit_address"])
            if chain == "TRC20"
            else verify_evm(tx, context.user_data["usdt"], context.user_data["deposit_address"], chain)
        )

        if not verified:
            await update.message.reply_text("❌ Verification failed", reply_markup=nav_menu())
            return

        cur.execute("""
        INSERT INTO orders VALUES
        (NULL,?,?,?,?,?,?,?,?,?,?,?, 'pending',?)
        """, (
            update.message.from_user.id,
            context.user_data["usdt"],
            context.user_data["fee"],
            context.user_data["net_usdt"],
            context.user_data["rate"],
            context.user_data["inr"],
            chain,
            context.user_data["deposit_address"],
            tx,
            context.user_data["payout_method"],
            context.user_data["payout_details"],
            int(time.time())
        ))
        db.commit()

        order_id = cur.lastrowid

        # ADMIN NOTIFY
        await context.bot.send_message(
            ADMIN_ID,
            f"🆕 NEW SWAP ORDER #{order_id}\n\n"
            f"USDT: {context.user_data['usdt']}\n"
            f"Fee: {context.user_data['fee']} USDT\n"
            f"Net: {context.user_data['net_usdt']} USDT\n"
            f"Rate: ₹{context.user_data['rate']}\n"
            f"INR: ₹{context.user_data['inr']}\n\n"
            f"Chain: {chain}\n"
            f"TX: {tx}\n\n"
            f"Payout Method: {context.user_data['payout_method']}\n"
            f"{context.user_data['payout_details']}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve_{order_id}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"reject_{order_id}")
                ]
            ])
        )

        await update.message.reply_text(
            "✅ Order placed successfully.\n"
            "Status: Pending\n"
            "You will be notified after admin action.",
            reply_markup=main_menu()
        )
        context.user_data.clear()

# ================== ADMIN ACTION ==================
async def admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action, oid = q.data.split("_")
    oid = int(oid)

    cur.execute("SELECT user_id, inr FROM orders WHERE id=?", (oid,))
    user_id, inr = cur.fetchone()

    if action == "approve":
        cur.execute("UPDATE orders SET status='approved' WHERE id=?", (oid,))
        db.commit()

        await context.bot.send_message(
            user_id,
            f"✅ Swap Approved\n\n₹{inr} has been processed."
        )
        await q.edit_message_text("✅ Order Approved. User notified.")

    else:
        cur.execute("UPDATE orders SET status='rejected' WHERE id=?", (oid,))
        db.commit()

        await context.bot.send_message(
            user_id,
            "❌ Swap Rejected\nPlease contact support."
        )
        await q.edit_message_text("❌ Order Rejected. User notified.")

# ================== ADMIN CMD ==================
async def setaddress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    chain, addr = context.args
    cur.execute("INSERT OR REPLACE INTO deposit VALUES (?,?)", (chain, addr))
    db.commit()
    await update.message.reply_text("Address set")

# ================== RUN ==================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setaddress", setaddress))
    app.add_handler(CallbackQueryHandler(admin_action, pattern="^(approve_|reject_)"))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, messages))

    app.run_polling()

if __name__ == "__main__":
    keep_alive()
    main()