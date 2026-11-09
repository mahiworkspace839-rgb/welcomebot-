"""
bot.py - 24/7 chalne wala Telegram bot jo Join aur Leave ko live track karta hai.
"""
import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    ApplicationBuilder,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
)
import database

load_dotenv()

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")  # Optional: specific admin Telegram ID

MEMBER_STATUSES = {
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.OWNER,
    ChatMemberStatus.RESTRICTED,
}

NON_MEMBER_STATUSES = {
    ChatMemberStatus.LEFT,
    ChatMemberStatus.BANNED,
}

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check karta hai ki command bhejnewala admin hai ya nahi."""
    user = update.effective_user
    if not user:
        return False
    
    # Agar .env me ADMIN_USER_ID diya hai to verify karein
    if ADMIN_USER_ID and str(user.id) == str(ADMIN_USER_ID):
        return True

    # Agar group chat me hai to chat admins check karein
    chat = update.effective_chat
    if chat and chat.type in ["group", "supergroup"]:
        try:
            admins = await chat.get_administrators()
            admin_ids = [admin.user.id for admin in admins]
            return user.id in admin_ids
        except Exception:
            return False

    # Agar private chat hai aur koi ADMIN_USER_ID set nahi hai to allow karein
    return True

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command instructions ke sath."""
    msg = (
        "👋 **Namaste! Main Member Tracking Bot hoon.**\n\n"
        "Main group ke members ka aana-jaana (Join/Left) real-time me record karta hoon.\n\n"
        "📌 **Admin Commands:**\n"
        "• `/stats` - Group ke active aur left members ki ginti\n"
        "• `/left` - Haal hi me group chhodne wale members\n"
        "• `/export` - Excel file download karein (Full Report)\n\n"
        "⚠️ *Note: Bots ko automatically filter/skip kar diya jata hai.*"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Current stats dikhata hai."""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Yeh command sirf Admins ke liye hai.")
        return

    stats = database.get_stats()
    text = (
        "📊 **Group Member Statistics:**\n\n"
        f"🟢 **Active Members:** {stats['active']}\n"
        f"🔴 **Left Members:** {stats['left']}\n"
        f"📁 **Total Tracked in DB:** {stats['total']}\n\n"
        "Full list download karne ke liye `/export` command use karein."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def recent_left_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Haal hi me group chhodne walo ki list."""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Yeh command sirf Admins ke liye hai.")
        return

    recent = database.get_recent_left(limit=10)
    if not recent:
        await update.message.reply_text("ℹ️ Abhi tak kisi bhi member ne group nahi chhoda hai.")
        return

    text = "🔴 **Recently Group Chhodne Wale Members:**\n\n"
    for idx, row in enumerate(recent, 1):
        uid, fname, lname, uname, left_at = row
        name = f"{fname or ''} {lname or ''}".strip() or "Unnamed"
        username_str = f"(@{uname})" if uname else "(No Username)"
        text += f"{idx}. **{name}** {username_str}\n   🕒 Left At: `{left_at}`\n   🆔 ID: `{uid}`\n\n"

    await update.message.reply_text(text, parse_mode="Markdown")

async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Excel file generate karke bhejta hai."""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Yeh command sirf Admins ke liye hai.")
        return

    msg = await update.message.reply_text("⏳ Excel report taiyaar ho rahi hai, kripya intezar karein...")
    try:
        filepath = database.export_to_excel("telegram_members_live_report.xlsx")
        with open(filepath, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename="telegram_members_live_report.xlsx",
                caption="📊 **Members Report (Excel File)**\nIsme sabhi Active aur Left members ka pura data hai."
            )
        await msg.delete()
    except Exception as e:
        logger.error(f"Excel export error: {e}")
        await msg.edit_text(f"❌ File export karne me error aaya: {e}")

async def track_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Real-time join aur leave events ko capture karta hai."""
    result = update.chat_member
    if not result:
        return

    user = result.new_chat_member.user

    # 1. Strict Bot Filter: Agar user bot hai, toh bilkul ignore karein
    if user.is_bot:
        logger.info(f"Bot detected and ignored: @{user.username} (ID: {user.id})")
        return

    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status

    was_member = old_status in MEMBER_STATUSES
    is_member = new_status in MEMBER_STATUSES

    # Case 1: Member ne JOIN kiya
    if not was_member and is_member:
        logger.info(f"Member JOINED: {user.first_name} (ID: {user.id})")
        database.member_joined(
            user_id=user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username
        )

    # Case 2: Member ne LEAVE kiya ya Kick/Ban hua
    elif was_member and not is_member:
        logger.info(f"Member LEFT: {user.first_name} (ID: {user.id})")
        database.member_left(
            user_id=user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username
        )

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - Telegram Member Tracker Bot is running!")

    def log_message(self, format, *args):
        # Noise reduce karne ke liye dummy access logs ko mute karein
        pass

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"Render Health Check Web Server running on port {port}")
    server.serve_forever()

def main():
    database.init_db()

    # Render Web Service ke liye background me dummy HTTP server start karein
    threading.Thread(target=run_health_server, daemon=True).start()

    token = BOT_TOKEN
    if not token:
        token = input("Apna Telegram Bot Token (@BotFather wala) darj karein: ").strip()

    application = (
        ApplicationBuilder()
        .token(token)
        .build()
    )

    # Handlers register karein
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(CommandHandler("left", recent_left_cmd))
    application.add_handler(CommandHandler("export", export_cmd))

    # Real-time Chat Member updates listener
    application.add_handler(
        ChatMemberHandler(track_chat_member, ChatMemberHandler.CHAT_MEMBER)
    )

    print("🚀 Member Tracker Bot start ho gaya hai! Listening for joins & leaves...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
