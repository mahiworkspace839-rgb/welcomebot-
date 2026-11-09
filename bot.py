"""
bot.py - 24/7 chalne wala Telegram bot jo Join aur Leave ko live track karta hai.
"""
import os
import logging
import asyncio
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
    MessageHandler,
    MessageReactionHandler,
    filters,
)
import database

load_dotenv()

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")    # Sirf aapka Telegram User ID
ALLOWED_GROUP_ID = os.getenv("ALLOWED_GROUP_ID")  # Sirf aapka Group ID (e.g. -100xxxxxxxxxx)

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

async def is_allowed(update: Update) -> bool:
    """
    Security Filter:
    1. Personal DM me: Sirf ADMIN_USER_ID ko reply karega, baaki sabke liye chup chap rahega.
    2. Group me: Agar kisi aur ka group hai to turant Leave karega aur chup chap rahega.
    """
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return False

    # 1. Personal DM chat
    if chat.type == "private":
        if ADMIN_USER_ID and str(user.id) != str(ADMIN_USER_ID):
            # Kisi anjaan ko koi reply nahi, bilkul silent
            return False
        return True

    # 2. Group chat
    if chat.type in ["group", "supergroup"]:
        # Agar koi aur apne group me add kare to auto-leave
        if ALLOWED_GROUP_ID and str(chat.id) != str(ALLOWED_GROUP_ID):
            try:
                await chat.leave()
            except Exception:
                pass
            return False

        # Group me commands sirf aap run kar sakein
        if ADMIN_USER_ID and str(user.id) != str(ADMIN_USER_ID):
            return False
        return True

    return False

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command instructions ke sath."""
    if not await is_allowed(update):
        return  # Silent ignore

    msg = (
        "👋 **Namaste! Main Member Tracking Bot hoon.**\n\n"
        "Main group ke members ka aana-jaana (Join/Left) real-time me record karta hoon.\n\n"
        "📌 **Aapke Commands:**\n"
        "• `/members` ya `/list` - Sabhi members ki poori details (Name, User ID, Username, Dates) DM me paayein\n"
        "• `/export` - Excel file (.xlsx) seedha DM me paayein\n"
        "• `/stats` - Active aur left members ka live count\n"
        "• `/left` - Haal hi me group chhodne wale members\n\n"
        "🔒 *Security: Yeh bot sirf aapke group aur aapke messages par active hai.*"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Current stats dikhata hai."""
    if not await is_allowed(update):
        return  # Silent ignore

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
    if not await is_allowed(update):
        return  # Silent ignore

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

async def members_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sabhi members ka pura detail (Name, ID, Status, Dates) seedha DM pe bhejta hai."""
    if not await is_allowed(update):
        return  # Silent ignore

    user = update.effective_user
    chat = update.effective_chat
    target_chat_id = user.id  # Pura data seedha aapke DM pe jayega

    if chat.type in ["group", "supergroup"]:
        await update.message.reply_text("🔒 Group me privacy ke liye poori list aapke Personal DM me bhej di gayi hai!")

    members = database.get_all_members()
    if not members:
        await context.bot.send_message(
            chat_id=target_chat_id,
            text="ℹ️ Abhi tak database me koi member record nahi hua hai."
        )
        return

    total = len(members)
    await context.bot.send_message(
        chat_id=target_chat_id,
        text=f"📋 **Total Members List ({total}):**\nHar member ki poori details neeche hain:"
    )

    # Chunking: 15 members per message taaki Telegram text limit exceed na ho
    chunk_size = 15
    for i in range(0, total, chunk_size):
        chunk = members[i:i + chunk_size]
        text_lines = []
        for idx, m in enumerate(chunk, start=i + 1):
            fname = m.get("first_name") or ""
            lname = m.get("last_name") or ""
            full_name = f"{fname} {lname}".strip() or "No Name"
            uname = f"@{m.get('username')}" if m.get("username") else "None"
            uid = m.get("_id") or m.get("user_id")
            status = m.get("status", "active")
            status_icon = "🟢 Active" if status == "active" else "🔴 Left"
            joined = m.get("joined_at", "N/A")
            left = m.get("left_at")

            entry = (
                f"**{idx}. {full_name}**\n"
                f"   🆔 **User ID:** `{uid}`\n"
                f"   👤 **Username:** {uname}\n"
                f"   📌 **Status:** {status_icon}\n"
                f"   📅 **Joined:** `{joined}`"
            )
            if left:
                entry += f"\n   🚪 **Left At:** `{left}`"
            text_lines.append(entry)

        message_chunk = "\n\n".join(text_lines)
        await context.bot.send_message(
            chat_id=target_chat_id,
            text=message_chunk,
            parse_mode="Markdown"
        )

async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Excel file generate karke seedha DM pe bhejta hai."""
    if not await is_allowed(update):
        return  # Silent ignore

    user = update.effective_user
    chat = update.effective_chat
    target_chat_id = user.id

    if chat.type in ["group", "supergroup"]:
        await update.message.reply_text("🔒 Excel report aapke Personal DM me bhej di gayi hai!")

    status_msg = await context.bot.send_message(
        chat_id=target_chat_id,
        text="⏳ Excel report taiyaar ho rahi hai, kripya intezar karein..."
    )
    try:
        filepath = database.export_to_excel("telegram_members_live_report.xlsx")
        with open(filepath, "rb") as f:
            await context.bot.send_document(
                chat_id=target_chat_id,
                document=f,
                filename="telegram_members_live_report.xlsx",
                caption="📊 **Members Full Report (Excel File)**\nIsme sabhi members ka User ID, Naam, Username, Phone aur Dates hain."
            )
        await status_msg.delete()
    except Exception as e:
        logger.error(f"Excel export error: {e}")
        await status_msg.edit_text(f"❌ File export karne me error aaya: {e}")

