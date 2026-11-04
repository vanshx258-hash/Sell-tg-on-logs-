import os
import asyncio
import sqlite3
import threading
import logging
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ================== CONFIG ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
OWNER_ID = int(os.environ.get("OWNER_ID", "8612102965"))
API_ID = int(os.environ.get("API_ID", "38746329"))
API_HASH = os.environ.get("API_HASH", "dc6d3dd0de462ed07952a64c3253c437")
PORT = int(os.environ.get("PORT", 10000))

(ASK_PHONE, ASK_OTP, ASK_2FA, ASK_TEXT, ASK_PHOTO, ASK_GROUPS) = range(6)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== RENDER PORT SERVER ==================
flask_app = Flask(__name__)
@flask_app.route('/')
def health(): return "Bot is running smoothly!", 200
def run_flask(): flask_app.run(host="0.0.0.0", port=PORT, use_reloader=False)

# ================== DATABASE ==================
def init_db():
    conn = sqlite3.connect("ads_master.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS sent_msgs (msg_id INTEGER PRIMARY KEY, group_id TEXT)""")
    conn.commit(); conn.close()
init_db()

def get_setting(key, default=None):
    conn = sqlite3.connect("ads_master.db")
    c = conn.cursor(); c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone(); conn.close()
    return row[0] if row else default

def set_setting(key, value):
    conn = sqlite3.connect("ads_master.db")
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, str(value)))
    conn.commit(); conn.close()

def del_setting(key):
    conn = sqlite3.connect("ads_master.db")
    c = conn.cursor()
    c.execute("DELETE FROM settings WHERE key=?", (key,))
    conn.commit(); conn.close()

def save_sent_msg(msg_id, group_id):
    conn = sqlite3.connect("ads_master.db")
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO sent_msgs (msg_id, group_id) VALUES (?,?)", (msg_id, str(group_id)))
    conn.commit(); conn.close()

def is_our_msg(msg_id):
    conn = sqlite3.connect("ads_master.db")
    c = conn.cursor(); c.execute("SELECT msg_id FROM sent_msgs WHERE msg_id=?", (msg_id,))
    row = c.fetchone(); conn.close()
    return bool(row)

# ================== USERBOT & REPLY LOGIC ==================
userbot = None
is_ads_running = False
ads_task = None

async def send_to_single_group(group, text, photo_id):
    try:
        sent_message = None
        # Agar photo_id (Telegram File ID) hai
        if photo_id:
            sent_message = await userbot.send_file(group, photo_id, caption=text)
        elif text:
            sent_message = await userbot.send_message(group, text)
        
        if sent_message:
            save_sent_msg(sent_message.id, group)
            logger.info(f"[SUCCESS] Sent ad to {group}")
            
    except Exception as e:
        logger.error(f"[FAILED] Could not send to {group}: {e}")

async def run_ads_loop():
    global is_ads_running, userbot
    while is_ads_running:
        text = get_setting("ad_text", "")
        photo_id = get_setting("ad_photo", "") # Ab yeh File ID hai, path nahi
        groups_str = get_setting("ad_groups", "")
        interval = int(get_setting("ad_interval", "8"))
        
        if not groups_str or not userbot:
            await asyncio.sleep(2); continue
            
        groups = [g.strip() for g in groups_str.split(',') if g.strip()]
        
        # Parallel sending to all groups
        tasks = [send_to_single_group(g, text, photo_id) for g in groups]
        await asyncio.gather(*tasks)
        
        await asyncio.sleep(interval)

async def start_userbot(session_str):
    global userbot
    if userbot:
        try: await userbot.disconnect()
        except: pass

    userbot = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    await userbot.connect()
    logger.info("Userbot connected!")
    
    @userbot.on(events.NewMessage(incoming=True))
    async def alert_handler(event):
        if event.is_private: return
        is_reply = False
        if event.message.reply_to_msg_id and is_our_msg(event.message.reply_to_msg_id): is_reply = True
        
        if is_reply or event.message.mentioned:
            sender = await event.get_sender()
            chat = await event.get_chat()
            try: await event.reply("DM me to add to contact.")
            except: pass
            
            msg = (
                f"🔔 <b>NEW ALERT</b>\n\n"
                f"👤 <b>User:</b> {sender.first_name} (@{sender.username or 'None'})\n"
                f"🆔 <b>ID:</b> <code>{sender.id}</code>\n"
                f"🔗 <b>Profile:</b> <a href='tg://user?id={sender.id}'>Click Here</a>\n\n"
                f"💬 <b>Comment:</b> {event.message.message}\n"
                f"🏠 <b>Group:</b> {chat.title}"
            )
            try: await bot_app.bot.send_message(chat_id=OWNER_ID, text=msg, parse_mode="HTML")
            except: pass

