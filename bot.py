import os
import asyncio
import sqlite3
import threading
import logging
import time
from datetime import datetime
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, ChatWriteForbiddenError, ChannelPrivateError

# ================== CONFIGURATION ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
OWNER_ID = int(os.environ.get("OWNER_ID", "8612102965"))
API_ID = int(os.environ.get("API_ID", "38746329"))
API_HASH = os.environ.get("API_HASH", "dc6d3dd0de462ed07952a64c3253c437")
PORT = int(os.environ.get("PORT", 10000))

(ASK_PHONE, ASK_OTP, ASK_2FA, ASK_TEXT, ASK_PHOTO, ASK_GROUPS) = range(6)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== FLASK SERVER (For Render 24/7) ==================
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    return "Ads Master Bot is Running 24/7!", 200

def run_flask_server():
    flask_app.run(host="0.0.0.0", port=PORT, use_reloader=False)

# ================== DATABASE MANAGEMENT ==================
DB_NAME = "ads_master_database.db"

def init_database():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS sent_logs (msg_id INTEGER PRIMARY KEY, group_id TEXT)""")
    conn.commit()
    conn.close()

def get_config(key, default=None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT value FROM config WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else default

def set_config(key, value):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?,?)", (key, str(value)))
    conn.commit()
    conn.close()

def del_config(key):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM config WHERE key=?", (key,))
    conn.commit()
    conn.close()

def log_sent_message(msg_id, group_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO sent_logs (msg_id, group_id) VALUES (?,?)", (msg_id, str(group_id)))
    conn.commit()
    conn.close()

def check_if_our_message(msg_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT msg_id FROM sent_logs WHERE msg_id=?", (msg_id,))
    row = c.fetchone()
    conn.close()
    return bool(row)

init_database()

# ================== USERBOT LOGIC ==================
userbot_client = None
ads_running_status = False
ads_background_task = None

async def send_to_single_group(group, ad_text, ad_photo_id):
    """Har ek group me safely message bhejne ka function"""
    global userbot_client
    try:
        sent_message = None
        
        # 1. Agar Photo Set Hai
        if ad_photo_id:
            sent_message = await userbot_client.send_file(
                entity=group,
                file=ad_photo_id,
                caption=ad_text
            )
        # 2. Agar Sirf Text Set Hai
        elif ad_text:
            sent_message = await userbot_client.send_message(
                entity=group,
                message=ad_text
            )
            
        if sent_message:
            log_sent_message(sent_message.id, group)
            logger.info(f"[SUCCESS] Ad sent to {group}")
            
    except FloodWaitError as e:
        logger.warning(f"[FLOODWAIT] Telegram ne block kiya hai. Wait for {e.seconds} seconds. Group: {group}")
        await asyncio.sleep(e.seconds)
    except ChatWriteForbiddenError:
        logger.error(f"[ERROR] Bot group me message nahi bhej sakta (Admin nahi hai). Group: {group}")
    except ChannelPrivateError:
        logger.error(f"[ERROR] Group private hai ya account ban hai. Group: {group}")
    except Exception as e:
        logger.error(f"[FAILED] {group} me error aaya: {e}")

async def ads_loop_worker():
    """Yeh loop 24/7 background me chalega aur ads bhejta rahega"""
    global ads_running_status, userbot_client
    logger.info("Ads Loop Worker Started.")
    
    while ads_running_status:
        try:
            ad_text = get_config("ad_text", "")
            ad_photo_id = get_config("ad_photo", "") # File ID
            ad_groups_string = get_config("ad_groups", "")
            interval_seconds = int(get_config("ad_interval", "8"))
            
            if not ad_groups_string or not userbot_client:
                logger.warning("Groups set nahi hain ya Userbot connected nahi hai.")
                await asyncio.sleep(3)
                continue
                
            groups_list = [g.strip() for g in ad_groups_string.split(',') if g.strip()]
            
            # Saare groups me ek sath (Parallel) message bhejna
            tasks = [send_to_single_group(group, ad_text, ad_photo_id) for group in groups_list]
            await asyncio.gather(*tasks)
            
            logger.info(f"All groups processed. Sleeping for {interval_seconds} seconds.")
            await asyncio.sleep(interval_seconds)
            
        except asyncio.CancelledError:
            logger.info("Ads Loop Cancelled.")
            break
        except Exception as e:
            logger.error(f"Loop Error: {e}")
            await asyncio.sleep(5)

async def initialize_userbot(session_str):
    """Account Login hone ke baad isko start karna"""
    global userbot_client
    if userbot_client:
        try: await userbot_client.disconnect()
        except: pass

    try:
        userbot_client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        await userbot_client.connect()
        logger.info("Userbot Connected Successfully.")
        
        # Reply aur Tag Detect karne ka system
        @userbot_client.on(events.NewMessage(incoming=True))
        async def interaction_handler(event):
            if event.is_private: return
            
            is_reply_to_us = False
            if event.message.reply_to_msg_id:
                if check_if_our_message(event.message.reply_to_msg_id):
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
                alert_text = (
                    f"🔔 <b>NEW INTERACTION ALERT</b>\n\n"
                    f"👤 <b>Name:</b> {sender.first_name} (@{sender.username or 'None'})\n"
                    f"🆔 <b>ID:</b> <code>{sender.id}</code>\n"
                    f"🔗 <b>Profile:</b> <a href='tg://user?id={sender.id}'>Click Here</a>\n\n"
                    f"💬 <b>Message:</b> {event.message.message}\n"
                    f"🏠 <b>Group:</b> {chat.title}"
                )
                try:
                    await bot_app.bot.send_message(chat_id=OWNER_ID, text=alert_text, parse_mode="HTML")
                except: pass
                
    except Exception as e:
        logger.error(f"Userbot Init Failed: {e}")

# ================== KEYBOARDS ==================
def get_main_keyboard():
    keyboard_layout = [
        [KeyboardButton("LOGIN ACCOUNT"), KeyboardButton("VIEW SETTINGS")],
        [KeyboardButton("ADD TEXT"), KeyboardButton("DELETE TEXT")],
        [KeyboardButton("ADD PHOTO"), KeyboardButton("DELETE PHOTO")],
        [KeyboardButton("SET GROUPS"), KeyboardButton("SET INTERVAL")],
        [KeyboardButton("RUN ADS"), KeyboardButton("STOP ADS")]
    ]
    return ReplyKeyboardMarkup(keyboard_layout, resize_keyboard=True)

def get_interval_keyboard():
    layout = [
        [InlineKeyboardButton("8 Seconds", callback_data="int_8"), InlineKeyboardButton("15 Seconds", callback_data="int_15")],
        [InlineKeyboardButton("30 Seconds", callback_data="int_30"), InlineKeyboardButton("50 Seconds", callback_data="int_50")]
    ]
    return InlineKeyboardMarkup(layout)

# ================== BOT HANDLERS ==================
bot_app = None

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("You are not authorized.")
    await update.message.reply_text("👑 Welcome Owner!\n\nManage your ads using the menu.", reply_markup=get_main_keyboard())

async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = get_config("ad_text", "Not Set")
    photo = "Set" if get_config("ad_photo") else "Not Set"
    groups = get_config("ad_groups", "Not Set")
    interval = get_config("ad_interval", "Not Set")
    
    msg = (
        f"<b>⚙️ CURRENT SETTINGS</b>\n\n"
        f"<b>Text:</b> {text}\n"
        f"<b>Photo:</b> {photo}\n"
        f"<b>Groups:</b> {groups}\n"
        f"<b>Interval:</b> {interval} seconds"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

# --- LOGIN FLOW ---
async def start_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Enter phone number (e.g. +919876543210):")
    return ASK_PHONE

async def process_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith("+"):
        await update.message.reply_text("Invalid format. Include country code (+91).")
        return ASK_PHONE
    
    context.user_data['phone'] = phone
    temp_client = TelegramClient(StringSession(), API_ID, API_HASH)
    await temp_client.connect()
    
    try:
        sent_code = await temp_client.send_code_request(phone)
        context.user_data['temp_client'] = temp_client
        context.user_data['hash'] = sent_code.phone_code_hash
        
        keypad = [
            [InlineKeyboardButton("1", callback_data="otp_1"), InlineKeyboardButton("2", callback_data="otp_2"), InlineKeyboardButton("3", callback_data="otp_3")],
            [InlineKeyboardButton("4", callback_data="otp_4"), InlineKeyboardButton("5", callback_data="otp_5"), InlineKeyboardButton("6", callback_data="otp_6")],
            [InlineKeyboardButton("7", callback_data="otp_7"), InlineKeyboardButton("8", callback_data="otp_8"), InlineKeyboardButton("9", callback_data="otp_9")],
            [InlineKeyboardButton("CLEAR", callback_data="otp_clear"), InlineKeyboardButton("0", callback_data="otp_0"), InlineKeyboardButton("SUBMIT", callback_data="otp_submit")]
        ]
        context.user_data['otp_entered'] = ""
        await update.message.reply_text("Enter OTP:", reply_markup=InlineKeyboardMarkup(keypad))
        return ASK_OTP
    except Exception as e:
        await update.message.reply_text(f"Failed to send OTP: {e}")
        return ConversationHandler.END

async def process_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data.replace("otp_", "")
    current_otp = context.user_data.get('otp_entered', "")
    
    if data == "clear":
        current_otp = ""
    elif data == "submit":
        if len(current_otp) < 4:
            return await query.answer("Enter at least 4 digits!")
            
        client = context.user_data['temp_client']
        try:
            await client.sign_in(phone=context.user_data['phone'], code=current_otp, phone_code_hash=context.user_data['hash'])
            session_str = client.session.save()
            set_config("session_string", session_str)
            await initialize_userbot(session_str)
            
            await query.message.delete()
            await bot_app.bot.send_message(chat_id=OWNER_ID, text="✅ Account Logged In!", reply_markup=get_main_keyboard())
            return ConversationHandler.END
        except Exception as e:
            if "Two-step verification" in str(e):
                await query.edit_message_text("Enter 2FA Password:")
                return ASK_2FA
            await query.edit_message_text(f"Login Failed: {e}")
            return ConversationHandler.END
    else:
        if len(current_otp) < 5: current_otp += data
        
    context.user_data['otp_entered'] = current_otp
    masked_otp = current_otp + "_" * (5 - len(current_otp))
    try: await query.edit_message_text(f"OTP: {masked_otp}", reply_markup=query.message.reply_markup)
    except: pass
    return ASK_OTP

async def process_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    client = context.user_data['temp_client']
    try:
        await client.sign_in(password=password)
        session_str = client.session.save()
        set_config("session_string", session_str)
        await initialize_userbot(session_str)
        await update.message.reply_text("✅ Logged In via 2FA!", reply_markup=get_main_keyboard())
    except Exception as e:
        await update.message.reply_text(f"2FA Failed: {e}")
    return ConversationHandler.END

# --- TEXT & PHOTO SETUP ---
async def start_add_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send the text for your ads:")
    return ASK_TEXT

async def save_ad_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_config("ad_text", update.message.text)
    await update.message.reply_text("✅ Text saved successfully!", reply_markup=get_main_keyboard())
    return ConversationHandler.END

async def delete_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del_config("ad_text")
    await update.message.reply_text("🗑️ Text Deleted Successfully!", reply_markup=get_main_keyboard())

async def start_add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send the photo for your ads:")
    return ASK_PHOTO

async def save_ad_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        set_config("ad_photo", file_id)
        await update.message.reply_text("✅ Photo saved successfully!", reply_markup=get_main_keyboard())
    else:
        await update.message.reply_text("Please send a valid photo.")
    return ConversationHandler.END

async def delete_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del_config("ad_photo")
    await update.message.reply_text("🗑️ Photo Deleted Successfully!", reply_markup=get_main_keyboard())

# --- GROUPS & INTERVAL SETUP ---
async def start_set_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send group links or usernames (comma separated):\n\nExample:\n@group1, https://t.me/group2")
    return ASK_GROUPS

async def save_ad_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_config("ad_groups", update.message.text)
    await update.message.reply_text("✅ Groups saved!", reply_markup=get_main_keyboard())
    return ConversationHandler.END

async def start_set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Select time interval:", reply_markup=get_interval_keyboard())

async def save_ad_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    seconds = query.data.split('_')[1]
    set_config("ad_interval", seconds)
    await query.message.reply_text(f"✅ Interval set to {seconds} seconds!", reply_markup=get_main_keyboard())

# --- ADS CONTROL ---
async def start_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ads_running_status, ads_background_task
    if ads_running_status:
        return await update.message.reply_text("⚠️ Ads are already running!")
    if not userbot_client:
        return await update.message.reply_text("❌ Please login your account first!")
        
    ads_running_status = True
    ads_background_task = asyncio.create_task(ads_loop_worker())
    await update.message.reply_text("🚀 Ads Started! Bot is now sending messages.")

async def stop_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ads_running_status, ads_background_task
    if not ads_running_status:
        return await update.message.reply_text("⚠️ Ads are already stopped!")
        
    ads_running_status = False
    if ads_background_task:
        ads_background_task.cancel()
    await update.message.reply_text("🛑 Ads Stopped!")

# ================== APPLICATION ENTRY POINT ==================
def main():
    global bot_app
    
    threading.Thread(target=run_flask_server, daemon=True).start()
    
    event_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(event_loop)
    
    saved_session = get_config("session_string")
    if saved_session:
        event_loop.run_until_complete(initialize_userbot(saved_session))
    
    bot_app = Application.builder().token(BOT_TOKEN).build()

    login_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^LOGIN ACCOUNT$"), start_login)],
        states={
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_phone)],
            ASK_OTP: [CallbackQueryHandler(process_otp, pattern="^otp_")],
            ASK_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_2fa)]
        },
        fallbacks=[]
    )
    
    text_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^ADD TEXT$"), start_add_text)],
        states={ASK_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_ad_text)]},
        fallbacks=[]
    )
    
    photo_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^ADD PHOTO$"), start_add_photo)],
        states={ASK_PHOTO: [MessageHandler(filters.PHOTO, save_ad_photo)]},
        fallbacks=[]
    )
    
    groups_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^SET GROUPS$"), start_set_groups)],
        states={ASK_GROUPS: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_ad_groups)]},
        fallbacks=[]
    )

    bot_app.add_handler(CommandHandler("start", cmd_start))
    bot_app.add_handler(login_handler)
    bot_app.add_handler(text_handler)
    bot_app.add_handler(photo_handler)
    bot_app.add_handler(groups_handler)
    
    bot_app.add_handler(MessageHandler(filters.Regex("^DELETE TEXT$"), delete_text))
    bot_app.add_handler(MessageHandler(filters.Regex("^DELETE PHOTO$"), delete_photo))
    bot_app.add_handler(MessageHandler(filters.Regex("^VIEW SETTINGS$"), show_settings))
    bot_app.add_handler(MessageHandler(filters.Regex("^SET INTERVAL$"), start_set_interval))
    bot_app.add_handler(CallbackQueryHandler(save_ad_interval, pattern="^int_"))
    bot_app.add_handler(MessageHandler(filters.Regex("^RUN ADS$"), start_ads))
    bot_app.add_handler(MessageHandler(filters.Regex("^STOP ADS$"), stop_ads))

    logger.info("All Systems Ready. Bot Polling Started.")
    bot_app.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)

if __name__ == "__main__":
    main()