async def namechanges_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Haal hi ki sabhi name changes ki history dikhata hai."""
    if not await is_allowed(update):
        return

    changes = database.get_recent_name_changes(limit=15)
    if not changes:
        await update.message.reply_text("ℹ️ Abhi tak kisi ne bhi apna naam nahi badla hai.")
        return

    text = "🔄 **Recent Name Changes:**\n\n"
    for idx, c in enumerate(changes, 1):
        old_name = f"{c.get('old_first_name', '')} {c.get('old_last_name', '')}".strip() or "No Name"
        new_name = f"{c.get('new_first_name', '')} {c.get('new_last_name', '')}".strip() or "No Name"
        old_uname = f"@{c.get('old_username')}" if c.get("old_username") else "None"
        new_uname = f"@{c.get('new_username')}" if c.get("new_username") else "None"
        changed_at = c.get("changed_at", "N/A")
        uid = c.get("user_id")

        entry = f"{idx}. 🆔 `{uid}`\n"
        entry += f"   📛 **Purana Naam:** {old_name} ({old_uname})\n"
        entry += f"   ✨ **Naya Naam:** {new_name} ({new_uname})\n"
        entry += f"   🕒 **Changed At:** `{changed_at}`\n\n"
        text += entry

    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_name_change(chat, user):
    """Common helper: Message, Reaction ya Event aane par name change detect karke alert bhejta hai."""
    if not user or user.is_bot or not chat:
        return
    if chat.type not in ["group", "supergroup"]:
        return
    if ALLOWED_GROUP_ID and str(chat.id) != str(ALLOWED_GROUP_ID):
        return

    change = database.check_name_change(
        user_id=user.id,
        current_first=user.first_name,
        current_last=user.last_name,
        current_username=user.username
    )

    if change is None:
        return  # Koi change nahi mila

    alert_parts = []
    if change["name_changed"]:
        alert_parts.append(
            f"📛 **Purana Naam:** {change['old_name']}\n"
            f"✨ **Naya Naam:** {change['new_name']}"
        )
    if change["username_changed"]:
        old_u = f"@{change['old_username']}" if change["old_username"] else "None"
        new_u = f"@{change['new_username']}" if change["new_username"] else "None"
        alert_parts.append(
            f"📛 **Purana Username:** {old_u}\n"
            f"✨ **Naya Username:** {new_u}"
        )

    alert_text = (
        f"🔄 **Name Change Detected!**\n\n"
        f"🆔 **User ID:** `{change['user_id']}`\n"
        + "\n".join(alert_parts) +
        f"\n🕒 **Changed At:** `{change['changed_at']}`"
    )

    try:
        sent_alert = await chat.send_message(text=alert_text, parse_mode="Markdown")
        logger.info(f"Name change alert sent for {user.id}")

        # 30 second baad alert group se khud-b-khud delete ho jaye
        async def auto_delete_alert(msg, delay=30):
            await asyncio.sleep(delay)
            try:
                await msg.delete()
                logger.info("Name change alert 30 second baad delete ho gaya.")
            except Exception as del_err:
                logger.warning(f"Alert auto-delete error: {del_err}")

        asyncio.create_task(auto_delete_alert(sent_alert, 30))
    except Exception as e:
        logger.error(f"Name change alert error: {e}")

async def check_name_on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Har message (text, photo, sticker, voice) aane par name change check karein."""
    if update.message and update.effective_user and update.effective_chat:
        await handle_name_change(update.effective_chat, update.effective_user)

