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
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus, MessageEntityType
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
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

# 3. Bad Words / Gaali Filter - Yeh words milenge toh turant delete + auto-warn
BAD_WORDS = {
    # Hindi gaali words
    "madarchod", "bhenchod", "chutiya", "bhosdike", "bhosdiwale", "bhosdiwala",
    "gandu", "lodu", "laude", "lawde", "lode", "randi", "harami", "haramkhor",
    "kutte", "kuttiya", "suar", "saala", "saali", "ullu", "gadha",
    "kamina", "kameena", "kameeni", "kamini", "bhosda", "chut", "lund",
    "tatte", "jhant", "gaand", "gand", "mc", "bc", "bsdk", "bkl",
    "mkc", "mkb", "betichod", "behenchod", "teri maa", "teri ma",
    "madar", "maderchod", "bhen ke", "bhen ka",
    # English bad words
    "fuck", "shit", "bitch", "asshole", "bastard", "dick", "pussy",
    "motherfucker", "wtf", "stfu", "idiot", "stupid",
}

# Pending cleanup confirmations store
pending_cleanup = {}

# ============================================================
# 🔧 GROUP PERMISSION STATES
# Yeh variables track karte hain ki kaun si permission ON/OFF hai.
# Quiz/Night Mode SIRF can_send_messages toggle karta hai — baaki sab yahan se aata hai.
# Owner dashboard se manually kisi bhi permission ko on/off kar sakti hain.
# ============================================================
perm_photos   = False  # Photos — Default OFF, owner manually ON kare
perm_videos   = False  # Videos — Default OFF, owner manually ON kare
perm_voice    = False  # Voice/Audio — Default OFF, owner manually ON kare
perm_polls    = False  # Polls — Default OFF, owner manually ON kare
perm_stickers = False  # Stickers/GIFs — Default OFF, owner manually ON kare
perm_invite   = False  # Invite — Default OFF, owner manually ON kare
perm_docs     = False  # Files/Docs — Default OFF, owner manually ON kare

def build_permissions(send_messages: bool = True) -> ChatPermissions:
    """
    Current perm_* state ke hisaab se ChatPermissions object banata hai.
    send_messages=True  → sirf message ON (unlock)
    send_messages=False → sirf message OFF (lock)
    Baaki sab permissions perm_* variables ke hisaab se set hoti hain.
    """
    return ChatPermissions(
        can_send_messages=send_messages,
        can_send_audios=perm_voice,
        can_send_documents=perm_docs,
        can_send_photos=perm_photos,
        can_send_videos=perm_videos,
        can_send_video_notes=perm_videos,
        can_send_voice_notes=perm_voice,
        can_send_polls=perm_polls,
        can_send_other_messages=perm_stickers,
        can_add_web_page_previews=True,
        can_change_info=False,
        can_invite_users=perm_invite,
        can_pin_messages=False,
    )

def contains_bad_word(text: str) -> bool:
    """Check karta hai ki message me koi gaali/bad word hai ya nahi."""
    if not text:
        return False
    text_lower = text.lower()
    cleaned = re.sub(r'[^a-zA-Z\u0900-\u097F\s]', '', text_lower)
    words = cleaned.split()
    for word in words:
        if word in BAD_WORDS:
            return True
    for bw in BAD_WORDS:
        if bw in text_lower:
            return True
    return False

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
    Sirf aur sirf 6416451659 (Aap) commands chala sakte hain!
    """
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return False

    if not is_super_owner(user.id):
        return False

    if chat.type in ["group", "supergroup"]:
        if ALLOWED_GROUP_ID and str(chat.id) != str(ALLOWED_GROUP_ID):
            try:
                await chat.leave()
            except Exception:
                pass
            return False

    return True

# ========================================================
# 🌙 SMART NIGHT MODE & 🎯 QUIZ CHAT LOCK — State Variables
# ========================================================
is_night_mode_active    = False
is_chat_manually_locked = False
night_mode_message_id   = None
quiz_lock_message_id    = None

def get_control_panel_keyboard() -> InlineKeyboardMarkup:
    """Owner ke liye button control panel keyboard — ab individual permission buttons ke saath."""
    quiz_btn_text  = "🟢 Quiz Mode OFF (Unlock)" if is_chat_manually_locked else "🔴 Quiz Mode ON (Lock)"
    night_btn_text = "☀️ Night Mode OFF" if is_night_mode_active else "🌙 Night Mode ON"

    # Individual permission button labels (ON/OFF state reflect karte hain)
    ph_txt  = "🖼 Photo ✅"   if perm_photos   else "🖼 Photo ❌"
    vid_txt = "🎥 Video ✅"   if perm_videos   else "🎥 Video ❌"
    vo_txt  = "🎤 Voice ✅"   if perm_voice    else "🎤 Voice ❌"
    pl_txt  = "📊 Poll ✅"    if perm_polls    else "📊 Poll ❌"
    st_txt  = "😀 Sticker ✅" if perm_stickers else "😀 Sticker ❌"
    inv_txt = "🔗 Invite ✅"  if perm_invite   else "🔗 Invite ❌"
    doc_txt = "📄 File ✅"    if perm_docs     else "📄 File ❌"

    keyboard = [
        [
            InlineKeyboardButton("📊 Live Stats", callback_data="btn_stats"),
            InlineKeyboardButton("👥 Member List", callback_data="btn_members"),
        ],
        [
            InlineKeyboardButton("🏆 Top Chatters", callback_data="btn_top"),
            InlineKeyboardButton("💤 Silent Members", callback_data="btn_inactive"),
        ],
        [
            InlineKeyboardButton("📈 Aaj Ki Report", callback_data="btn_today"),
            InlineKeyboardButton("📅 Weekly Report", callback_data="btn_weekly"),
        ],
        [
            InlineKeyboardButton("🔄 Name Changes", callback_data="btn_names"),
            InlineKeyboardButton("🔴 Recent Left", callback_data="btn_left"),
        ],
        [
            InlineKeyboardButton(quiz_btn_text, callback_data="btn_toggle_quiz"),
        ],
        [
            InlineKeyboardButton(night_btn_text, callback_data="btn_toggle_night"),
        ],
        # ── Individual Permission Toggles ──────────────────────────
        [InlineKeyboardButton("⚙️ ─── GROUP PERMISSIONS ─── ⚙️", callback_data="btn_perm_noop")],
        [
            InlineKeyboardButton(ph_txt,  callback_data="btn_perm_photos"),
            InlineKeyboardButton(vid_txt, callback_data="btn_perm_videos"),
        ],
        [
            InlineKeyboardButton(vo_txt,  callback_data="btn_perm_voice"),
            InlineKeyboardButton(pl_txt,  callback_data="btn_perm_polls"),
        ],
        [
            InlineKeyboardButton(st_txt,  callback_data="btn_perm_stickers"),
            InlineKeyboardButton(inv_txt, callback_data="btn_perm_invite"),
        ],
        [
            InlineKeyboardButton(doc_txt, callback_data="btn_perm_docs"),
        ],
        # ───────────────────────────────────────────────────────────
        [
            InlineKeyboardButton("👻 Ghost Member Cleanup", callback_data="btn_ghost_clean"),
            InlineKeyboardButton("📥 Download Excel", callback_data="btn_export"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="btn_refresh"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command instructions aur interactive buttons panel ke sath."""
    if not await is_allowed(update):
        return

    quiz_status  = "`ACTIVE (Chat Locked)` 🔴" if is_chat_manually_locked else "`INACTIVE (Chat Open)` 🟢"
    night_status = "`ACTIVE (Group Muted)` 🔒" if is_night_mode_active    else "`INACTIVE (Group Open)` 🔓"

    panel_text = (
        "👑 **SUPER OWNER CONTROL DASHBOARD** 👑\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Namaste Malik! Aap apne group ka poora hisaab-kitaab aur security "
        "seedha neeche diye gaye buttons se one-click me control kar sakte hain:\n\n"
        f"🎯 **Quiz Mode Status:** {quiz_status}\n"
        f"🌙 **Night Mode Status:** {night_status}\n\n"
        "👇 **Neeche kisi bhi button par click karein:**"
    )
    await update.message.reply_text(
        panel_text,
        reply_markup=get_control_panel_keyboard(),
        parse_mode="Markdown"
    )

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed(update):
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
    if not await is_allowed(update):
        return
    recent = database.get_recent_left(limit=10)
    if not recent:
        await update.message.reply_text("ℹ️ Abhi tak kisi bhi member ne group nahi chhoda hai.")
        return
    text = "🔴 **Recently Group Chhodne Wale Members:**\n\n"
    for idx, row in enumerate(recent, 1):
        uid, fname, lname, uname, left_at = row
        name         = f"{fname or ''} {lname or ''}".strip() or "Unnamed"
        username_str = f"(@{uname})" if uname else "(No Username)"
        left_time    = database.format_datetime_ist(left_at)
        text += f"{idx}. **{name}** {username_str}\n   🕒 Left At: `{left_time}`\n   🆔 ID: `{uid}`\n\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def whois_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    data  = database.get_user_history(query)
    if not data:
        await update.message.reply_text(f"❌ User `{query}` database me nahi mila.", parse_mode="Markdown")
        return
    status_str = "🟢 Active (Group me hai)" if data["status"] == "active" else f"🔴 Left (Left at: `{data['left_at']}`)"
    uname_str  = f"@{data['username']}" if data["username"] else "(No Username)"
    full_name  = f"{data['first_name']} {data['last_name']}".strip() or "No Name"
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
            o_u    = f"@{c.get('old_username')}" if c.get("old_username") else "None"
            n_u    = f"@{c.get('new_username')}" if c.get("new_username") else "None"
            text  += f"{idx}. `{c.get('changed_at')}`: {o_name} ({o_u}) ➔ {n_name} ({n_u})\n"
    else:
        text += "\n🔄 **Name Change History:** Koi badlaav nahi hua."
    await update.message.reply_text(text, parse_mode="Markdown")