# ================== KEYBOARDS ==================
def main_menu():
    kb = [
        [KeyboardButton("LOGIN ACCOUNT")],
        [KeyboardButton("ADD TEXT"), KeyboardButton("DELETE TEXT")],
        [KeyboardButton("ADD PHOTO"), KeyboardButton("DELETE PHOTO")],
        [KeyboardButton("SET GROUPS"), KeyboardButton("SET INTERVAL")],
        [KeyboardButton("RUN ADS"), KeyboardButton("STOP ADS")],
        [KeyboardButton("VIEW SETTINGS")]
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def interval_menu():
    kb = [
        [InlineKeyboardButton("8 Seconds", callback_data="int_8"), InlineKeyboardButton("15 Seconds", callback_data="int_15")],
        [InlineKeyboardButton("30 Seconds", callback_data="int_30"), InlineKeyboardButton("50 Seconds", callback_data="int_50")]
    ]
    return InlineKeyboardMarkup(kb)

# ================== BOT HANDLERS ==================
bot_app = None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text("👑 Welcome Owner!\n\nManage your ads using the menu.", reply_markup=main_menu())

async def view_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = get_setting("ad_text", "Not Set")
    photo = "Set" if get_setting("ad_photo") else "Not Set"
    groups = get_setting("ad_groups", "Not Set")
    interval = get_setting("ad_interval", "Not Set")
    
    msg = f"<b>⚙️ SETTINGS</b>\n\n<b>Text:</b> {text}\n<b>Photo:</b> {photo}\n<b>Groups:</b> {groups}\n<b>Interval:</b> {interval}s"
    await update.message.reply_text(msg, parse_mode="HTML")

# --- LOGIN ---
async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Apna phone number daalein (+91...):")
    return ASK_PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith("+"): return await update.message.reply_text("Format galat hai. +91 lagayein.")
    context.user_data['phone'] = phone
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    try:
        sent_code = await client.send_code_request(phone)
        context.user_data['temp_client'] = client
        context.user_data['hash'] = sent_code.phone_code_hash
        kb = [[InlineKeyboardButton(str(i), callback_data=f"otp_{i}") for i in range(1,4)],
              [InlineKeyboardButton(str(i), callback_data=f"otp_{i}") for i in range(4,7)],
              [InlineKeyboardButton(str(i), callback_data=f"otp_{i}") for i in range(7,10)],
              [InlineKeyboardButton("CLEAR", callback_data="otp_clear"), InlineKeyboardButton("0", callback_data="otp_0"), InlineKeyboardButton("SUBMIT", callback_data="otp_submit")]]
        context.user_data['otp_entered'] = ""
        await update.message.reply_text("Enter OTP:", reply_markup=InlineKeyboardMarkup(kb))
        return ASK_OTP
    except Exception as e:
        await update.message.reply_text(f"Error: {e}"); return ConversationHandler.END

async def otp_keypad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; data = query.data.replace("otp_", "")
    otp = context.user_data.get('otp_entered', "")
    if data == "clear": otp = ""
    elif data == "submit":
        if len(otp) < 4: return await query.answer("Kam se kam 4 digit daalein!")
        try:
            await context.user_data['temp_client'].sign_in(phone=context.user_data['phone'], code=otp, phone_code_hash=context.user_data['hash'])
            session_str = context.user_data['temp_client'].session.save()
            set_setting("session_string", session_str)
            await start_userbot(session_str)
            await query.message.delete()
            await bot_app.bot.send_message(chat_id=OWNER_ID, text="✅ Account Logged In!", reply_markup=main_menu())
            return ConversationHandler.END
        except Exception as e:
            if "Two-step" in str(e): await query.edit_message_text("2FA Password daalein:"); return ASK_2FA
            await query.edit_message_text(f"Failed: {e}"); return ConversationHandler.END
    else:
        if len(otp) < 5: otp += data
    context.user_data['otp_entered'] = otp
    try: await query.edit_message_text(f"OTP: {otp + '_' * (5 - len(otp))}", reply_markup=query.message.reply_markup)
    except: pass
    return ASK_OTP

async def get_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.user_data['temp_client'].sign_in(password=update.message.text.strip())
        session_str = context.user_data['temp_client'].session.save()
        set_setting("session_string", session_str)
        await start_userbot(session_str)
        await update.message.reply_text("✅ Logged In!", reply_markup=main_menu())
    except Exception as e: await update.message.reply_text(f"2FA failed: {e}")
    return ConversationHandler.END

# --- TEXT SETUP ---
async def add_text_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send text:"); return ASK_TEXT
async def save_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_setting("ad_text", update.message.text); await update.message.reply_text("✅ Text Saved!", reply_markup=main_menu()); return ConversationHandler.END
async def delete_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del_setting("ad_text"); await update.message.reply_text("🗑️ Text Deleted!", reply_markup=main_menu())

# --- PHOTO SETUP (FIXED WITH FILE ID) ---
async def add_photo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send photo:"); return ASK_PHOTO
async def save_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        set_setting("ad_photo", file_id) # Save File ID
        await update.message.reply_text("✅ Photo Saved Successfully!", reply_markup=main_menu())
    else:
        await update.message.reply_text("Please send a valid photo.")
    return ConversationHandler.END
async def delete_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del_setting("ad_photo"); await update.message.reply_text("🗑️ Photo Deleted!", reply_markup=main_menu())

# --- GROUPS & INTERVAL ---
async def set_groups_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send groups (comma separated):"); return ASK_GROUPS
async def save_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_setting("ad_groups", update.message.text); await update.message.reply_text("✅ Groups Saved!", reply_markup=main_menu()); return ConversationHandler.END

async def set_interval_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Select Interval:", reply_markup=interval_menu())
async def save_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    set_setting("ad_interval", query.data.split('_')[1])
    await query.message.reply_text(f"✅ Interval set!", reply_markup=main_menu())

# --- ADS CONTROL ---
async def run_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_ads_running, ads_task
    if is_ads_running: return await update.message.reply_text("⚠️ Already running!")
    if not userbot: return await update.message.reply_text("❌ Pehle account login karein!")
    is_ads_running = True; ads_task = asyncio.create_task(run_ads_loop())
    await update.message.reply_text("🚀 Ads Started!")

async def stop_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_ads_running, ads_task
    if not is_ads_running: return await update.message.reply_text("⚠️ Already stopped!")
    is_ads_running = False
    if ads_task: ads_task.cancel()
    await update.message.reply_text("🛑 Ads Stopped!")

# ================== MAIN ==================
def main():
    global bot_app
    threading.Thread(target=run_flask, daemon=True).start()
    
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    saved_session = get_setting("session_string")
    if saved_session: loop.run_until_complete(start_userbot(saved_session))
    
    bot_app = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    login_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex("^LOGIN ACCOUNT$"), login_start)], states={ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)], ASK_OTP: [CallbackQueryHandler(otp_keypad, pattern="^otp_")], ASK_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_2fa)]}, fallbacks=[])
    text_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex("^ADD TEXT$"), add_text_start)], states={ASK_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_text)]}, fallbacks=[])
    photo_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex("^ADD PHOTO$"), add_photo_start)], states={ASK_PHOTO: [MessageHandler(filters.PHOTO, save_photo)]}, fallbacks=[])
    groups_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex("^SET GROUPS$"), set_groups_start)], states={ASK_GROUPS: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_groups)]}, fallbacks=[])

    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(login_conv)
    bot_app.add_handler(text_conv)
    bot_app.add_handler(photo_conv)
    bot_app.add_handler(groups_conv)
    
    bot_app.add_handler(MessageHandler(filters.Regex("^DELETE TEXT$"), delete_text))
    bot_app.add_handler(MessageHandler(filters.Regex("^DELETE PHOTO$"), delete_photo))
    bot_app.add_handler(MessageHandler(filters.Regex("^SET INTERVAL$"), set_interval_start))
    bot_app.add_handler(CallbackQueryHandler(save_interval, pattern="^int_"))
    bot_app.add_handler(MessageHandler(filters.Regex("^RUN ADS$"), run_ads))
    bot_app.add_handler(MessageHandler(filters.Regex("^STOP ADS$"), stop_ads))
    bot_app.add_handler(MessageHandler(filters.Regex("^VIEW SETTINGS$"), view_settings))

    logger.info("Bot Started!")
    bot_app.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)

if __name__ == "__main__":
    main()