async def check_name_on_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kisi message par Emoji Reaction aane par bhi name change check karein."""
    reaction = update.message_reaction
    if reaction and reaction.chat and reaction.user:
        await handle_name_change(reaction.chat, reaction.user)

async def track_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Real-time join aur leave events ko capture karta hai."""
    result = update.chat_member
    if not result:
        return

    chat = update.effective_chat
    # Security: Agar kisi aur ka group hai to seedha auto-leave
    if ALLOWED_GROUP_ID and str(chat.id) != str(ALLOWED_GROUP_ID):
        logger.warning(f"Unauthorized group {chat.id} attempted tracking. Leaving...")
        try:
            await chat.leave()
        except Exception:
            pass
        return

    user = result.new_chat_member.user

    # 1. Strict Bot Filter: Agar user bot hai, toh bilkul ignore karein
    if user.is_bot:
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

        # Group me exactly ye message bhejna:
        welcome_text = (
            f"🎉 Welcome {user.first_name}\n\n"
            f"✅ Admission Confirmed!"
        )
        try:
            sent_msg = await chat.send_message(text=welcome_text)
            
            # 1 Minute (60 seconds) baad message khud-b-khud delete ho jaye
            async def auto_delete(msg, delay=60):
                await asyncio.sleep(delay)
                try:
                    await msg.delete()
                    logger.info("Welcome message 1 minute baad auto-delete ho gaya.")
                except Exception as del_err:
                    logger.warning(f"Auto-delete error: {del_err}")

            asyncio.create_task(auto_delete(sent_msg, 60))
        except Exception as e:
            logger.error(f"Welcome message error: {e}")

    # Case 2: Member ne LEAVE kiya ya Kick/Ban hua
    elif was_member and not is_member:
        logger.info(f"Member LEFT: {user.first_name} (ID: {user.id})")
        database.member_left(
            user_id=user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username
        )

async def track_bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot ko kisi unauthorized group me add kiya jaye to turant leave kar de."""
    chat = update.effective_chat
    if not chat or chat.type not in ["group", "supergroup"]:
        return

    if ALLOWED_GROUP_ID and str(chat.id) != str(ALLOWED_GROUP_ID):
        logger.warning(f"Bot unauthorized group me add hua ({chat.id} - {chat.title}). Auto-leaving...")
        try:
            await chat.leave()
        except Exception as e:
            logger.error(f"Group leave error: {e}")

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()

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
    application.add_handler(CommandHandler("members", members_cmd))
    application.add_handler(CommandHandler("list", members_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(CommandHandler("left", recent_left_cmd))
    application.add_handler(CommandHandler("namechanges", namechanges_cmd))
    application.add_handler(CommandHandler("export", export_cmd))

    # Real-time Chat Member updates listener
    application.add_handler(
        ChatMemberHandler(track_chat_member, ChatMemberHandler.CHAT_MEMBER)
    )
    application.add_handler(
        ChatMemberHandler(track_bot_added, ChatMemberHandler.MY_CHAT_MEMBER)
    )

    # Har message par Name Change check karne ke liye listener
    application.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, check_name_on_message)
    )
    # Emoji Reaction aane par bhi check karein
    application.add_handler(
        MessageReactionHandler(check_name_on_reaction)
    )

    print("🚀 Member Tracker Bot start ho gaya hai! Listening for joins & leaves...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
