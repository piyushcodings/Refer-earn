#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Advanced Refer & Earn Telegram Bot (Pyrogram) — V2
--------------------------------------------------
User menu = NORMAL keyboard (as requested)
Admin panel = Inline keyboard

User features:
- Normal keyboard:
    💰 Balance        🎁 Daily Bonus
    👥 Invite          💵 Withdraw
                  📢 Support
- Must-join channels gate (cannot use bot until joined)
- Daily bonus (default ₹1, once per day)
- Referral system:
    * Referral bonus ONLY when the referred user joins all required channels (verified)
    * Anti-fake: if verified user later leaves a required channel, the bot blocks actions until they rejoin
- Withdraw request: user enters amount + UPI; admins approve/reject
- Support button shows a custom message (choice 3)

Admin features (inline panel via /admin):
- Admins: add/remove/list
- Channels: add/remove/list
- Maintenance ON/OFF
- Settings editor: DAILY_BONUS, REFERRAL_BONUS, MIN_WITHDRAW, CURRENCY, WELCOME_TEXT
- Payout manager (approve/reject withdrawals)
- Broadcast (all or active-only users)
- Ban/Unban users
- Add/Remove balance; Reset balance; Reset daily bonus flag
- Lookup user (full profile)
- Export users (TXT) & withdrawals (CSV)
- Owner-only DB backup (export file)