async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed(update):
        return
    top_list = database.get_top_members(limit=10)
    if not top_list:
        await update.message.reply_text("ℹ️ Abhi tak group me kisi member ne message nahi kiya hai.")
        return
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    text = "🏆 **Top Active Members Leaderboard:**\n\n"
    for idx, m in enumerate(top_list):
        medal     = medals[idx] if idx < len(medals) else f"{idx+1}."
        full_name = f"{m.get('first_name', '')} {m.get('last_name', '')}".strip() or "No Name"
        uname     = f"(@{m.get('username')})" if m.get("username") else ""
        cnt       = m.get("message_count", 0)
        text += f"{medal} **{full_name}** {uname}\n    💬 **Messages:** {cnt} | 🆔 `{m.get('_id')}`\n\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def inactive_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        uname     = f"(@{m.get('username')})" if m.get("username") else ""
        last_seen = m.get("last_message_at") or "Kabhi nahi (0 messages)"
        text += f"{idx}. **{full_name}** {uname}\n    🆔 `{m.get('_id')}` | 🕒 Last: `{last_seen}`\n\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def weekly_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed(update):
        return
    w = database.get_weekly_summary_stats()
    top_text = ""
    if w.get("top_5"):
        top_text = "\n\n🏆 **Top 5 Active Chatters of the Week:**\n"
        for i, m in enumerate(w["top_5"], 1):
            top_text += f"{i}. {m.get('first_name', 'User')} — `{m.get('message_count', 0)} msgs`\n"
    text = (
        f"📅 **Weekly Performance Report ({w['from_date']} se {w['to_date']}):**\n\n"
        f"🟢 **Hafte ke Naye Joins:** {w['joins']}\n"
        f"🔴 **Hafte ke Left Members:** {w['left']}\n"
        f"🔄 **Name Changes:** {w['name_changes']}\n"
        f"👥 **Total Active Members:** {w['active_total']}\n"
        f"📁 **Total Tracked in DB:** {w['total_members']}"
        f"{top_text}\n\n"
        "Full Excel download karne ke liye `/export` bhejein."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def cleanup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed(update):
        return
    chat         = update.effective_chat
    target_group = ALLOWED_GROUP_ID or (chat.id if chat.type in ["group", "supergroup"] else None)
    days = 30
    if context.args:
        try:
            if context.args[0].lower() == "confirm":
                if str(update.effective_user.id) not in pending_cleanup:
                    await update.message.reply_text("❌ Koi pending cleanup list nahi hai. Pehle `/cleanup` chalayein.")
                    return
                ghost_list   = pending_cleanup.pop(str(update.effective_user.id))
                if not target_group:
                    await update.message.reply_text("❌ ALLOWED_GROUP_ID set nahi hai.")
                    return
                progress_msg = await update.message.reply_text(f"⏳ Cleanup shuru ho raha hai... Total {len(ghost_list)} ghost members.")
                kicked = 0
                failed = 0
                for m in ghost_list:
                    uid = m.get("user_id") or m.get("_id")
                    if str(uid) in LINK_ALLOWED_IDS or is_super_owner(uid):
                        continue
                    try:
                        await context.bot.ban_chat_member(chat_id=int(target_group), user_id=int(uid))
                        await context.bot.unban_chat_member(chat_id=int(target_group), user_id=int(uid))
                        kicked += 1
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        failed += 1
                        logger.warning(f"Could not kick ghost member {uid}: {e}")
                await progress_msg.edit_text(
                    f"✅ **Ghost Member Cleanup Safal!**\n\n"
                    f"👢 **Kicked Members:** {kicked}\n"
                    f"⚠️ **Failed / Already Left:** {failed}"
                )
                return
            else:
                days = int(context.args[0])
        except ValueError:
            pass
    ghosts = database.get_ghost_members(days=days, limit=30)
    if not ghosts:
        await update.message.reply_text(f"🎉 Pichle {days} din se koi bhi Ghost/Silent member nahi mila! Sab active hain.")
        return
    pending_cleanup[str(update.effective_user.id)] = ghosts
    text  = f"👻 **Ghost / Silent Members (Pichle {days} Din Se):**\n"
    text += f"Total {len(ghosts)} members mile jo itne din se chup hain:\n\n"
    for idx, m in enumerate(ghosts[:15], 1):
        name  = f"{m.get('first_name', '')} {m.get('last_name', '')}".strip() or "No Name"
        uname = f"(@{m.get('username')})" if m.get("username") else ""
        text += f"{idx}. **{name}** {uname} — ID: `{m.get('user_id') or m.get('_id')}`\n"
    if len(ghosts) > 15:
        text += f"\n...aur {len(ghosts) - 15} aur members.\n"
    text += (
        "\n⚠️ **Kya aap in sabko group se kick/remove karna chahte hain?**\n"
        "👉 Confirm karne ke liye type karein: `/cleanup confirm`\n"
        "*(Note: Aap aur aapke partner whitelist me surakshit rahenge)*"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /ban command: Reply karke ya /ban @username ya /ban <user_id>
    Bot usko group se ban karega aur 'GET OUT 🪟🧹' bolega — 20s me auto-delete.
    """
    if not await is_allowed(update):
        return
    msg          = update.effective_message
    chat         = update.effective_chat
    target_group = ALLOWED_GROUP_ID or (chat.id if chat.type in ["group", "supergroup"] else None)
    if not target_group:
        await msg.reply_text("❌ ALLOWED_GROUP_ID set nahi hai.")
        return
    target_user_id = None
    target_name    = "User"
    if msg.reply_to_message and msg.reply_to_message.from_user:
        target_user    = msg.reply_to_message.from_user
        target_user_id = target_user.id
        target_name    = target_user.first_name
    elif context.args:
        arg = context.args[0].strip()
        if arg.isdigit():
            target_user_id = int(arg)
            target_name    = f"ID {arg}"
        elif arg.startswith("@"):
            uname_clean = arg.lstrip("@")
            hist = database.get_user_history(uname_clean)
            if hist and hist.get("user_id"):
                target_user_id = int(hist["user_id"])
                target_name    = hist.get("first_name") or arg
            else:
                await msg.reply_text(f"❌ User `{arg}` database me nahi mila.", parse_mode="Markdown")
                return
        else:
            await msg.reply_text("ℹ️ Format: `/ban @username` ya `/ban <user_id>`", parse_mode="Markdown")
            return
    else:
        await msg.reply_text("ℹ️ Kripya kisi ke message par reply karein ya type karein:\n`/ban @username` ya `/ban <user_id>`", parse_mode="Markdown")
        return
    if is_super_owner(target_user_id) or str(target_user_id) in LINK_ALLOWED_IDS:
        await msg.reply_text("⛔ Yeh Owner / Partner hain! Inhe ban nahi kiya ja sakta.")
        return
    try:
        await context.bot.ban_chat_member(chat_id=int(target_group), user_id=target_user_id)
        logger.info(f"User {target_user_id} ({target_name}) banned from chat {target_group} by Owner.")
        database.member_left(user_id=target_user_id)
        user_mention  = f"[{target_name}](tg://user?id={target_user_id})"
        ban_msg_text  = f"🚪 {user_mention}\n\n**GET OUT 🪟🧹**"
        sent_ban_msg  = await context.bot.send_message(
            chat_id=int(target_group),
            text=ban_msg_text,
            parse_mode="Markdown"
        )
        async def _auto_del_ban():
            await asyncio.sleep(20)
            try:
                await sent_ban_msg.delete()
                if chat.type in ["group", "supergroup"]:
                    await msg.delete()
            except Exception:
                pass
        asyncio.create_task(_auto_del_ban())
    except Exception as e:
        logger.error(f"Error banning user {target_user_id}: {e}")
        await msg.reply_text(f"❌ Ban karne me error aaya: {e}")

async def members_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed(update):
        return
    user          = update.effective_user
    chat          = update.effective_chat
    target_chat_id = user.id
    if chat.type in ["group", "supergroup"]:
        await update.message.reply_text("🔒 Group me privacy ke liye poori list aapke Personal DM me bhej di gayi hai!")
    members = database.get_all_members()
    if not members:
        await context.bot.send_message(chat_id=target_chat_id, text="ℹ️ Abhi tak database me koi member record nahi hua hai.")
        return
    total = len(members)
    await context.bot.send_message(chat_id=target_chat_id, text=f"📋 **Total Members List ({total}):**\nHar member ki poori details neeche hain:")
    chunk_size = 15
    for i in range(0, total, chunk_size):
        chunk      = members[i:i + chunk_size]
        text_lines = []
        for idx, m in enumerate(chunk, start=i + 1):
            fname      = m.get("first_name") or ""
            lname      = m.get("last_name") or ""
            full_name  = f"{fname} {lname}".strip() or "No Name"
            uname      = f"@{m.get('username')}" if m.get("username") else "None"
            uid        = m.get("_id") or m.get("user_id")
            status     = m.get("status", "active")
            status_icon = "🟢 Active" if status == "active" else "🔴 Left"
            joined     = database.format_datetime_ist(m.get("joined_at", "N/A"))
            left_raw   = m.get("left_at")
            left_str   = f"\n   🚪 **Left At:** `{database.format_datetime_ist(left_raw)}`" if left_raw else ""
            entry = (
                f"**{idx}. {full_name}**\n"
                f"   🆔 **User ID:** `{uid}`\n"
                f"   👤 **Username:** {uname}\n"
                f"   📌 **Status:** {status_icon}\n"
                f"   📅 **Joined:** `{joined}`{left_str}"
            )
            text_lines.append(entry)
        await context.bot.send_message(chat_id=target_chat_id, text="\n\n".join(text_lines), parse_mode="Markdown")

async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed(update):
        return
    user           = update.effective_user
    chat           = update.effective_chat
    target_chat_id = user.id
    if chat.type in ["group", "supergroup"]:
        await update.message.reply_text("🔒 Excel report aapke Personal DM me bhej di gayi hai!")
    status_msg = await context.bot.send_message(chat_id=target_chat_id, text="⏳ Excel report taiyaar ho rahi hai, kripya intezar karein...")
    try:
        filepath = database.export_to_excel("telegram_members_live_report.xlsx")
        with open(filepath, "rb") as f:
            await context.bot.send_document(
                chat_id=target_chat_id,
                document=f,
                filename="telegram_members_live_report.xlsx",
                caption="📊 **Members Full Report (Excel File)**\nIsme sabhi members ka User ID, Naam, Username aur Dates hain."
            )
        await status_msg.delete()
    except Exception as e:
        logger.error(f"Excel export error: {e}")
        await status_msg.edit_text(f"❌ File export karne me error aaya: {e}")

async def namechanges_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed(update):
        return
    changes = database.get_recent_name_changes(limit=15)
    if not changes:
        await update.message.reply_text("ℹ️ Abhi tak kisi ne bhi apna naam nahi badla hai.")
        return
    text = "🔄 **Recent Name Changes:**\n\n"
    for idx, c in enumerate(changes, 1):
        old_name   = f"{c.get('old_first_name', '')} {c.get('old_last_name', '')}".strip() or "No Name"
        new_name   = f"{c.get('new_first_name', '')} {c.get('new_last_name', '')}".strip() or "No Name"
        old_uname  = f"@{c.get('old_username')}" if c.get("old_username") else "None"
        new_uname  = f"@{c.get('new_username')}" if c.get("new_username") else "None"
        changed_at = database.format_datetime_ist(c.get("changed_at", "N/A"))
        uid        = c.get("user_id")
        entry  = f"{idx}. 🆔 `{uid}`\n"
        entry += f"   📛 **Purana Naam:** {old_name} ({old_uname})\n"
        entry += f"   ✨ **Naya Naam:** {new_name} ({new_uname})\n"
        entry += f"   🕒 **Changed At:** `{changed_at}`\n\n"
        text  += entry
    await update.message.reply_text(text, parse_mode="Markdown")

# ─────────────────────────────────────────────────────────
# Helper: dashboard text banata hai (refresh ke liye reuse)
# ─────────────────────────────────────────────────────────
def _dashboard_text() -> str:
    quiz_status  = "`ACTIVE (Chat Locked)` 🔴" if is_chat_manually_locked else "`INACTIVE (Chat Open)` 🟢"
    night_status = "`ACTIVE (Group Muted)` 🔒" if is_night_mode_active    else "`INACTIVE (Group Open)` 🔓"
    return (
        "👑 **SUPER OWNER CONTROL DASHBOARD** 👑\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Namaste Malik! Aap apne group ka poora hisaab-kitaab aur security "
        "seedha neeche diye gaye buttons se one-click me control kar sakte hain:\n\n"
        f"🎯 **Quiz Mode Status:** {quiz_status}\n"
        f"🌙 **Night Mode Status:** {night_status}\n"
        f"🕒 **Last Updated:** `{datetime.now().strftime('%H:%M:%S')}`\n\n"
        "👇 **Neeche kisi bhi button par click karein:**"
    )

async def panel_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Button click hone par actions handle karta hai."""
    global is_chat_manually_locked, quiz_lock_message_id, is_night_mode_active, night_mode_message_id
    global perm_photos, perm_videos, perm_voice, perm_polls, perm_stickers, perm_invite, perm_docs

    query = update.callback_query
    if not query:
        return

    # Sirf Super Owner click kar sakte hain
    if not is_super_owner(query.from_user.id):
        await query.answer("⛔ Aapko is control panel ko use karne ki permission nahi hai!", show_alert=True)
        return

    data         = query.data
    target_group = ALLOWED_GROUP_ID

    # 1. Refresh Button
    if data == "btn_refresh":
        await query.answer("🔄 Dashboard refresh ho raha hai...")
        try:
            await query.edit_message_text(_dashboard_text(), reply_markup=get_control_panel_keyboard(), parse_mode="Markdown")
        except Exception:
            pass
        return

    # 2. Live Stats
    elif data == "btn_stats":
        await query.answer()
        stats = database.get_stats()
        text = (
            "📊 **Group Member Live Statistics:**\n\n"
            f"🟢 **Active Members:** {stats['active']}\n"
            f"🔴 **Left Members:** {stats['left']}\n"
            f"📁 **Total Tracked in DB:** {stats['total']}\n"
        )
        await query.message.reply_text(text, parse_mode="Markdown")

    # 3. Member List
    elif data == "btn_members":
        await query.answer("📋 Member list DM me bheji ja rahi hai...")
        members = database.get_all_members()
        if not members:
            await context.bot.send_message(chat_id=query.from_user.id, text="ℹ️ Abhi tak database me koi member record nahi hua hai.")
            return
        total = len(members)
        await context.bot.send_message(chat_id=query.from_user.id, text=f"📋 **Total Members List ({total}):**")
        chunk_size = 15
        for i in range(0, total, chunk_size):
            chunk      = members[i:i + chunk_size]
            text_lines = []
            for idx, m in enumerate(chunk, start=i + 1):
                full_name   = f"{m.get('first_name','') or ''} {m.get('last_name','') or ''}".strip() or "No Name"
                uname       = f"@{m.get('username')}" if m.get("username") else "None"
                uid         = m.get("_id") or m.get("user_id")
                status_icon = "🟢 Active" if m.get("status","active") == "active" else "🔴 Left"
                joined      = database.format_datetime_ist(m.get("joined_at", "N/A"))
                left_raw    = m.get("left_at")
                left_str    = f" | 🚪 Left: `{database.format_datetime_ist(left_raw)}`" if left_raw else ""
                text_lines.append(f"**{idx}. {full_name}** ({uname})\n   🆔 `{uid}` | {status_icon} | 📅 `{joined}`{left_str}")
            await context.bot.send_message(chat_id=query.from_user.id, text="\n\n".join(text_lines), parse_mode="Markdown")
            await asyncio.sleep(0.3)

    # 4. Top Chatters
    elif data == "btn_top":
        await query.answer()
        top_list = database.get_top_members(limit=10)
        if not top_list:
            await query.message.reply_text("ℹ️ Abhi tak group me kisi member ne message nahi kiya hai.")
            return
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        text = "🏆 **Top Active Members Leaderboard:**\n\n"
        for idx, m in enumerate(top_list):
            medal     = medals[idx] if idx < len(medals) else f"{idx+1}."
            full_name = f"{m.get('first_name','') or ''} {m.get('last_name','') or ''}".strip() or "No Name"
            uname     = f"(@{m.get('username')})" if m.get("username") else ""
            cnt       = m.get("message_count", 0)
            text += f"{medal} **{full_name}** {uname}\n    💬 **Messages:** {cnt} | 🆔 `{m.get('_id')}`\n\n"
        await query.message.reply_text(text, parse_mode="Markdown")

    # 5. Inactive Members
    elif data == "btn_inactive":
        await query.answer()
        inactives = database.get_inactive_members(days=15, limit=20)
        if not inactives:
            await query.message.reply_text("👏 Pichle 15 din me sabhi active members ne koi na koi message kiya hai!")
            return
        text = "💤 **Inactive / Silent Members (Pichle 15 Din Se):**\n\n"
        for idx, m in enumerate(inactives, 1):
            full_name = f"{m.get('first_name','') or ''} {m.get('last_name','') or ''}".strip() or "No Name"
            uname     = f"(@{m.get('username')})" if m.get("username") else ""
            last_seen = m.get("last_message_at") or "Kabhi nahi"
            text += f"{idx}. **{full_name}** {uname}\n    🆔 `{m.get('_id')}` | 🕒 Last: `{last_seen}`\n\n"
        await query.message.reply_text(text, parse_mode="Markdown")

    # 6. Aaj Ki Report
    elif data == "btn_today":
        await query.answer()
        s = database.get_daily_summary_stats()
        text = (
            f"📈 **Aaj ki Report ({s['date']}):**\n\n"
            f"🟢 **Naye Joins:** {s['joins']}\n"
            f"🔴 **Left Members:** {s['left']}\n"
            f"🔄 **Name Changes:** {s['name_changes']}\n"
            f"👥 **Total Active Members:** {s['active_total']}\n"
            f"📁 **Total Tracked in DB:** {s['total_members']}\n"
        )
        await query.message.reply_text(text, parse_mode="Markdown")

    # 7. Weekly Report
    elif data == "btn_weekly":
        await query.answer()
        w = database.get_weekly_summary_stats()
        top_text = ""
        if w.get("top_5"):
            top_text = "\n\n🏆 **Top 5 Active Chatters of the Week:**\n"
            for i, m in enumerate(w["top_5"], 1):
                top_text += f"{i}. {m.get('first_name', 'User')} — `{m.get('message_count', 0)} msgs`\n"
        text = (
            f"📅 **Weekly Report ({w['from_date']} se {w['to_date']}):**\n\n"
            f"🟢 **Hafte ke Naye Joins:** {w['joins']}\n"
            f"🔴 **Hafte ke Left Members:** {w['left']}\n"
            f"🔄 **Name Changes:** {w['name_changes']}\n"
            f"👥 **Total Active Members:** {w['active_total']}\n"
            f"📁 **Total Tracked in DB:** {w['total_members']}"
            f"{top_text}\n"
        )
        await query.message.reply_text(text, parse_mode="Markdown")

    # 8. Name Changes
    elif data == "btn_names":
        await query.answer()
        changes = database.get_recent_name_changes(limit=10)
        if not changes:
            await query.message.reply_text("ℹ️ Abhi tak kisi ne bhi apna naam nahi badla hai.")
            return
        text = "🔄 **Recent Name Changes:**\n\n"
        for idx, c in enumerate(changes, 1):
            old_name  = f"{c.get('old_first_name','') or ''} {c.get('old_last_name','') or ''}".strip() or "No Name"
            new_name  = f"{c.get('new_first_name','') or ''} {c.get('new_last_name','') or ''}".strip() or "No Name"
            old_uname = f"@{c.get('old_username')}" if c.get("old_username") else "None"
            new_uname = f"@{c.get('new_username')}" if c.get("new_username") else "None"
            changed_at = database.format_datetime_ist(c.get("changed_at", "N/A"))
            uid = c.get("user_id")
            text += f"{idx}. 🆔 `{uid}`\n   📛 {old_name} ({old_uname}) ➔ {new_name} ({new_uname})\n   🕒 `{changed_at}`\n\n"
        await query.message.reply_text(text, parse_mode="Markdown")

    # 9. Recent Left
    elif data == "btn_left":
        await query.answer()
        recent = database.get_recent_left(limit=10)
        if not recent:
            await query.message.reply_text("ℹ️ Abhi tak kisi ne group nahi chhoda hai.")
            return
        text = "🔴 **Recently Group Chhodne Wale Members:**\n\n"
        for idx, row in enumerate(recent, 1):
            uid, fname, lname, uname, left_at = row
            name         = f"{fname or ''} {lname or ''}".strip() or "Unnamed"
            username_str = f"(@{uname})" if uname else "(No Username)"
            left_time    = database.format_datetime_ist(left_at)
            text += f"{idx}. **{name}** {username_str}\n   🕒 Left: `{left_time}` | 🆔 `{uid}`\n\n"
        await query.message.reply_text(text, parse_mode="Markdown")

    # 10. Quiz Mode Toggle
    elif data == "btn_toggle_quiz":
        if not target_group:
            await query.answer("❌ ALLOWED_GROUP_ID set nahi hai!", show_alert=True)
            return
        if is_chat_manually_locked:
            # ── QUIZ OFF ── sirf message ON karo, baaki sab same rahega
            is_chat_manually_locked = False
            await unlock_chat(
                context.bot, target_group,
                delete_msg_id=quiz_lock_message_id,
                announce_text="🎉 Quiz समाप्त हो गया है !! ❤️",
                auto_del_delay=30
            )
            quiz_lock_message_id = None
            await query.answer("🟢 Quiz Mode OFF! Sirf Message ON kiya gaya.", show_alert=True)
        else:
            # ── QUIZ ON ── sirf message OFF karo, baaki sab same rahega
            is_chat_manually_locked = True
            announce_text = "📣 Quiz खत्म होने के बाद Message Option ON कर दिया जाएगा !! ❤️"
            success, msg_id = await lock_chat(
                context.bot, target_group,
                announce_text=announce_text,
                partner_allow=True
            )
            quiz_lock_message_id = msg_id
            await query.answer("🔴 Quiz Mode ON! Sirf Message OFF kiya gaya.", show_alert=True)
        try:
            await query.edit_message_text(_dashboard_text(), reply_markup=get_control_panel_keyboard(), parse_mode="Markdown")
        except Exception:
            pass

    # 11. Night Mode Toggle
    elif data == "btn_toggle_night":
        if not target_group:
            await query.answer("❌ ALLOWED_GROUP_ID set nahi hai!", show_alert=True)
            return
        if is_night_mode_active:
            await disable_night_mode(context.bot, target_group, announce=True)
            await query.answer("☀️ Night Mode OFF! Sirf Message ON kiya gaya.", show_alert=True)
        else:
            await enable_night_mode(context.bot, target_group, announce=True)
            await query.answer("🌙 Night Mode ON! Sirf Message OFF kiya gaya.", show_alert=True)
        try:
            await query.edit_message_text(_dashboard_text(), reply_markup=get_control_panel_keyboard(), parse_mode="Markdown")
        except Exception:
            pass

    # 12. Ghost Cleaner
    elif data == "btn_ghost_clean":
        await query.answer()
        ghosts = database.get_ghost_members(days=30, limit=20)
        if not ghosts:
            await query.message.reply_text("🎉 Pichle 30 din me koi bhi ghost member nahi mila! Sab active hain.")
            return
        pending_cleanup[str(query.from_user.id)] = ghosts
        text  = f"👻 **Ghost / Silent Members (Pichle 30 Din Se):**\n"
        text += f"Total {len(ghosts)} members mile jo itne din se chup hain:\n\n"
        for idx, m in enumerate(ghosts[:10], 1):
            name  = f"{m.get('first_name', '') or ''} {m.get('last_name', '') or ''}".strip() or "No Name"
            uname = f"(@{m.get('username')})" if m.get("username") else ""
            text += f"{idx}. **{name}** {uname} — ID: `{m.get('user_id') or m.get('_id')}`\n"
        if len(ghosts) > 10:
            text += f"\n...aur {len(ghosts) - 10} aur members.\n"
        text += "\n⚠️ Unhe kick karne ke liye type karein: `/cleanup confirm`"
        await query.message.reply_text(text, parse_mode="Markdown")

    # 13. Export Excel
    elif data == "btn_export":
        await query.answer("📥 Excel file ban rahi hai, aapke DM me aayegi...")
        target_chat_id = query.from_user.id
        status_msg     = await context.bot.send_message(chat_id=target_chat_id, text="⏳ Excel report taiyaar ho rahi hai, kripya intezar karein...")
        try:
            filepath = database.export_to_excel("telegram_members_live_report.xlsx")
            with open(filepath, "rb") as f:
                await context.bot.send_document(
                    chat_id=target_chat_id,
                    document=f,
                    filename="telegram_members_live_report.xlsx",
                    caption="📊 **Members Full Report (Excel File)**"
                )
            await status_msg.delete()
        except Exception as e:
            logger.error(f"Excel export error: {e}")
            await status_msg.edit_text(f"❌ File export error: {e}")

    # 14. No-op (Permission section header button)
    elif data == "btn_perm_noop":
        await query.answer("⚙️ Neeche diye buttons se individual permissions toggle karein!", show_alert=False)
        return

    # 15. Individual Permission Toggles ─────────────────────────────────
    elif data in ("btn_perm_photos", "btn_perm_videos", "btn_perm_voice",
                  "btn_perm_polls",  "btn_perm_stickers", "btn_perm_invite", "btn_perm_docs"):
        if not target_group:
            await query.answer("❌ ALLOWED_GROUP_ID set nahi hai!", show_alert=True)
            return

        # Toggle the right variable
        if data == "btn_perm_photos":
            perm_photos   = not perm_photos
            label, new_val = "🖼 Photos",        perm_photos
        elif data == "btn_perm_videos":
            perm_videos   = not perm_videos
            label, new_val = "🎥 Videos",        perm_videos
        elif data == "btn_perm_voice":
            perm_voice    = not perm_voice
            label, new_val = "🎤 Voice/Audio",   perm_voice
        elif data == "btn_perm_polls":
            perm_polls    = not perm_polls
            label, new_val = "📊 Polls",         perm_polls
        elif data == "btn_perm_stickers":
            perm_stickers = not perm_stickers
            label, new_val = "😀 Stickers/GIFs", perm_stickers
        elif data == "btn_perm_invite":
            perm_invite   = not perm_invite
            label, new_val = "🔗 Invite",        perm_invite
        elif data == "btn_perm_docs":
            perm_docs     = not perm_docs
            label, new_val = "📄 Files/Docs",    perm_docs

        # Apply to group —
        # Agar quiz/night lock hai toh messages off rahenge, baaki sab update hoga
        current_send_msgs = not (is_chat_manually_locked or is_night_mode_active)
        try:
            await context.bot.set_chat_permissions(
                chat_id=int(target_group),
                permissions=build_permissions(send_messages=current_send_msgs)
            )
            status_word = "✅ ON" if new_val else "❌ OFF"
            await query.answer(f"{label}: {status_word} kar diya gaya!", show_alert=True)
        except Exception as perm_err:
            logger.error(f"Permission toggle error: {perm_err}")
            await query.answer(f"❌ Error: {perm_err}", show_alert=True)

        # Refresh dashboard buttons
        try:
            await query.edit_message_text(_dashboard_text(), reply_markup=get_control_panel_keyboard(), parse_mode="Markdown")
        except Exception:
            pass

# ─────────────────────────────────────────────────────────
# Name Change Helper
# ─────────────────────────────────────────────────────────
async def handle_name_change(chat, user):
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
        return
    alert_parts = []
    if change["name_changed"]:
        alert_parts.append(f"📛 **Purana Naam:** {change['old_name']}\n✨ **Naya Naam:** {change['new_name']}")
    if change["username_changed"]:
        old_u = f"@{change['old_username']}" if change["old_username"] else "None"
        new_u = f"@{change['new_username']}" if change["new_username"] else "None"
        alert_parts.append(f"📛 **Purana Username:** {old_u}\n✨ **Naya Username:** {new_u}")
    alert_text = (
        f"🔄 **Name Change Detected!**\n\n"
        f"🆔 **User ID:** `{change['user_id']}`\n"
        + "\n".join(alert_parts)
        + f"\n🕒 **Changed At:** `{change['changed_at']}`"
    )
    try:
        sent_alert = await chat.send_message(text=alert_text, parse_mode="Markdown")
        logger.info(f"Name change alert sent for {user.id}")
        async def auto_delete_alert(msg, delay=30):
            await asyncio.sleep(delay)
            try:
                await msg.delete()
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
    text = f"{message.text or ''} {message.caption or ''}"
    if LINK_REGEX.search(text):
        return True
    entities = list(message.entities or []) + list(message.caption_entities or [])
    for e in entities:
        if e.type in [MessageEntityType.URL, MessageEntityType.TEXT_LINK]:
            return True
    return False

def is_forwarded(message) -> bool:
    return bool(
        message.forward_origin
        or message.forward_date
        or message.forward_from
        or message.forward_from_chat
    )

async def send_auto_delete_warning(chat, text: str, delay: int = 10):
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
    msg  = update.message
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
    msg  = update.message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat:
        return

    await handle_name_change(chat, user)

    if chat.type not in ["group", "supergroup"]:
        return
    if ALLOWED_GROUP_ID and str(chat.id) != str(ALLOWED_GROUP_ID):
        return

    # Lock Mode: sirf Owner + Partner bol sakte hain
    if (is_night_mode_active or is_chat_manually_locked) and not (is_super_owner(user.id) or str(user.id) in LINK_ALLOWED_IDS):
        try:
            await msg.delete()
            logger.info(f"Lock Mode active: message deleted from {user.id}")
            return
        except Exception as e:
            logger.warning(f"Lock mode message delete error: {e}")

    if user.is_bot:
        return

    database.record_message(user.id)

    if is_allowed_to_post_links(user.id):
        return

    # Anti-Link
    if has_link(msg):
        try:
            await msg.delete()
            logger.info(f"Link deleted from user {user.id}")
            user_mention = f"[{user.first_name}](tg://user?id={user.id})"
            await send_auto_delete_warning(chat, f"⚠️ {user_mention}, is group me kisi bhi tarah ka **Link** bhejna sakht mana hai!", delay=10)
            return
        except Exception as e:
            logger.error(f"Error deleting link: {e}")

    # Anti-Forward
    if is_forwarded(msg):
        try:
            await msg.delete()
            logger.info(f"Forwarded message deleted from user {user.id}")
            user_mention = f"[{user.first_name}](tg://user?id={user.id})"
            await send_auto_delete_warning(chat, f"⚠️ {user_mention}, dusre channel ya group ke messages **Forward** karna mana hai!", delay=10)
            return
        except Exception as e:
            logger.error(f"Error deleting forwarded message: {e}")

    # Bad Words Filter
    text_to_check = msg.text or msg.caption or ""
    if text_to_check and contains_bad_word(text_to_check):
        try:
            await msg.delete()
            warn_count   = database.add_warning(user.id, reason="Abusive Language / Gaali")
            user_mention = f"[{user.first_name}](tg://user?id={user.id})"
            logger.info(f"Bad word deleted from {user.id}. Warning #{warn_count}")
            if warn_count >= 3:
                try:
                    await chat.ban_member(user_id=user.id)
                    await chat.unban_member(user_id=user.id)
                    await send_auto_delete_warning(chat, f"🚫 {user_mention} ko group se **Remove (Kick)** kar diya gaya hai (3/3 Warnings poori ho gayi)!", delay=20)
                except Exception as kick_err:
                    logger.error(f"Error kicking warned user: {kick_err}")
            else:
                await send_auto_delete_warning(
                    chat,
                    f"🤬 {user_mention}, is group me apshabd / gaali dena sakht mana hai!\n"
                    f"⚠️ **Warning: {warn_count}/3** (3 warnings par automatic remove ho jaoge)",
                    delay=12
                )
            return
        except Exception as e:
            logger.error(f"Error handling bad word: {e}")

async def check_name_on_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reaction = update.message_reaction
    if reaction and reaction.chat and reaction.user:
        await handle_name_change(reaction.chat, reaction.user)

async def track_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if not result:
        return
    chat = update.effective_chat
    if ALLOWED_GROUP_ID and str(chat.id) != str(ALLOWED_GROUP_ID):
        logger.warning(f"Unauthorized group {chat.id}. Leaving...")
        try:
            await chat.leave()
        except Exception:
            pass
        return
    user       = result.new_chat_member.user
    if user.is_bot:
        return
    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    was_member = old_status in MEMBER_STATUSES
    is_member  = new_status in MEMBER_STATUSES

    if not was_member and is_member:
        logger.info(f"Member JOINED: {user.first_name} (ID: {user.id})")
        database.member_joined(user_id=user.id, first_name=user.first_name, last_name=user.last_name, username=user.username)
        welcome_text = f"🎉 Welcome {user.first_name}\n\n✅ Admission Confirmed!"
        try:
            sent_msg = await chat.send_message(text=welcome_text)
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

    elif was_member and not is_member:
        logger.info(f"Member LEFT: {user.first_name} (ID: {user.id})")
        database.member_left(user_id=user.id, first_name=user.first_name, last_name=user.last_name, username=user.username)

    elif is_member:
        await handle_name_change(chat, user)

async def track_bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat or chat.type not in ["group", "supergroup"]:
        return
    if ALLOWED_GROUP_ID and str(chat.id) != str(ALLOWED_GROUP_ID):
        logger.warning(f"Bot unauthorized group me add hua ({chat.id}). Auto-leaving...")
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
        pass

def run_health_server():
    port   = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"Render Health Check Web Server running on port {port}")
    server.serve_forever()

# ========================================================
# 🌙 NIGHT MODE & 🎯 QUIZ LOCK — Core Functions
# ========================================================

async def lock_chat(bot, chat_id, announce_text=None, partner_allow=True):
    """
    Group chat lock karta hai — SIRF can_send_messages=False.
    Photos, videos, voice, polls etc. perm_* state ke hisaab se same rahenge.
    """
    try:
        await bot.set_chat_permissions(
            chat_id=int(chat_id),
            permissions=build_permissions(send_messages=False)
        )
        if partner_allow:
            # Partner 8914751355 ko lock ke baad bhi sabkuch bhejne ki permission
            try:
                await bot.restrict_chat_member(
                    chat_id=int(chat_id),
                    user_id=8914751355,
                    permissions=ChatPermissions(
                        can_send_messages=True,
                        can_send_audios=True,
                        can_send_documents=True,
                        can_send_photos=True,
                        can_send_videos=True,
                        can_send_video_notes=True,
                        can_send_voice_notes=True,
                        can_send_polls=True,
                        can_send_other_messages=True,
                        can_add_web_page_previews=True,
                        can_invite_users=True,
                    ),
                )
            except Exception as partner_err:
                logger.warning(f"Could not set partner permissions: {partner_err}")
        sent_msg_id = None
        if announce_text:
            sent        = await bot.send_message(chat_id=int(chat_id), text=announce_text)
            sent_msg_id = sent.message_id
        return True, sent_msg_id
    except Exception as e:
        logger.error(f"Error locking chat: {e}")
        return False, None

async def unlock_chat(bot, chat_id, delete_msg_id=None, announce_text=None, auto_del_delay=30):
    """
    Group chat unlock karta hai — SIRF can_send_messages=True.
    Baaki sab permissions perm_* state ke hisaab se same rahenge.
    """
    try:
        await bot.set_chat_permissions(
            chat_id=int(chat_id),
            permissions=build_permissions(send_messages=True)
        )
        if delete_msg_id:
            try:
                await bot.delete_message(chat_id=int(chat_id), message_id=delete_msg_id)
            except Exception as del_err:
                logger.warning(f"Could not delete lock message: {del_err}")
        if announce_text:
            sent = await bot.send_message(chat_id=int(chat_id), text=announce_text)
            if auto_del_delay:
                async def _del():
                    await asyncio.sleep(auto_del_delay)
                    try:
                        await sent.delete()
                    except Exception:
                        pass
                asyncio.create_task(_del())
        return True
    except Exception as e:
        logger.error(f"Error unlocking chat: {e}")
        return False

async def enable_night_mode(bot, chat_id, announce=True):
    """
    Night Mode ON — SIRF messages off.
    Photos / videos / voice etc. perm_* ke hisaab se same rahenge.
    """
    global is_night_mode_active, night_mode_message_id
    try:
        await bot.set_chat_permissions(
            chat_id=int(chat_id),
            permissions=build_permissions(send_messages=False)
        )
        is_night_mode_active = True
        database.set_bot_state("night_mode_active", "true")
        logger.info(f"Night Mode ENABLED for chat {chat_id}")

        # Partner ko raat me bhi sabkuch bhejne ki permission
        try:
            await bot.restrict_chat_member(
                chat_id=int(chat_id),
                user_id=8914751355,
                permissions=ChatPermissions(
                    can_send_messages=True,
                    can_send_audios=True,
                    can_send_documents=True,
                    can_send_photos=True,
                    can_send_videos=True,
                    can_send_video_notes=True,
                    can_send_voice_notes=True,
                    can_send_polls=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                    can_invite_users=True,
                ),
            )
            logger.info("Partner 8914751355 ko night mode me bhi full permission di.")
        except Exception as partner_err:
            logger.warning(f"Could not set partner permissions: {partner_err}")

        if announce:
            existing_msg_id = night_mode_message_id or database.get_bot_state("night_mode_msg_id")
            if not existing_msg_id:
                sent                = await bot.send_message(chat_id=int(chat_id), text="🌙 Night Mode Active! ✅")
                night_mode_message_id = sent.message_id
                database.set_bot_state("night_mode_msg_id", str(sent.message_id))
                logger.info(f"Night mode message sent (msg_id={sent.message_id}).")
            else:
                logger.info(f"Night mode notice already exists (msg_id={existing_msg_id}), skipping duplicate.")
        return True
    except Exception as e:
        logger.error(f"Error enabling night mode: {e}")
        return False

async def disable_night_mode(bot, chat_id, announce=True):
    """
    Night Mode OFF — SIRF messages on.
    Baaki permissions perm_* ke hisaab se same rahenge.
    """
    global is_night_mode_active, night_mode_message_id
    try:
        await bot.set_chat_permissions(
            chat_id=int(chat_id),
            permissions=build_permissions(send_messages=True)
        )
        is_night_mode_active = False
        database.set_bot_state("night_mode_active", "false")
        logger.info(f"Night Mode DISABLED for chat {chat_id}")

        target_del_id = night_mode_message_id or database.get_bot_state("night_mode_msg_id")
        if target_del_id:
            try:
                await bot.delete_message(chat_id=int(chat_id), message_id=int(target_del_id))
                logger.info(f"Night mode message (msg_id={target_del_id}) deleted at 6 AM.")
            except Exception as del_err:
                logger.warning(f"Could not delete night mode message: {del_err}")
            night_mode_message_id = None
            database.set_bot_state("night_mode_msg_id", "")

        if announce:
            sent = await bot.send_message(chat_id=int(chat_id), text="🌸 Radhe Radhe! 🙏")
            async def _del_morning():
                await asyncio.sleep(30)
                try:
                    await sent.delete()
                    logger.info("Morning 'Radhe Radhe' message auto-deleted after 30 seconds.")
                except Exception as del_err:
                    logger.warning(f"Error deleting morning message: {del_err}")
            asyncio.create_task(_del_morning())
        return True
    except Exception as e:
        logger.error(f"Error disabling night mode: {e}")
        return False

async def night_mode_worker(bot):
    """24/7 background scheduler: 11 PM lock, 6 AM unlock."""
    logger.info("Night Mode 11 PM - 6 AM IST Scheduler loop started.")
    while True:
        try:
            if ALLOWED_GROUP_ID:
                ist_now      = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
                is_night_time = (ist_now.hour >= 23 or ist_now.hour < 6)
                if is_night_time and not is_night_mode_active:
                    await enable_night_mode(bot, ALLOWED_GROUP_ID, announce=True)
                elif not is_night_time and is_night_mode_active:
                    await disable_night_mode(bot, ALLOWED_GROUP_ID, announce=True)
        except Exception as e:
            logger.error(f"Night mode worker error: {e}")
        await asyncio.sleep(30)

async def nightmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed(update):
        return
    chat         = update.effective_chat
    target_group = ALLOWED_GROUP_ID or (chat.id if chat.type in ["group", "supergroup"] else None)
    if not target_group:
        await update.message.reply_text("❌ ALLOWED_GROUP_ID set nahi hai.")
        return
    args = context.args
    if not args:
        status_text = "🌙 ACTIVE (Group Muted)" if is_night_mode_active else "☀️ INACTIVE (Group Open)"
        await update.message.reply_text(
            f"📌 **Night Mode Status:** {status_text}\n\n"
            "• `/nightmode on` - Group abhi turant lock karein\n"
            "• `/nightmode off` - Group abhi turant unlock karein\n"
            "*(Auto Schedule: Raat 11:00 PM se Subah 6:00 AM IST)*",
            parse_mode="Markdown"
        )
        return
    action = args[0].lower()
    if action == "on":
        success = await enable_night_mode(context.bot, target_group, announce=True)
        await update.message.reply_text("✅ Night Mode ON! Sirf Message OFF kiya gaya." if success else "❌ Night Mode on karne me error aaya.")
    elif action == "off":
        success = await disable_night_mode(context.bot, target_group, announce=True)
        await update.message.reply_text("✅ Night Mode OFF! Sirf Message ON kiya gaya." if success else "❌ Night Mode off karne me error aaya.")

async def quizmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_chat_manually_locked, quiz_lock_message_id
    if not await is_allowed(update):
        return
    chat         = update.effective_chat
    target_group = ALLOWED_GROUP_ID or (chat.id if chat.type in ["group", "supergroup"] else None)
    if not target_group:
        await update.message.reply_text("❌ ALLOWED_GROUP_ID set nahi hai.")
        return
    args = context.args
    if not args:
        status_text = "🔴 ACTIVE (Chat Locked)" if is_chat_manually_locked else "🟢 INACTIVE (Chat Open)"
        await update.message.reply_text(
            f"🎯 **Quiz Mode Status:** {status_text}\n\n"
            "• `/quiz on` ya `/quiz lock` - Quiz ke liye chat lock karein\n"
            "• `/quiz off` ya `/quiz unlock` - Quiz khatam hone par chat unlock karein",
            parse_mode="Markdown"
        )
        return
    action = args[0].lower()
    if action in ["on", "lock"]:
        is_chat_manually_locked = True
        announce_text = "📣 Quiz खत्म होने के बाद Message Option ON कर दिया जाएगा !! ❤️"
        success, msg_id = await lock_chat(context.bot, target_group, announce_text=announce_text, partner_allow=True)
        quiz_lock_message_id = msg_id
        await update.message.reply_text("✅ **Quiz Mode ON!** Sirf Message OFF kiya gaya." if success else "❌ Group chat lock karne me error aaya.")
    elif action in ["off", "unlock"]:
        is_chat_manually_locked = False
        success = await unlock_chat(context.bot, target_group, delete_msg_id=quiz_lock_message_id, announce_text="🎉 Quiz समाप्त हो गया है !! ❤️", auto_del_delay=30)
        quiz_lock_message_id = None
        await update.message.reply_text("✅ **Quiz Mode OFF!** Sirf Message ON kiya gaya." if success else "❌ Group chat unlock karne me error aaya.")

async def daily_report_worker(bot):
    logger.info("Daily 9:00 PM IST Scheduler loop started.")
    while True:
        try:
            ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
            target  = ist_now.replace(hour=21, minute=0, second=0, microsecond=0)
            if ist_now >= target:
                target += timedelta(days=1)
            await asyncio.sleep((target - ist_now).total_seconds())
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
            await bot.send_message(chat_id=int(SUPER_OWNER_ID), text=report_text, parse_mode="Markdown")
            logger.info("9:00 PM Daily report sent successfully to Owner!")
            await asyncio.sleep(70)
        except Exception as e:
            logger.error(f"Daily report scheduler error: {e}")
            await asyncio.sleep(60)

async def weekly_report_worker(bot):
    logger.info("Weekly Monday 9:00 AM IST Scheduler loop started.")
    while True:
        try:
            ist_now    = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
            days_ahead = (0 - ist_now.weekday()) % 7
            target     = ist_now.replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
            if ist_now >= target:
                target += timedelta(days=7)
            await asyncio.sleep((target - ist_now).total_seconds())
            w = database.get_weekly_summary_stats()
            top_text = ""
            if w.get("top_5"):
                top_text = "\n\n🏆 **Top 5 Active Chatters of the Week:**\n"
                for i, m in enumerate(w["top_5"], 1):
                    top_text += f"{i}. {m.get('first_name', 'User')} — `{m.get('message_count', 0)} msgs`\n"
            weekly_text = (
                f"📅 **Har Somwar (Monday) Weekly Summary Report:**\n"
                f"🕒 *Period: {w['from_date']} se {w['to_date']}*\n\n"
                f"🟢 **Hafte ke Naye Joins:** {w['joins']}\n"
                f"🔴 **Hafte ke Left Members:** {w['left']}\n"
                f"🔄 **Name Changes:** {w['name_changes']}\n"
                f"👥 **Total Active Members:** {w['active_total']}\n"
                f"📁 **Total Tracked in DB:** {w['total_members']}"
                f"{top_text}\n"
                "Poora detail report lene ke liye `/export` bhejein."
            )
            await bot.send_message(chat_id=int(SUPER_OWNER_ID), text=weekly_text, parse_mode="Markdown")
            logger.info("Monday Weekly report sent successfully to Owner!")
            await asyncio.sleep(70)
        except Exception as e:
            logger.error(f"Weekly report scheduler error: {e}")
            await asyncio.sleep(60)

async def post_init(application):
    global is_night_mode_active, night_mode_message_id, is_chat_manually_locked
    saved_nm = database.get_bot_state("night_mode_active")
    if saved_nm == "true":
        is_night_mode_active = True
        saved_msg_id = database.get_bot_state("night_mode_msg_id")
        if saved_msg_id and saved_msg_id.isdigit():
            night_mode_message_id = int(saved_msg_id)
        logger.info(f"Bot restart: Night Mode state restored from DB (msg_id={night_mode_message_id})")
    asyncio.create_task(daily_report_worker(application.bot))
    asyncio.create_task(weekly_report_worker(application.bot))
    asyncio.create_task(night_mode_worker(application.bot))

def main():
    database.init_db()
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
    application.add_handler(CommandHandler("start",       start_cmd))
    application.add_handler(CommandHandler("whois",       whois_cmd))
    application.add_handler(CommandHandler("top",         top_cmd))
    application.add_handler(CommandHandler("inactive",    inactive_cmd))
    application.add_handler(CommandHandler("today",       today_cmd))
    application.add_handler(CommandHandler("members",     members_cmd))
    application.add_handler(CommandHandler("list",        members_cmd))
    application.add_handler(CommandHandler("stats",       stats_cmd))
    application.add_handler(CommandHandler("left",        recent_left_cmd))
    application.add_handler(CommandHandler("nightmode",   nightmode_cmd))
    application.add_handler(CommandHandler("quiz",        quizmode_cmd))
    application.add_handler(CommandHandler("ban",         ban_cmd))
    application.add_handler(CommandHandler("namechanges", namechanges_cmd))
    application.add_handler(CommandHandler("weekly",      weekly_cmd))
    application.add_handler(CommandHandler("cleanup",     cleanup_cmd))
    application.add_handler(CommandHandler("export",      export_cmd))
    application.add_handler(CallbackQueryHandler(panel_callback_handler))
    application.add_handler(ChatMemberHandler(track_chat_member, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(ChatMemberHandler(track_bot_added,   ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.StatusUpdate.ALL,         clean_service_messages))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND,   check_name_on_message))
    application.add_handler(MessageReactionHandler(check_name_on_reaction))
    print("🚀 Member Tracker Bot start ho gaya hai! Listening for joins & leaves...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
