"""
bot.py - 24/7 chalne wala Telegram bot jo Join aur Leave ko live track karta hai.
"""
import os
import re
import logging
import asyncio
import threading
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatMemberStatus, MessageEntityType
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
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")    # Optional extra ID
ALLOWED_GROUP_ID = os.getenv("ALLOWED_GROUP_ID")  # Sirf aapka Group ID (e.g. -100xxxxxxxxxx)

# 1. Super Owner (Sirf Aap: 6416451659) - Sabhi Commands & Data Access sirf aapke liye
SUPER_OWNER_ID = "6416451659"

# 2. Links Whitelist (Sirf Aap aur 8914751355 link send kar sakte hain)
LINK_ALLOWED_IDS = {"6416451659", "8914751355"}

def is_super_owner(user_id: int) -> bool:
    """Check karta hai ki user sirf aur sirf MAIN OWNER (6416451659) hai."""
    uid = str(user_id)
    if uid == SUPER_OWNER_ID:
        return True
    if ADMIN_USER_ID and uid == str(ADMIN_USER_ID).strip():
        return True
    return False

def is_allowed_to_post_links(user_id: int) -> bool:
    """
    VIP Link Protection:
    Sirf 6416451659 (Aap) aur 8914751355 ko link bhejne ki chhoot hai.
    Baaki koi bhi ho (chahe koi aur admin ya aam member), unka link TURANT FAST delete hoga!
    """
    return str(user_id) in LINK_ALLOWED_IDS

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
    Commands Security:
    Sirf aur sirf 6416451659 (Aap) commands (/members, /export, /stats, /namechanges) chala sakte hain!
    8914751355 ya koi bhi dusra admin/member agar command chalayega toh bot bilkul chup chap rahega.
    """
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return False

    # Sirf aur sirf SUPER OWNER (6416451659) ko commands allow hain
    if not is_super_owner(user.id):
        return False

    # Agar group chat me hai to unauthorized group se auto-leave
    if chat.type in ["group", "supergroup"]:
        if ALLOWED_GROUP_ID and str(chat.id) != str(ALLOWED_GROUP_ID):
            try:
                await chat.leave()
            except Exception:
                pass
            return False

    return True

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command instructions ke sath."""
    if not await is_allowed(update):
        return  # Silent ignore

    msg = (
        "👋 **Namaste! Main Member Tracking & Analytics Bot hoon.**\n\n"
        "👑 **Owner Commands (Sirf Aapke Liye):**\n"
        "• `/whois <id/@username>` - Kisi member ki poori kundli (joins, messages, name changes)\n"
        "• `/top` - Top 10 sabse active chatters leaderboard\n"
        "• `/inactive [days]` - Silent/inactive members ki list (default: 15 days)\n"
        "• `/today` - Aaj ki 24-ghante ki summary report\n"
        "• `/members` ya `/list` - Sabhi members ki list DM me\n"
        "• `/export` - Excel file (.xlsx) download karein\n"
        "• `/namechanges` - Haal hi me badle gaye naamo ki history\n"
        "• `/stats` - Total active/left count\n"
        "• `/left` - Haal hi me group chhodne wale members\n\n"
        "⏰ *Daily Automated Report: Har raat theek 9:00 PM IST par aapke DM me summary aayegi!*"
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

async def whois_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User ki poori kundli: joins, message count, name changes, status."""
    if not await is_allowed(update):
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "ℹ️ Kripya User ID ya Username likhein:\n"
            "Example: `/whois 123456789` ya `/whois @username`",
            parse_mode="Markdown"
        )
        return

    query = args[0]
    data = database.get_user_history(query)
    if not data:
        await update.message.reply_text(f"❌ User `{query}` database me nahi mila.", parse_mode="Markdown")
        return

    status_str = "🟢 Active (Group me hai)" if data["status"] == "active" else f"🔴 Left (Left at: `{data['left_at']}`)"
    uname_str = f"@{data['username']}" if data["username"] else "(No Username)"
    full_name = f"{data['first_name']} {data['last_name']}".strip() or "No Name"

    text = (
        f"🔍 **User Kundli / Full History:**\n\n"
        f"👤 **Name:** {full_name}\n"
        f"🆔 **User ID:** `{data['user_id']}`\n"
        f"🔗 **Username:** {uname_str}\n"
        f"📌 **Status:** {status_str}\n"
        f"📅 **Joined Date:** `{data['joined_at']}`\n"
        f"💬 **Total Messages Sent:** {data['message_count']}\n"
        f"🕒 **Last Active:** `{data['last_message_at']}`\n"
    )

    nc = data["name_changes"]
    if nc:
        text += f"\n🔄 **Name Change History ({len(nc)}):**\n"
        for idx, c in enumerate(nc[:5], 1):
            o_name = f"{c.get('old_first_name', '')} {c.get('old_last_name', '')}".strip() or "No Name"
            n_name = f"{c.get('new_first_name', '')} {c.get('new_last_name', '')}".strip() or "No Name"
            o_u = f"@{c.get('old_username')}" if c.get("old_username") else "None"
            n_u = f"@{c.get('new_username')}" if c.get("new_username") else "None"
            text += f"{idx}. `{c.get('changed_at')}`: {o_name} ({o_u}) ➔ {n_name} ({n_u})\n"
    else:
        text += "\n🔄 **Name Change History:** Koi badlaav nahi hua."

    await update.message.reply_text(text, parse_mode="Markdown")

async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Top 10 active chatters leaderboard."""
    if not await is_allowed(update):
        return

    top_list = database.get_top_members(limit=10)
    if not top_list:
        await update.message.reply_text("ℹ️ Abhi tak group me kisi member ne message nahi kiya hai.")
        return

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    text = "🏆 **Top Active Members Leaderboard:**\n\n"
    for idx, m in enumerate(top_list):
        medal = medals[idx] if idx < len(medals) else f"{idx+1}."
        full_name = f"{m.get('first_name', '')} {m.get('last_name', '')}".strip() or "No Name"
        uname = f"(@{m.get('username')})" if m.get("username") else ""
        cnt = m.get("message_count", 0)
        text += f"{medal} **{full_name}** {uname}\n    💬 **Messages:** {cnt} | 🆔 `{m.get('_id')}`\n\n"

    await update.message.reply_text(text, parse_mode="Markdown")

async def inactive_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pichle 15 ya X din se silent members ki list."""
    if not await is_allowed(update):
        return

    days = 15
    if context.args and context.args[0].isdigit():
        days = int(context.args[0])

    inactives = database.get_inactive_members(days=days, limit=20)
    if not inactives:
        await update.message.reply_text(f"👏 Pichle {days} din me sabhi active members ne koi na koi message kiya hai!")
        return

    text = f"💤 **Inactive / Silent Members (Pichle {days} Din Se):**\n"
    text += "Yeh members group me hain lekin koi baat nahi kar rahe:\n\n"
    for idx, m in enumerate(inactives, 1):
        full_name = f"{m.get('first_name', '')} {m.get('last_name', '')}".strip() or "No Name"
        uname = f"(@{m.get('username')})" if m.get("username") else ""
        last_seen = m.get("last_message_at") or "Kabhi nahi (0 messages)"
        text += f"{idx}. **{full_name}** {uname}\n    🆔 `{m.get('_id')}` | 🕒 Last: `{last_seen}`\n\n"

    await update.message.reply_text(text, parse_mode="Markdown")

async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Aaj ki poori live report summary."""
    if not await is_allowed(update):
        return

    s = database.get_daily_summary_stats()
    text = (
        f"📈 **Aaj ki Report ({s['date']}):**\n\n"
        f"🟢 **Naye Joins:** {s['joins']}\n"
        f"🔴 **Left Members:** {s['left']}\n"
        f"🔄 **Name Changes:** {s['name_changes']}\n"
        f"👥 **Total Active Members:** {s['active_total']}\n"
        f"📁 **Total Tracked in DB:** {s['total_members']}\n"
    )
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

async def whois_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User ki poori kundli: joins, message count, name changes, status."""
    if not await is_allowed(update):
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "ℹ️ Kripya User ID ya Username likhein:\n"
            "Example: `/whois 123456789` ya `/whois @username`",
            parse_mode="Markdown"
        )
        return

    query = args[0]
    data = database.get_user_history(query)
    if not data:
        await update.message.reply_text(f"❌ User `{query}` database me nahi mila.", parse_mode="Markdown")
        return

    status_str = "🟢 Active (Group me hai)" if data["status"] == "active" else f"🔴 Left (Left at: `{data['left_at']}`)"
    uname_str = f"@{data['username']}" if data["username"] else "(No Username)"
    full_name = f"{data['first_name']} {data['last_name']}".strip() or "No Name"

    text = (
        f"🔍 **User Kundli / Full History:**\n\n"
        f"👤 **Name:** {full_name}\n"
        f"🆔 **User ID:** `{data['user_id']}`\n"
        f"🔗 **Username:** {uname_str}\n"
        f"📌 **Status:** {status_str}\n"
        f"📅 **Joined Date:** `{data['joined_at']}`\n"
        f"💬 **Total Messages Sent:** {data['message_count']}\n"
        f"🕒 **Last Active:** `{data['last_message_at']}`\n"
    )

    nc = data["name_changes"]
    if nc:
        text += f"\n🔄 **Name Change History ({len(nc)}):**\n"
        for idx, c in enumerate(nc[:5], 1):
            o_name = f"{c.get('old_first_name', '')} {c.get('old_last_name', '')}".strip() or "No Name"
            n_name = f"{c.get('new_first_name', '')} {c.get('new_last_name', '')}".strip() or "No Name"
            o_u = f"@{c.get('old_username')}" if c.get("old_username") else "None"
            n_u = f"@{c.get('new_username')}" if c.get("new_username") else "None"
            text += f"{idx}. `{c.get('changed_at')}`: {o_name} ({o_u}) ➔ {n_name} ({n_u})\n"
    else:
        text += "\n🔄 **Name Change History:** Koi badlaav nahi hua."

    await update.message.reply_text(text, parse_mode="Markdown")

async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Top 10 active chatters leaderboard."""
    if not await is_allowed(update):
        return

    top_list = database.get_top_members(limit=10)
    if not top_list:
        await update.message.reply_text("ℹ️ Abhi tak group me kisi member ne message nahi kiya hai.")
        return

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    text = "🏆 **Top Active Members Leaderboard:**\n\n"
    for idx, m in enumerate(top_list):
        medal = medals[idx] if idx < len(medals) else f"{idx+1}."
        full_name = f"{m.get('first_name', '')} {m.get('last_name', '')}".strip() or "No Name"
        uname = f"(@{m.get('username')})" if m.get("username") else ""
        cnt = m.get("message_count", 0)
        text += f"{medal} **{full_name}** {uname}\n    💬 **Messages:** {cnt} | 🆔 `{m.get('_id')}`\n\n"

    await update.message.reply_text(text, parse_mode="Markdown")

async def inactive_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pichle 15 ya X din se silent members ki list."""
    if not await is_allowed(update):
        return

    days = 15
    if context.args and context.args[0].isdigit():
        days = int(context.args[0])

    inactives = database.get_inactive_members(days=days, limit=20)
    if not inactives:
        await update.message.reply_text(f"👏 Pichle {days} din me sabhi active members ne koi na koi message kiya hai!")
        return

    text = f"💤 **Inactive / Silent Members (Pichle {days} Din Se):**\n"
    text += "Yeh members group me hain lekin koi baat nahi kar rahe:\n\n"
    for idx, m in enumerate(inactives, 1):
        full_name = f"{m.get('first_name', '')} {m.get('last_name', '')}".strip() or "No Name"
        uname = f"(@{m.get('username')})" if m.get("username") else ""
        last_seen = m.get("last_message_at") or "Kabhi nahi (0 messages)"
        text += f"{idx}. **{full_name}** {uname}\n    🆔 `{m.get('_id')}` | 🕒 Last: `{last_seen}`\n\n"

    await update.message.reply_text(text, parse_mode="Markdown")

async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Aaj ki poori live report summary."""
    if not await is_allowed(update):
        return

    s = database.get_daily_summary_stats()
    text = (
        f"📈 **Aaj ki Report ({s['date']}):**\n\n"
        f"🟢 **Naye Joins:** {s['joins']}\n"
        f"🔴 **Left Members:** {s['left']}\n"
        f"🔄 **Name Changes:** {s['name_changes']}\n"
        f"👥 **Total Active Members:** {s['active_total']}\n"
        f"📁 **Total Tracked in DB:** {s['total_members']}\n"
    )
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

LINK_REGEX = re.compile(
    r'(https?://|www\.|t\.me/|telegram\.me/|bit\.ly/|[a-zA-Z0-9-]+\.(com|in|org|net|xyz|me|io|info|ru|co|biz|online|store|vip))',
    re.IGNORECASE
)


def has_link(message) -> bool:
    """Message ya caption me link detect karta hai."""
    text = f"{message.text or ''} {message.caption or ''}"
    if LINK_REGEX.search(text):
        return True
    entities = list(message.entities or []) + list(message.caption_entities or [])
    for e in entities:
        if e.type in [MessageEntityType.URL, MessageEntityType.TEXT_LINK]:
            return True
    return False

def is_forwarded(message) -> bool:
    """Dusre channel/group se forwarded message detect karta hai."""
    return bool(
        message.forward_origin
        or message.forward_date
        or message.forward_from
        or message.forward_from_chat
    )

async def send_auto_delete_warning(chat, text: str, delay: int = 10):
    """Warning bhejkar 10 second baad auto-delete karta hai."""
    try:
        warn_msg = await chat.send_message(text=text, parse_mode="Markdown")
        async def _delete():
            await asyncio.sleep(delay)
            try:
                await warn_msg.delete()
            except Exception:
                pass
        asyncio.create_task(_delete())
    except Exception as e:
        logger.error(f"Warning send error: {e}")

async def clean_service_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telegram ke faltu system messages ('Rahul joined', 'Priya left') ko turant delete karta hai."""
    msg = update.message
    if not msg:
        return
    chat = update.effective_chat
    if not chat or chat.type not in ["group", "supergroup"]:
        return
    if ALLOWED_GROUP_ID and str(chat.id) != str(ALLOWED_GROUP_ID):
        return

    try:
        await msg.delete()
        logger.info("Service message (join/leave/pin) auto-deleted.")
    except Exception as e:
        logger.warning(f"Could not delete service message: {e}")

async def check_name_on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Har message aane par Security (Anti-Link, Anti-Forward) aur Name Change check karein."""
    msg = update.message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat:
        return

    # 1. Name change tracking
    await handle_name_change(chat, user)

    # Security sirf group me apply hogi
    if chat.type not in ["group", "supergroup"]:
        return
    if ALLOWED_GROUP_ID and str(chat.id) != str(ALLOWED_GROUP_ID):
        return

    # Bots ko chhod dein
    if user.is_bot:
        return

    # Message count & last seen record karein (Top chatters & Inactivity tracking)
    database.record_message(user.id)

    # Sirf Main Owner (Aap) ko link bhejna allow hai. Dusre admins aur members sabka link delete hoga!
    if is_allowed_to_post_links(user.id):
        return

    # 2. Anti-Link (Link Block karna)
    if has_link(msg):
        try:
            await msg.delete()
            logger.info(f"Link deleted from user {user.id}")
            user_mention = f"[{user.first_name}](tg://user?id={user.id})"
            await send_auto_delete_warning(
                chat,
                f"⚠️ {user_mention}, is group me kisi bhi tarah ka **Link** bhejna sakht mana hai!",
                delay=10
            )
            return
        except Exception as e:
            logger.error(f"Error deleting link: {e}")

    # 3. Anti-Forward (Dusre channel/group se forward rokna)
    if is_forwarded(msg):
        try:
            await msg.delete()
            logger.info(f"Forwarded message deleted from user {user.id}")
            user_mention = f"[{user.first_name}](tg://user?id={user.id})"
            await send_auto_delete_warning(
                chat,
                f"⚠️ {user_mention}, dusre channel ya group ke messages **Forward** karna mana hai!",
                delay=10
            )
            return
        except Exception as e:
            logger.error(f"Error deleting forwarded message: {e}")

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

async def daily_report_worker(bot):
    """Har raat theek 9:00 PM IST par seedha 6416451659 ke DM me automatic report bhejta hai."""
    logger.info("Daily 9:00 PM IST Scheduler loop started.")
    while True:
        try:
            # Current time in IST (UTC + 5:30)
            utc_now = datetime.now(timezone.utc)
            ist_now = utc_now + timedelta(hours=5, minutes=30)

            # Target: Aaj raat 21:00:00 (9:00 PM) IST
            target = ist_now.replace(hour=21, minute=0, second=0, microsecond=0)
            if ist_now >= target:
                target += timedelta(days=1)

            wait_secs = (target - ist_now).total_seconds()
            logger.info(f"Next 9 PM IST daily report scheduled in {int(wait_secs)} seconds ({target.strftime('%Y-%m-%d %H:%M:%S')} IST)")
            await asyncio.sleep(wait_secs)

            # Send report to SUPER_OWNER_ID (6416451659)
            s = database.get_daily_summary_stats()
            report_text = (
                f"⏰ **9:00 PM Daily Automated Summary Report:**\n\n"
                f"📅 **Date:** {s['date']}\n"
                f"🟢 **Naye Joins:** {s['joins']}\n"
                f"🔴 **Left Members:** {s['left']}\n"
                f"🔄 **Name Changes:** {s['name_changes']}\n"
                f"👥 **Total Active Members:** {s['active_total']}\n"
                f"📁 **Total Tracked in DB:** {s['total_members']}\n\n"
                "Full Excel report download karne ke liye `/export` bhejein."
            )
            await bot.send_message(
                chat_id=int(SUPER_OWNER_ID),
                text=report_text,
                parse_mode="Markdown"
            )
            logger.info("9:00 PM Daily report sent successfully to Owner!")
            # 70 seconds sleep taaki ek hi minute me do baar trigger na ho
            await asyncio.sleep(70)
        except Exception as e:
            logger.error(f"Daily report scheduler error: {e}")
            await asyncio.sleep(60)

async def post_init(application):
    """Bot start hone par 9 PM scheduler task start karta hai."""
    asyncio.create_task(daily_report_worker(application.bot))

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
        .post_init(post_init)
        .build()
    )

    # Handlers register karein
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("whois", whois_cmd))
    application.add_handler(CommandHandler("top", top_cmd))
    application.add_handler(CommandHandler("inactive", inactive_cmd))
    application.add_handler(CommandHandler("today", today_cmd))
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

    # Telegram ke faltu system messages (joined, left, pinned) ko auto-delete karna
    application.add_handler(
        MessageHandler(filters.StatusUpdate.ALL, clean_service_messages)
    )

    # Har message par Name Change & Anti-Spam Security check karne ke liye listener
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