Stack: Python 3.10+, Pyrogram 2.x, SQLite3, python-dotenv
"""
import os
import sqlite3
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Tuple

from dotenv import load_dotenv
from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from pyrogram.errors import UserNotParticipant

# --------------------------- Config ---------------------------
load_dotenv()
API_ID = int(os.getenv("API_ID", 23907288))
API_HASH = os.getenv("API_HASH", "f9a47570ed19aebf8eb0f0a5ec1111e5")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8414309662:AAG3XoDlOE8DT5m6yWzr6C_iqFy-SjokzJE")
OWNER_ID = int(os.getenv("OWNER_ID", 5748100919))
DB_PATH = os.getenv("DB_PATH", "bot.db")

DEFAULTS = {
    "DAILY_BONUS": "0.5",
    "REFERRAL_BONUS": "1",
    "MIN_WITHDRAW": "50",
    "CURRENCY": "₹",
    "WELCOME_TEXT": "Welcome to Refer & Earn Bot! Earn by inviting friends.",
    "MAINTENANCE": "0",
    "ACTIVE_DAYS": "30"   # days for "active users"
}

STATE: Dict[int, Dict[str, str]] = {}

# --------------------------- DB ---------------------------

def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db(); cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            joined_at TEXT,
            referrer_id INTEGER,
            balance REAL DEFAULT 0,
            last_bonus_date TEXT,
            verified INTEGER DEFAULT 0,              -- joined all required channels at least once
            referred_bonus_paid INTEGER DEFAULT 0,   -- has referrer received bonus
            is_banned INTEGER DEFAULT 0,
            last_seen TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            upi TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)
    for k, v in DEFAULTS.items():
        cur.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    if OWNER_ID:
        cur.execute("INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (OWNER_ID,))
    con.commit(); con.close()

# --------------------------- Helpers ---------------------------

def get_setting(key: str) -> str:
    con = db(); cur = con.cursor()
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone(); con.close()
    return row[0] if row else DEFAULTS.get(key, "")

def set_setting(key: str, val: str):
    con = db(); cur = con.cursor()
    cur.execute("REPLACE INTO settings(key,value) VALUES(?,?)", (key, val))
    con.commit(); con.close()

def is_admin(uid: int) -> bool:
    if uid == OWNER_ID:
        return True
    con = db(); cur = con.cursor()
    cur.execute("SELECT 1 FROM admins WHERE user_id=?", (uid,))
    f = cur.fetchone(); con.close()
    return bool(f)

def add_user_if_absent(uid: int, ref: Optional[int]) -> Tuple[bool, Optional[int]]:
    con = db(); cur = con.cursor()
    cur.execute("SELECT referrer_id FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    if row:
        # also update last_seen
        cur.execute("UPDATE users SET last_seen=? WHERE user_id=?", (datetime.utcnow().isoformat(), uid))
        con.commit(); con.close(); return False, row[0]
    cur.execute(
        "INSERT INTO users(user_id, joined_at, referrer_id, balance, last_seen) VALUES(?,?,?,?,?)",
        (uid, datetime.utcnow().isoformat(), ref, 0.0, datetime.utcnow().isoformat())
    )
    con.commit(); con.close()
    return True, ref

def mark_seen(uid: int):
    con = db(); cur = con.cursor()
    cur.execute("UPDATE users SET last_seen=? WHERE user_id=?", (datetime.utcnow().isoformat(), uid))
    con.commit(); con.close()

def credit(uid: int, amt: float):
    con = db(); cur = con.cursor()
    cur.execute("UPDATE users SET balance = COALESCE(balance,0)+? WHERE user_id=?", (amt, uid))
    con.commit(); con.close()

def debit(uid: int, amt: float) -> bool:
    con = db(); cur = con.cursor()
    cur.execute("SELECT COALESCE(balance,0) FROM users WHERE user_id=?", (uid,))
    bal = float(cur.fetchone()[0] or 0)
    if bal < amt:
        con.close(); return False
    cur.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amt, uid))
    con.commit(); con.close(); return True

def get_balance(uid: int) -> float:
    con = db(); cur = con.cursor()
    cur.execute("SELECT COALESCE(balance,0) FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone(); con.close()
    return float(row[0] if row and row[0] is not None else 0)

def set_last_bonus_today(uid: int):
    con = db(); cur = con.cursor()
    cur.execute("UPDATE users SET last_bonus_date=? WHERE user_id=?", (date.today().isoformat(), uid))
    con.commit(); con.close()

def get_last_bonus_date(uid: int) -> Optional[str]:
    con = db(); cur = con.cursor()
    cur.execute("SELECT last_bonus_date FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone(); con.close()
    return row[0] if row and row[0] else None

def list_channels() -> List[str]:
    con = db(); cur = con.cursor()
    cur.execute("SELECT username FROM channels ORDER BY id ASC")
    names = [r[0] for r in cur.fetchall()]
    con.close(); return names

def add_channel(username: str) -> bool:
    username = username.strip()
    if username.startswith("https://t.me/"):
        username = "@" + username.split("https://t.me/")[-1]
    if not username.startswith("@"):
        username = "@" + username
    con = db(); cur = con.cursor()
    try:
        cur.execute("INSERT INTO channels(username) VALUES(?)", (username,))
        con.commit(); ok = True
    except sqlite3.IntegrityError:
        ok = False
    finally:
        con.close()
    return ok

def remove_channel(username: str) -> bool:
    con = db(); cur = con.cursor()
    cur.execute("DELETE FROM channels WHERE username=?", (username,))
    ok = cur.rowcount > 0
    con.commit(); con.close(); return ok

def add_admin(uid: int) -> bool:
    con = db(); cur = con.cursor()
    try:
        cur.execute("INSERT INTO admins(user_id) VALUES(?)", (uid,))
        con.commit(); ok = True
    except sqlite3.IntegrityError:
        ok = False
    finally:
        con.close()
    return ok

def remove_admin(uid: int) -> bool:
    con = db(); cur = con.cursor()
    cur.execute("DELETE FROM admins WHERE user_id=?", (uid,))
    ok = cur.rowcount > 0
    con.commit(); con.close(); return ok

def set_ban(uid: int, ban: bool):
    con = db(); cur = con.cursor()
    cur.execute("UPDATE users SET is_banned=? WHERE user_id=?", (1 if ban else 0, uid))
    con.commit(); con.close()

def is_banned(uid: int) -> bool:
    con = db(); cur = con.cursor()
    cur.execute("SELECT is_banned FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone(); con.close()
    return bool(row and row[0] == 1)

def get_user(uid: int) -> Optional[sqlite3.Row]:
    con = db(); cur = con.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone(); con.close()
    return row

def set_verified(uid: int):
    con = db(); cur = con.cursor()
    cur.execute("UPDATE users SET verified=1 WHERE user_id=?", (uid,))
    con.commit(); con.close()

def set_ref_bonus_paid(uid: int):
    con = db(); cur = con.cursor()
    cur.execute("UPDATE users SET referred_bonus_paid=1 WHERE user_id=?", (uid,))
    con.commit(); con.close()

# --------------------------- Bot ---------------------------
app = Client(
    name="refer_earn_bot_v2",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=enums.ParseMode.HTML,
)

# Reply keyboard for users
def user_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("💰 Balance"), KeyboardButton("🎁 Daily Bonus")],
            [KeyboardButton("👥 Invite"), KeyboardButton("💵 Withdraw")],
            [KeyboardButton("📢 Support")]
        ],
        resize_keyboard=True
    )

def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👑 Admins", callback_data="A:ADMINS"),
         InlineKeyboardButton("#️⃣ Channels", callback_data="A:CHANS")],
        [InlineKeyboardButton("🧰 Settings", callback_data="A:SET"),
         InlineKeyboardButton("🛠 Maintenance", callback_data="A:MAINT")],
        [InlineKeyboardButton("💸 Payouts", callback_data="A:PAYOUTS"),
         InlineKeyboardButton("📣 Broadcast", callback_data="A:BC")],
        [InlineKeyboardButton("🚫 Ban/Unban", callback_data="A:BANSET"),
         InlineKeyboardButton("➕➖ Balance", callback_data="A:BALSET")],
        [InlineKeyboardButton("🔎 Lookup User", callback_data="A:LOOKUP"),
         InlineKeyboardButton("📤 Export", callback_data="A:EXPORT")],
        [InlineKeyboardButton("🧰 Owner Tools", callback_data="A:OWNER")]
    ])

async def ensure_joined(user_id: int) -> List[str]:
    missing: List[str] = []
    for ch in list_channels():
        try:
            await app.get_chat_member(ch, user_id)
        except UserNotParticipant:
            missing.append(ch)
        except Exception:
            pass
    return missing

async def send_join_prompt(chat_id: int):
    chans = list_channels()
    if not chans:
        return await app.send_message(chat_id, "No required channels set by admin.")
    rows = [[InlineKeyboardButton(ch, url=f"https://t.me/{ch.lstrip('@')}")] for ch in chans]
    rows.append([InlineKeyboardButton("✅ I've joined", callback_data="U:JOINED")])
    await app.send_message(chat_id, "Please join all channels to continue:", reply_markup=InlineKeyboardMarkup(rows))

async def maybe_verify_and_credit(uid: int):
    """If user now joined all channels and was not verified: set verified and pay referrer if needed."""
    user = get_user(uid)
    if not user:
        return
    if user["verified"] == 1:
        return
    # verify
    need = await ensure_joined(uid)
    if not need:
        set_verified(uid)
        # referral bonus only once, when verifying for the first time
        if user["referrer_id"] and user["referred_bonus_paid"] == 0:
            try:
                amt = float(get_setting("REFERRAL_BONUS"))
                credit(user["referrer_id"], amt)
                set_ref_bonus_paid(uid)
                try:
                    await app.send_message(user["referrer_id"], f"🎉 Your referral verified! +{get_setting('CURRENCY')}{amt:.2f}")
                except Exception:
                    pass
            except Exception:
                pass

# ----------------------- User Handlers -----------------------


@app.on_message(filters.command("start"))
async def start_cmd(client: Client, m: Message):
    args = m.text.split(maxsplit=1)
    referrer_id = None
    if len(args) == 2 and args[1].isdigit():
        rid = int(args[1])
        if rid != m.from_user.id:
            referrer_id = rid

    is_new, saved_ref = add_user_if_absent(m.from_user.id, referrer_id)
    mark_seen(m.from_user.id)

    if get_setting("MAINTENANCE") == "1" and not is_admin(m.from_user.id):
        return await m.reply_text("🚧 Bot is under maintenance. Please try again later.")

    if is_banned(m.from_user.id):
        return await m.reply_text("🚫 You are banned from using this bot.")

    # On /start show keyboard and gate if needed
    need = await ensure_joined(m.from_user.id)
    welcome = get_setting("WELCOME_TEXT")
    if need:
        await m.reply_text(f"{welcome}\n\nYou must join required channels first.", reply_markup=user_keyboard())
        return await send_join_prompt(m.chat.id)

    # If first time verification happens here, pay referrer
    await maybe_verify_and_credit(m.from_user.id)

    await m.reply_text(f"{welcome}\n\nUse the menu below.", reply_markup=user_keyboard())

@app.on_callback_query(filters.regex(r"^U:JOINED$"))
async def joined_confirm(client: Client, cq: CallbackQuery):
    uid = cq.from_user.id
    if is_banned(uid):
        return await cq.answer("Banned.", show_alert=True)
    need = await ensure_joined(uid)
    if need:
        return await cq.answer("Still missing some channels.", show_alert=True)
    await maybe_verify_and_credit(uid)
    await cq.answer("All set!", show_alert=True)
    await cq.message.reply_text("✅ Thanks for joining. You can use the menu now.", reply_markup=user_keyboard())

# Unified user text router (NORMAL keyboard labels)
USER_BAL = "💰 Balance"
USER_BONUS = "🎁 Daily Bonus"
USER_INVITE = "👥 Invite"
USER_WITHDRAW = "💵 Withdraw"
USER_SUPPORT = "📢 Support"

@app.on_message(filters.text & ~filters.command(["start", "admin"]))
async def user_text_router(client: Client, m: Message):
    uid = m.from_user.id
    mark_seen(uid)

    # maintenance & ban
    if get_setting("MAINTENANCE") == "1" and not is_admin(uid):
        return
    if is_banned(uid):
        return await m.reply_text("🚫 You are banned from using this bot.")

    # channel gate on each action
    need = await ensure_joined(uid)
    if need:
        await m.reply_text("Please join required channels first.", reply_markup=user_keyboard())
        return await send_join_prompt(m.chat.id)

    # verification & referral payout if first time
    await maybe_verify_and_credit(uid)

    text = m.text.strip()

    if text == USER_BAL:
        bal = get_balance(uid)
        return await m.reply_text(f"🧾 <b>Your Balance:</b> {get_setting('CURRENCY')}{bal:.2f}", reply_markup=user_keyboard())

    if text == USER_BONUS:
        last = get_last_bonus_date(uid)
        today = date.today().isoformat()
        if last == today:
            return await m.reply_text("You already claimed today's bonus.", reply_markup=user_keyboard())
        amt = float(get_setting("DAILY_BONUS"))
        credit(uid, amt)
        set_last_bonus_today(uid)
        bal = get_balance(uid)
        return await m.reply_text(
            f"🎁 Daily bonus credited: {get_setting('CURRENCY')}{amt:.2f}\nCurrent balance: {get_setting('CURRENCY')}{bal:.2f}",
            reply_markup=user_keyboard()
        )

    if text == USER_INVITE:
        bot = await app.get_me()
        link = f"https://t.me/{bot.username}?start={uid}"
        return await m.reply_text(
            "👥 <b>Invite & Earn</b>\n"
            f"Share your link: <code>{link}</code>\n"
            f"Referral bonus (on verification): {get_setting('CURRENCY')}{float(get_setting('REFERRAL_BONUS')):.2f}",
            reply_markup=user_keyboard()
        )

    if text == USER_WITHDRAW:
        STATE[uid] = {"step": "wd_amount"}
        return await m.reply_text(
            f"💳 <b>Withdrawal</b>\nMinimum: {get_setting('CURRENCY')}{float(get_setting('MIN_WITHDRAW')):.2f}\nEnter the amount you want to withdraw:",
            reply_markup=user_keyboard()
        )

    if text == USER_SUPPORT:
        return await m.reply_text("📢 Support: Please wait, support will contact you.", reply_markup=user_keyboard())

    # Withdrawal steps
    st = STATE.get(uid)
    if st and st.get("step") == "wd_amount":
        try:
            amt = float(text)
        except ValueError:
            return await m.reply_text("Please enter a valid number amount.", reply_markup=user_keyboard())
        if amt < float(get_setting("MIN_WITHDRAW")):
            return await m.reply_text(f"Minimum withdrawal is {get_setting('CURRENCY')}{float(get_setting('MIN_WITHDRAW')):.2f}.", reply_markup=user_keyboard())
        STATE[uid] = {"step": "wd_upi", "amount": str(amt)}
        return await m.reply_text("Enter your UPI ID (e.g., username@bank):", reply_markup=user_keyboard())

    if st and st.get("step") == "wd_upi":
        upi = text
        try:
            amt = float(st["amount"])
        except Exception:
            amt = 0.0
        con = db(); cur = con.cursor()
        cur.execute(
            "INSERT INTO withdrawals(user_id, amount, upi, status, created_at) VALUES(?,?,?,?,?)",
            (uid, amt, upi, "pending", datetime.utcnow().isoformat())
        )
        con.commit(); con.close()
        STATE.pop(uid, None)
        await notify_admins(f"🆕 Withdrawal Request\nUser: <a href='tg://user?id={uid}'>{uid}</a>\nAmount: {get_setting('CURRENCY')}{amt:.2f}\nUPI: <code>{upi}</code>")
        return await m.reply_text("✅ Request submitted. Admins will review soon.", reply_markup=user_keyboard())

# ----------------------- Admin Panel -----------------------

def admin_home():
    return ("<b>Admin Panel</b>\nUse the buttons below."), admin_menu()
text, markup = admin_home()
@app.on_message(filters.command(["admin"]) & filters.private)
async def admin_cmd(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("❌ Not authorized.")

    await message.reply_text(
        "🛠 **Admin Panel**",
        reply_markup=admin_menu(),
        parse_mode=enums.ParseMode.MARKDOWN
    )
@app.on_callback_query(filters.regex(r"^A:"))
async def admin_callbacks(client: Client, cq: CallbackQuery):
    uid = cq.from_user.id
    if not is_admin(uid):
        return await cq.answer("Not authorized.", show_alert=True)

    code = cq.data.split(":", 1)[1]

    # ----- Admins -----
    if code == "ADMINS":
        STATE[uid] = {"step": "admin_menu"}
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Admin", callback_data="A:ADM_ADD")],
            [InlineKeyboardButton("➖ Remove Admin", callback_data="A:ADM_REM")],
            [InlineKeyboardButton("⬅️ Back", callback_data="A:BACK")]
        ])
        return await cq.message.edit_text("👑 <b>Admins</b>", reply_markup=kb)

    if code == "ADM_ADD":
        STATE[uid] = {"step": "add_admin"}
        return await cq.message.edit_text("Send numeric Telegram user ID to add as admin.\n\nOr press Back.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="A:ADMINS")]]))

    if code == "ADM_REM":
        STATE[uid] = {"step": "rem_admin"}
        return await cq.message.edit_text("Send numeric Telegram user ID to remove from admins.\n\nOr press Back.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="A:ADMINS")]]))

    # ----- Channels -----
    if code == "CHANS":
        chans = list_channels()
        rows = [[InlineKeyboardButton(ch, callback_data=f"A:CHAN_DEL|{ch}") ] for ch in chans] if chans else []
        rows += [
            [InlineKeyboardButton("➕ Add Channel", callback_data="A:CHAN_ADD")],
            [InlineKeyboardButton("⬅️ Back", callback_data="A:BACK")]
        ]
        return await cq.message.edit_text("#️⃣ <b>Required Channels</b>", reply_markup=InlineKeyboardMarkup(rows))

    if code.startswith("CHAN_DEL|"):
        ch = code.split("|",1)[1]
        ok = remove_channel(ch)
        await cq.answer("Removed." if ok else "Not found.", show_alert=True)
        return await admin_callbacks(client, CallbackQuery(id=cq.id, from_user=cq.from_user, chat_instance=cq.chat_instance, data="A:CHANS", message=cq.message))

    if code == "CHAN_ADD":
        STATE[uid] = {"step": "add_channel"}
        return await cq.message.edit_text("Send channel @username or https://t.me/ link to require.\n\nOr press Back.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="A:CHANS")]]))

    # ----- Settings -----
    if code == "SET":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("DAILY_BONUS", callback_data="A:SETK|DAILY_BONUS")],
            [InlineKeyboardButton("REFERRAL_BONUS", callback_data="A:SETK|REFERRAL_BONUS")],
            [InlineKeyboardButton("MIN_WITHDRAW", callback_data="A:SETK|MIN_WITHDRAW")],
            [InlineKeyboardButton("CURRENCY", callback_data="A:SETK|CURRENCY")],
            [InlineKeyboardButton("WELCOME_TEXT", callback_data="A:SETK|WELCOME_TEXT")],
            [InlineKeyboardButton("ACTIVE_DAYS", callback_data="A:SETK|ACTIVE_DAYS")],
            [InlineKeyboardButton("⬅️ Back", callback_data="A:BACK")]
        ])
        current = (f"<b>Settings</b>\n"
                   f"DAILY_BONUS: {get_setting('DAILY_BONUS')}\n"
                   f"REFERRAL_BONUS: {get_setting('REFERRAL_BONUS')}\n"
                   f"MIN_WITHDRAW: {get_setting('MIN_WITHDRAW')}\n"
                   f"CURRENCY: {get_setting('CURRENCY')}\n"
                   f"ACTIVE_DAYS: {get_setting('ACTIVE_DAYS')}\n"
                   f"WELCOME_TEXT: {get_setting('WELCOME_TEXT')[:80]}...")
        return await cq.message.edit_text(current, reply_markup=kb)

    if code.startswith("SETK|"):
        key = code.split("|",1)[1]
        STATE[uid] = {"step": "set_value", "key": key}
        return await cq.message.edit_text(f"Send new value for <b>{key}</b>.\n\nOr press Back.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="A:SET")]]))

    # ----- Maintenance -----
    if code == "MAINT":
        current = get_setting("MAINTENANCE")
        new = "0" if current == "1" else "1"
        set_setting("MAINTENANCE", new)
        return await cq.message.edit_text(f"🛠 Maintenance is now {'ON' if new=='1' else 'OFF'}.", reply_markup=admin_menu())

    # ----- Broadcast -----
    if code == "BC":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Send to ALL", callback_data="A:BCALL")],
            [InlineKeyboardButton("Send to ACTIVE", callback_data="A:BCACT")],
            [InlineKeyboardButton("⬅️ Back", callback_data="A:BACK")]
        ])
        return await cq.message.edit_text("📣 Broadcast mode?", reply_markup=kb)

    if code in ("BCALL", "BCACT"):
        STATE[uid] = {"step": "broadcast", "mode": code}
        return await cq.message.edit_text("Send the broadcast message text.\n\nOr press Back.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="A:BC")]]))

    # ----- Payouts -----
    if code == "PAYOUTS":
        con = db(); cur = con.cursor()
        cur.execute("SELECT id,user_id,amount,upi FROM withdrawals WHERE status='pending' ORDER BY id DESC LIMIT 10")
        rows = cur.fetchall(); con.close()
        if not rows:
            return await cq.message.edit_text("No pending withdrawals.", reply_markup=admin_menu())
        buttons = []
        for r in rows:
            buttons.append([InlineKeyboardButton(f"#{r['id']} {get_setting('CURRENCY')}{r['amount']} | {r['upi']}", callback_data=f"A:WD_VIEW|{r['id']}")])
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="A:BACK")])
        return await cq.message.edit_text("💸 <b>Pending Withdrawals</b>", reply_markup=InlineKeyboardMarkup(buttons))

    if code.startswith("WD_VIEW|"):
        wid = int(code.split("|",1)[1])
        con = db(); cur = con.cursor()
        cur.execute("SELECT id,user_id,amount,upi,status FROM withdrawals WHERE id=?", (wid,))
        r = cur.fetchone(); con.close()
        if not r:
            return await cq.answer("Not found.", show_alert=True)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Approve", callback_data=f"A:WD_OK|{wid}")],
            [InlineKeyboardButton("❌ Reject", callback_data=f"A:WD_REJ|{wid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="A:PAYOUTS")]
        ])
        text = (f"ID: #{r['id']}\nUser: <a href='tg://user?id={r['user_id']}'>{r['user_id']}</a>\n"
                f"Amount: {get_setting('CURRENCY')}{r['amount']:.2f}\nUPI: <code>{r['upi']}</code>\nStatus: {r['status']}")
        return await cq.message.edit_text(text, reply_markup=kb)

    if code.startswith("WD_OK|"):
        wid = int(code.split("|",1)[1])
        await finalize_withdrawal(wid, approve=True)
        await cq.answer("Approved.")
        return await admin_callbacks(client, CallbackQuery(id=cq.id, from_user=cq.from_user, chat_instance=cq.chat_instance, data="A:PAYOUTS", message=cq.message))

    if code.startswith("WD_REJ|"):
        wid = int(code.split("|",1)[1])
        await finalize_withdrawal(wid, approve=False)
        await cq.answer("Rejected.")
        return await admin_callbacks(client, CallbackQuery(id=cq.id, from_user=cq.from_user, chat_instance=cq.chat_instance, data="A:PAYOUTS", message=cq.message))

    # ----- Ban/Unban -----
    if code == "BANSET":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚫 Ban User", callback_data="A:BAN")],
            [InlineKeyboardButton("✅ Unban User", callback_data="A:UNBAN")],
            [InlineKeyboardButton("⬅️ Back", callback_data="A:BACK")]
        ])
        return await cq.message.edit_text("Ban/Unban users.", reply_markup=kb)

    if code in ("BAN", "UNBAN"):
        STATE[uid] = {"step": "ban" if code=="BAN" else "unban"}
        return await cq.message.edit_text("Send user ID.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="A:BANSET")]]))

    # ----- Balance ops -----
    if code == "BALSET":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Balance", callback_data="A:BALADD")],
            [InlineKeyboardButton("➖ Remove Balance", callback_data="A:BALREM")],
            [InlineKeyboardButton("🧹 Reset Balance", callback_data="A:BALRST")],
            [InlineKeyboardButton("🎁 Reset Bonus Flag", callback_data="A:BONUSRST")],
            [InlineKeyboardButton("⬅️ Back", callback_data="A:BACK")]
        ])
        return await cq.message.edit_text("Balance operations.", reply_markup=kb)

    if code in ("BALADD", "BALREM", "BALRST", "BONUSRST"):
        STATE[uid] = {"step": code.lower()}
        prompt = {
            "BALADD": "Send: user_id amount",
            "BALREM": "Send: user_id amount",
            "BALRST": "Send: user_id",
            "BONUSRST": "Send: user_id (clear daily bonus claimed for today)"
        }[code]
        return await cq.message.edit_text(prompt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="A:BALSET")]]))

    # ----- Lookup -----
    if code == "LOOKUP":
        STATE[uid] = {"step": "lookup"}
        return await cq.message.edit_text("Send user ID to lookup.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="A:BACK")]]))

    # ----- Export -----
    if code == "EXPORT":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📄 Users (TXT)", callback_data="A:EX_USERS")],
            [InlineKeyboardButton("📊 Withdrawals (CSV)", callback_data="A:EX_WD")],
            [InlineKeyboardButton("⬅️ Back", callback_data="A:BACK")]
        ])
        return await cq.message.edit_text("Choose export type.", reply_markup=kb)

    if code == "EX_USERS":
        path = await export_users()
        return await cq.message.reply_document(path, caption="Users export")

    if code == "EX_WD":
        path = await export_withdrawals()
        return await cq.message.reply_document(path, caption="Withdrawals export")

    # ----- Owner tools -----
    if code == "OWNER":
        if uid != OWNER_ID:
            return await cq.answer("Owner only.", show_alert=True)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗂 DB Backup", callback_data="A:BK_DB")],
            [InlineKeyboardButton("⬅️ Back", callback_data="A:BACK")]
        ])
        return await cq.message.edit_text("Owner tools.", reply_markup=kb)

    if code == "BK_DB":
        if uid != OWNER_ID:
            return await cq.answer("Owner only.", show_alert=True)
        return await cq.message.reply_document(DB_PATH, caption="DB backup")

    if code == "BACK":
        return await cq.message.edit_text(
    text,
    reply_markup=markup,
    parse_mode="html"
        )

# Admin text flows
@app.on_message(filters.text & filters.user(lambda _, __, m: is_admin(m.from_user.id)))
async def admin_text_router(client: Client, m: Message):
    uid = m.from_user.id
    st = STATE.get(uid)
    if not st:
        return

    # add/remove admin
    if st.get("step") == "add_admin":
        try:
            new_uid = int(m.text.strip())
        except ValueError:
            return await m.reply_text("Send numeric user ID.")
        ok = add_admin(new_uid)
        return await m.reply_text("✅ Added." if ok else "Already admin or invalid.")

    if st.get("step") == "rem_admin":
        try:
            rem_uid = int(m.text.strip())
        except ValueError:
            return await m.reply_text("Send numeric user ID.")
        ok = remove_admin(rem_uid)
        return await m.reply_text("✅ Removed." if ok else "Not an admin.")

    # channels
    if st.get("step") == "add_channel":
        ok = add_channel(m.text.strip())
        return await m.reply_text("✅ Channel added." if ok else "Could not add (maybe duplicate).")

    # settings
    if st.get("step") == "set_value":
        key = st.get("key", "")
        set_setting(key, m.text)
        return await m.reply_text(f"✅ {key} updated.")

    # broadcast
    if st.get("step") == "broadcast":
        text = m.text
        mode = st.get("mode", "BCALL")
        await broadcast(text, active_only=(mode=="BCACT"))
        return await m.reply_text("✅ Broadcast queued.")

    # ban/unban
    if st.get("step") == "ban":
        try:
            target = int(m.text.strip())
        except ValueError:
            return await m.reply_text("Send numeric user ID.")
        set_ban(target, True)
        return await m.reply_text("🚫 User banned.")
    if st.get("step") == "unban":
        try:
            target = int(m.text.strip())
        except ValueError:
            return await m.reply_text("Send numeric user ID.")
        set_ban(target, False)
        return await m.reply_text("✅ User unbanned.")

    # balance ops
    if st.get("step") == "baladd":
        try:
            tid, amt = m.text.strip().split()
            tid = int(tid); amt = float(amt)
        except Exception:
            return await m.reply_text("Format: user_id amount")
        credit(tid, amt)
        return await m.reply_text("✅ Balance added.")
    if st.get("step") == "balrem":
        try:
            tid, amt = m.text.strip().split()
            tid = int(tid); amt = float(amt)
        except Exception:
            return await m.reply_text("Format: user_id amount")
        ok = debit(tid, amt)
        return await m.reply_text("✅ Balance removed." if ok else "Insufficient balance.")
    if st.get("step") == "balrst":
        try:
            tid = int(m.text.strip())
        except Exception:
            return await m.reply_text("Send user_id")
        con = db(); cur = con.cursor()
        cur.execute("UPDATE users SET balance=0 WHERE user_id=?", (tid,))
        con.commit(); con.close()
        return await m.reply_text("🧹 Balance reset.")
    if st.get("step") == "bonusrst":
        try:
            tid = int(m.text.strip())
        except Exception:
            return await m.reply_text("Send user_id")
        # Clear last_bonus_date so they can claim again today (admin override)
        con = db(); cur = con.cursor()
        cur.execute("UPDATE users SET last_bonus_date=NULL WHERE user_id=?", (tid,))
        con.commit(); con.close()
        return await m.reply_text("🎁 Daily bonus reset for user.")

    # lookup
    if st.get("step") == "lookup":
        try:
            tid = int(m.text.strip())
        except Exception:
            return await m.reply_text("Send user_id")
        u = get_user(tid)
        if not u:
            return await m.reply_text("Not found.")
        text = (f"User: {u['user_id']}\n"
                f"Joined: {u['joined_at']}\n"
                f"Referrer: {u['referrer_id']}\n"
                f"Balance: {get_setting('CURRENCY')}{float(u['balance']):.2f}\n"
                f"Verified: {bool(u['verified'])}\n"
                f"Ref bonus paid: {bool(u['referred_bonus_paid'])}\n"
                f"Banned: {bool(u['is_banned'])}\n"
                f"Last seen: {u['last_seen']}")
        return await m.reply_text(text)

# ----------------------- Admin Helpers -----------------------

async def notify_admins(text: str):
    con = db(); cur = con.cursor()
    cur.execute("SELECT user_id FROM admins")
    admins = [r[0] for r in cur.fetchall()]
    con.close()
    for a in admins:
        try:
            await app.send_message(a, text)
        except Exception:
            pass

async def broadcast(text: str, active_only: bool=False):
    days = int(get_setting("ACTIVE_DAYS") or "30")
    limit_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
    con = db(); cur = con.cursor()
    if active_only:
        cur.execute("SELECT user_id FROM users WHERE last_seen >= ?", (limit_date,))
    else:
        cur.execute("SELECT user_id FROM users")
    users = [r[0] for r in cur.fetchall()]
    con.close()
    for uid in users:
        try:
            await app.send_message(uid, text)
        except Exception:
            pass

async def finalize_withdrawal(wid: int, approve: bool):
    con = db(); cur = con.cursor()
    cur.execute("SELECT id,user_id,amount,status FROM withdrawals WHERE id=?", (wid,))
    r = cur.fetchone()
    if not r or r["status"] != "pending":
        con.close(); return
    if approve:
        # debit if balance sufficient
        cur.execute("SELECT COALESCE(balance,0) FROM users WHERE user_id=?", (r["user_id"],))
        bal = float(cur.fetchone()[0] or 0)
        if bal >= float(r["amount"]):
            cur.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (float(r["amount"]), r["user_id"]))
            cur.execute("UPDATE withdrawals SET status='approved' WHERE id=?", (wid,))
            con.commit(); con.close()
            try:
                await app.send_message(r["user_id"], f"✅ Withdrawal approved for {get_setting('CURRENCY')}{float(r['amount']):.2f}. Payment processing.")
            except Exception:
                pass
            return
        else:
            approve = False
    # reject
    cur.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (wid,))
    con.commit(); con.close()
    try:
        await app.send_message(r["user_id"], "❌ Withdrawal rejected (insufficient balance or other issue).")
    except Exception:
        pass

async def export_users() -> str:
    path = "users.txt"
    con = db(); cur = con.cursor()
    cur.execute("SELECT user_id FROM users ORDER BY user_id ASC")
    ids = [str(r[0]) for r in cur.fetchall()]
    con.close()
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(ids))
    return path

async def export_withdrawals() -> str:
    path = "withdrawals.csv"
    con = db(); cur = con.cursor()
    cur.execute("SELECT id,user_id,amount,upi,status,created_at FROM withdrawals ORDER BY id ASC")
    rows = cur.fetchall()
    con.close()
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id","user_id","amount","upi","status","created_at"])
        for r in rows:
            writer.writerow([r["id"], r["user_id"], r["amount"], r["upi"], r["status"], r["created_at"]])
    return path

# --------------------------- Boot ---------------------------

if __name__ == "__main__":
    if not (API_ID and API_HASH and BOT_TOKEN and OWNER_ID):
        raise SystemExit("Please set API_ID, API_HASH, BOT_TOKEN, OWNER_ID in environment or .env")
    init_db()
    print("Bot starting...")
    app.run()
