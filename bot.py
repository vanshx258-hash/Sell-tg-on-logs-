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
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "8612102965"))
API_ID = int(os.environ.get("API_ID", "38746329"))
API_HASH = os.environ.get("API_HASH", "dc6d3dd0de462ed07952a64c3253c437")
SESSION_STRING = os.environ.get("SESSION_STRING", "YOUR_SESSION_STRING")
PORT = int(os.environ.get("PORT", 10000))

# States
(ASK_TEXT, ASK_PHOTO, ASK_GROUPS) = range(3)

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

async def run_ads_loop():
    global is_ads_running, userbot
    while is_ads_running:
        text = get_setting("ad_text", "")
        photo = get_setting("ad_photo", "")
        groups_str = get_setting("ad_groups", "")
        interval = int(get_setting("ad_interval", "10"))
        
        if not groups_str or not userbot:
            await asyncio.sleep(5); continue
            
        groups = [g.strip() for g in groups_str.split(',') if g.strip()]
        
        for group in groups:
            try:
                sent_msg = None
                if photo and os.path.exists(photo):
                    sent_msg = await userbot.send_file(group, photo, caption=text)
                elif text:
                    sent_msg = await userbot.send_message(group, text)
                
                if sent_msg:
                    save_sent_msg(sent_msg.id, group)
                    logger.info(f"Sent ad to {group}")
                
                await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"Failed to send to {group}: {e}")
                
        await asyncio.sleep(interval)

async def start_userbot():
    global userbot
    if not SESSION_STRING or SESSION_STRING == "YOUR_SESSION_STRING":
        logger.error("Session string missing!")
        return

    userbot = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await userbot.connect()
    logger.info("Userbot connected!")
    
    # REPLY & TAG DETECTOR
    @userbot.on(events.NewMessage(incoming=True))
    async def alert_handler(event):
        if event.is_private: return
        
        is_reply_to_us = False
        if event.message.reply_to_msg_id:
            if is_our_msg(event.message.reply_to_msg_id):
                is_reply_to_us = True
        
        is_tagged = event.message.mentioned
        
        if is_reply_to_us or is_tagged:
            sender = await event.get_sender()
            chat = await event.get_chat()
            
            # 1. Userbot auto reply
            try:
                await event.reply("DM me to add to contact.")
            except: pass
            
            # 2. Owner Alert
            msg = (
                f"🔔 <b>NEW INTERACTION</b>\n\n"
                f"👤 <b>User:</b> {sender.first_name} (@{sender.username or 'None'})\n"
                f"🆔 <b>ID:</b> <code>{sender.id}</code>\n"
                f"🔗 <b>Profile:</b> <a href='tg://user?id={sender.id}'>Click Here</a>\n\n"
                f"💬 <b>Comment:</b> {event.message.message}\n"
                f"🏠 <b>Group:</b> {chat.title}"
            )
            try:
                await bot_app.bot.send_message(chat_id=OWNER_ID, text=msg, parse_mode="HTML")
            except: pass

# ================== KEYBOARDS ==================
def main_menu():
    kb = [
        [KeyboardButton("ADD TEXT"), KeyboardButton("ADD PHOTO")],
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
    await update.message.reply_text("👑 Welcome Owner!\n\nManage your ads using the menu below.", reply_markup=main_menu())

async def view_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = get_setting("ad_text", "Not set")
    photo = "Set" if get_setting("ad_photo") else "Not set"
    groups = get_setting("ad_groups", "Not set")
    interval = get_setting("ad_interval", "Not set")
    
    msg = f"<b>⚙️ CURRENT SETTINGS</b>\n\n"
    msg += f"<b>Text:</b> {text}\n"
    msg += f"<b>Photo:</b> {photo}\n"
    msg += f"<b>Groups:</b> {groups}\n"
    msg += f"<b>Interval:</b> {interval} seconds"
    await update.message.reply_text(msg, parse_mode="HTML")

# --- ADD TEXT ---
async def add_text_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send the text you want to send in groups:")
    return ASK_TEXT

async def save_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_setting("ad_text", update.message.text)
    await update.message.reply_text("✅ Text saved!", reply_markup=main_menu())
    return ConversationHandler.END

# --- ADD PHOTO ---
async def add_photo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send the photo you want to send:")
    return ASK_PHOTO

async def save_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        msg = await update.message.reply_text("⏳ Downloading photo, please wait...")
        photo_file = await update.message.photo[-1].get_file()
        path = "ad_photo.jpg"
        await photo_file.download_to_drive(path)
        set_setting("ad_photo", path)
        await msg.edit_text("✅ Photo saved successfully!")
    else:
        await update.message.reply_text("Please send a valid photo.")
    return ConversationHandler.END

# --- SET GROUPS ---
async def set_groups_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send 4-5 group links/usernames separated by comma:\n\nExample:\n@group1, @group2, https://t.me/group3")
    return ASK_GROUPS

async def save_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_setting("ad_groups", update.message.text)
    await update.message.reply_text("✅ Groups saved!", reply_markup=main_menu())
    return ConversationHandler.END

# --- SET INTERVAL ---
async def set_interval_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Select the time interval for sending ads:", reply_markup=interval_menu())

async def save_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    seconds = query.data.split('_')[1]
    set_setting("ad_interval", seconds)
    await query.message.reply_text(f"✅ Interval set to {seconds} seconds!", reply_markup=main_menu())

# --- RUN & STOP ADS ---
async def run_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_ads_running, ads_task
    if is_ads_running:
        return await update.message.reply_text("⚠️ Ads are already running!")
        
    is_ads_running = True
    ads_task = asyncio.create_task(run_ads_loop())
    await update.message.reply_text("🚀 Ads Started! Bot will now send messages in groups.")

async def stop_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_ads_running, ads_task
    if not is_ads_running:
        return await update.message.reply_text("⚠️ Ads are already stopped!")
        
    is_ads_running = False
    if ads_task:
        ads_task.cancel()
    await update.message.reply_text("🛑 Ads Stopped!")

# ================== MAIN ==================
def main():
    global bot_app
    threading.Thread(target=run_flask, daemon=True).start()
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_userbot())
    
    bot_app = Application.builder().token(BOT_TOKEN).build()

    # Conversations
    text_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex("^ADD TEXT$"), add_text_start)], states={ASK_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_text)]}, fallbacks=[])
    photo_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex("^ADD PHOTO$"), add_photo_start)], states={ASK_PHOTO: [MessageHandler(filters.PHOTO, save_photo)]}, fallbacks=[])
    groups_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex("^SET GROUPS$"), set_groups_start)], states={ASK_GROUPS: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_groups)]}, fallbacks=[])

    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(text_conv)
    bot_app.add_handler(photo_conv)
    bot_app.add_handler(groups_conv)
    
    bot_app.add_handler(MessageHandler(filters.Regex("^SET INTERVAL$"), set_interval_start))
    bot_app.add_handler(CallbackQueryHandler(save_interval, pattern="^int_"))
    bot_app.add_handler(MessageHandler(filters.Regex("^RUN ADS$"), run_ads))
    bot_app.add_handler(MessageHandler(filters.Regex("^STOP ADS$"), stop_ads))
    bot_app.add_handler(MessageHandler(filters.Regex("^VIEW SETTINGS$"), view_settings))

    logger.info("Bot Started successfully!")
    bot_app.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)

if __name__ == "__main__":
    main()
