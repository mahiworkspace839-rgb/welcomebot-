import os
import re
import sqlite3
from datetime import datetime, timezone, timedelta
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from dotenv import load_dotenv

load_dotenv()

def get_ist_now():
    """Current Indian Standard Time (IST = UTC+5:30) datetime object."""
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

def format_datetime_ist(dt_str):
    """
    Purane ya naye datetime string ko pyare 12-hour AM/PM format me badalta hai:
    '2026-11-09 15:01:42' -> '09 Nov 2026, 03:01 PM'
    """
    if not dt_str:
        return "N/A"
    try:
        # Agar already standard format me hai
        clean_str = str(dt_str).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %I:%M:%S %p", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(clean_str, fmt)
                return dt.strftime("%d %b %Y, %I:%M %p")
            except ValueError:
                pass
        return clean_str
    except Exception:
        return str(dt_str)

MONGO_URI = os.getenv("MONGO_URI")
SQLITE_DB = "members.db"

# MongoDB connection
mongo_client = None
mongo_db = None
mongo_col = None

if MONGO_URI:
    try:
        from pymongo import MongoClient
        import certifi
        mongo_client = MongoClient(
            MONGO_URI,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=5000
        )
        # Test connection ping
        mongo_client.admin.command('ping')
        mongo_db = mongo_client["telegram_tracker"]
        mongo_col = mongo_db["members"]
        print("✅ MongoDB Atlas se safaltapoorvak connect ho gaya!")
    except Exception as e:
        print(f"⚠️ MongoDB Atlas connection error: {e}. Fallback SQLite use kiya ja raha hai.")
        mongo_col = None

# Name changes collection
mongo_name_col = None
# Warnings collection
mongo_warn_col = None
if mongo_db is not None:
    mongo_name_col = mongo_db["name_changes"]
    mongo_warn_col = mongo_db["warnings"]

def init_db():
    """Database tables/collections initialize karta hai."""
    global mongo_col, mongo_name_col, mongo_warn_col
    if mongo_col is not None:
        try:
            mongo_col.create_index("status")
            mongo_col.create_index("left_at")
            if mongo_name_col is not None:
                mongo_name_col.create_index("user_id")
                mongo_name_col.create_index("changed_at")
            if mongo_warn_col is not None:
                mongo_warn_col.create_index("user_id")
            return
        except Exception as e:
            print(f"⚠️ Index create warning: {e}")

    # Fallback SQLite
    with sqlite3.connect(SQLITE_DB) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS members (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT,
                last_name TEXT,
                username TEXT,
                phone TEXT,
                status TEXT DEFAULT 'active',
                joined_at TEXT,
                left_at TEXT,
                updated_at TEXT,
                message_count INTEGER DEFAULT 0,
                last_message_at TEXT
            )
        """)
        # Existing tables ke liye columns check karein
        cursor.execute("PRAGMA table_info(members)")
        cols = [c[1] for c in cursor.fetchall()]
        if "message_count" not in cols:
            cursor.execute("ALTER TABLE members ADD COLUMN message_count INTEGER DEFAULT 0")
        if "last_message_at" not in cols:
            cursor.execute("ALTER TABLE members ADD COLUMN last_message_at TEXT")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS name_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                old_first_name TEXT,
                old_last_name TEXT,
                old_username TEXT,
                new_first_name TEXT,
                new_last_name TEXT,
                new_username TEXT,
                changed_at TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                reason TEXT,
                warned_at TEXT
            )
        """)
        conn.commit()

def save_scanned_member(user_id, first_name, last_name, username, phone=None):
    """Puraane members ko initial scan ke waqt save karta hai."""
    now_str = get_ist_now().strftime("%Y-%m-%d %I:%M:%S %p")

    if mongo_col is not None:
        try:
            mongo_col.update_one(
                {"_id": user_id},
                {
                    "$set": {
                        "first_name": first_name,
                        "last_name": last_name,
                        "username": username,
                        "phone": phone,
                        "status": "active",
                        "updated_at": now_str
                    },
                    "$setOnInsert": {
                        "joined_at": "Pre-existing Member"
                    }
                },
                upsert=True
            )
            return
        except Exception as e:
            print(f"MongoDB write error: {e}")

    with sqlite3.connect(SQLITE_DB) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO members (user_id, first_name, last_name, username, phone, status, joined_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'active', 'Pre-existing Member', ?)
            ON CONFLICT(user_id) DO UPDATE SET
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                username = excluded.username,
                phone = COALESCE(excluded.phone, members.phone),
                status = 'active',
                updated_at = excluded.updated_at
        """, (user_id, first_name, last_name, username, phone, now_str))
        conn.commit()

def member_joined(user_id, first_name, last_name, username):
    """Naya member join hone par record add ya update karta hai."""
    now_str = get_ist_now().strftime("%Y-%m-%d %I:%M:%S %p")

    if mongo_col is not None:
        try:
            mongo_col.update_one(
                {"_id": user_id},
                {
                    "$set": {
                        "first_name": first_name,
                        "last_name": last_name,
                        "username": username,
                        "status": "active",
                        "joined_at": now_str,
                        "left_at": None,
                        "updated_at": now_str
                    }
                },
                upsert=True
            )
            return
        except Exception as e:
            print(f"MongoDB join write error: {e}")

    with sqlite3.connect(SQLITE_DB) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO members (user_id, first_name, last_name, username, status, joined_at, left_at, updated_at)
            VALUES (?, ?, ?, ?, 'active', ?, NULL, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                username = excluded.username,
                status = 'active',
                left_at = NULL,
                updated_at = excluded.updated_at
        """, (user_id, first_name, last_name, username, now_str, now_str))
        conn.commit()

def member_left(user_id, first_name=None, last_name=None, username=None):
    """Member ke group chhodne (left/kicked) par status update karta hai."""
    now_str = get_ist_now().strftime("%Y-%m-%d %I:%M:%S %p")

    if mongo_col is not None:
        try:
            update_data = {
                "status": "left",
                "left_at": now_str,
                "updated_at": now_str
            }
            if first_name:
                update_data["first_name"] = first_name
            if last_name:
                update_data["last_name"] = last_name
            if username:
                update_data["username"] = username

            mongo_col.update_one(
                {"_id": user_id},
                {
                    "$set": update_data,
                    "$setOnInsert": {
                        "joined_at": "Unknown"
                    }
                },
                upsert=True
            )
            return
        except Exception as e:
            print(f"MongoDB left write error: {e}")

    with sqlite3.connect(SQLITE_DB) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM members WHERE user_id = ?", (user_id,))
        exists = cursor.fetchone()

        if exists:
            cursor.execute("""
                UPDATE members
                SET status = 'left', left_at = ?, updated_at = ?
                WHERE user_id = ?
            """, (now_str, now_str, user_id))
        else:
            cursor.execute("""
                INSERT INTO members (user_id, first_name, last_name, username, status, joined_at, left_at, updated_at)
                VALUES (?, ?, ?, ?, 'left', 'Unknown', ?, ?)
            """, (user_id, first_name, last_name, username, now_str, now_str))
        conn.commit()

def check_name_change(user_id, current_first, current_last, current_username):
    """
    User ka current naam database me stored naam se compare karta hai.
    Agar naam badla hai toh name_changes me log karta hai aur change details return karta hai.
    Agar nahi badla toh None return karta hai.
    """
    now_str = get_ist_now().strftime("%Y-%m-%d %I:%M:%S %p")

    if mongo_col is not None:
        try:
            doc = mongo_col.find_one({"_id": user_id})
            if not doc:
                return None

            old_first = doc.get("first_name") or ""
            old_last = doc.get("last_name") or ""
            old_uname = doc.get("username") or ""

            name_changed = (old_first != (current_first or "")) or (old_last != (current_last or ""))
            uname_changed = (old_uname != (current_username or ""))

            if not name_changed and not uname_changed:
                return None

            # Name change log save karein
            change_record = {
                "user_id": user_id,
                "old_first_name": old_first,
                "old_last_name": old_last,
                "old_username": old_uname,
                "new_first_name": current_first or "",
                "new_last_name": current_last or "",
                "new_username": current_username or "",
                "changed_at": now_str
            }
            if mongo_name_col is not None:
                mongo_name_col.insert_one(change_record)

            # Member record me naam update karein
            mongo_col.update_one(
                {"_id": user_id},
                {"$set": {
                    "first_name": current_first,
                    "last_name": current_last,
                    "username": current_username,
                    "updated_at": now_str
                }}
            )

            return {
                "user_id": user_id,
                "old_name": f"{old_first} {old_last}".strip() or "No Name",
                "new_name": f"{current_first or ''} {current_last or ''}".strip() or "No Name",
                "old_username": old_uname,
                "new_username": current_username,
                "name_changed": name_changed,
                "username_changed": uname_changed,
                "changed_at": now_str
            }
        except Exception as e:
            print(f"MongoDB name check error: {e}")
            return None

    # SQLite fallback
    with sqlite3.connect(SQLITE_DB) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT first_name, last_name, username FROM members WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            return None

        old_first, old_last, old_uname = row[0] or "", row[1] or "", row[2] or ""

        name_changed = (old_first != (current_first or "")) or (old_last != (current_last or ""))
        uname_changed = (old_uname != (current_username or ""))

        if not name_changed and not uname_changed:
            return None

        cursor.execute("""
            INSERT INTO name_changes (user_id, old_first_name, old_last_name, old_username,
                                      new_first_name, new_last_name, new_username, changed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, old_first, old_last, old_uname,
              current_first or "", current_last or "", current_username or "", now_str))

        cursor.execute("""
            UPDATE members SET first_name = ?, last_name = ?, username = ?, updated_at = ?
            WHERE user_id = ?
        """, (current_first, current_last, current_username, now_str, user_id))
        conn.commit()

        return {
            "user_id": user_id,
            "old_name": f"{old_first} {old_last}".strip() or "No Name",
            "new_name": f"{current_first or ''} {current_last or ''}".strip() or "No Name",
            "old_username": old_uname,
            "new_username": current_username,
            "name_changed": name_changed,
            "username_changed": uname_changed,
            "changed_at": now_str
        }

def get_recent_name_changes(limit=15):
    """Haal hi ki name changes ki list return karta hai."""
    if mongo_name_col is not None:
        try:
            docs = mongo_name_col.find({}).sort("changed_at", -1).limit(limit)
            return list(docs)
        except Exception as e:
            print(f"MongoDB name changes read error: {e}")

    with sqlite3.connect(SQLITE_DB) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, old_first_name, old_last_name, old_username,
                   new_first_name, new_last_name, new_username, changed_at
            FROM name_changes
            ORDER BY changed_at DESC
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        return [
            {
                "user_id": r[0],
                "old_first_name": r[1], "old_last_name": r[2], "old_username": r[3],
                "new_first_name": r[4], "new_last_name": r[5], "new_username": r[6],
                "changed_at": r[7]
            } for r in rows
        ]

def get_all_members():
    """Sabhi members ki full details list return karta hai."""
    if mongo_col is not None:
        try:
            return list(mongo_col.find({}).sort("status", 1))
        except Exception as e:
            print(f"MongoDB read error: {e}")

    with sqlite3.connect(SQLITE_DB) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, first_name, last_name, username, phone, status, joined_at, left_at
            FROM members
            ORDER BY status ASC
        """)
        rows = cursor.fetchall()
        return [
            {
                "_id": r[0],
                "first_name": r[1],
                "last_name": r[2],
                "username": r[3],
                "phone": r[4],
                "status": r[5],
                "joined_at": r[6],
                "left_at": r[7]
            } for r in rows
        ]

def get_stats():
    """Group ke current stats nikalta hai."""
    if mongo_col is not None:
        try:
            active_count = mongo_col.count_documents({"status": "active"})
            left_count = mongo_col.count_documents({"status": "left"})
            total_tracked = mongo_col.count_documents({})
            return {
                "active": active_count,
                "left": left_count,
                "total": total_tracked
            }
        except Exception as e:
            print(f"MongoDB stats read error: {e}")

    with sqlite3.connect(SQLITE_DB) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM members WHERE status = 'active'")
        active_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM members WHERE status = 'left'")
        left_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM members")
        total_tracked = cursor.fetchone()[0]

        return {
            "active": active_count,
            "left": left_count,
            "total": total_tracked
        }

def get_recent_left(limit=10):
    """Haal hi me group chhodne wale members ki list deta hai."""
    if mongo_col is not None:
        try:
            docs = mongo_col.find(
                {"status": "left", "left_at": {"$ne": None}}
            ).sort("left_at", -1).limit(limit)

            result = []
            for doc in docs:
                result.append((
                    doc["_id"],
                    doc.get("first_name"),
                    doc.get("last_name"),
                    doc.get("username"),
                    doc.get("left_at")
                ))
            return result
        except Exception as e:
            print(f"MongoDB recent left read error: {e}")

    with sqlite3.connect(SQLITE_DB) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, first_name, last_name, username, left_at
            FROM members
            WHERE status = 'left' AND left_at IS NOT NULL
            ORDER BY left_at DESC
            LIMIT ?
        """, (limit,))
        return cursor.fetchall()

def export_to_excel(filepath="telegram_members_report.xlsx"):
    """Sabhi members ka pura data formatted Excel sheet me export karta hai."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Members Report"

    headers = [
        "User ID",
        "First Name",
        "Last Name",
        "Username",
        "Phone",
        "Status (Active/Left)",
        "Joined At",
        "Left At",
        "Last Updated"
    ]
    ws.append(headers)

    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")

    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )

    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_num)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = align_center

    rows = []
    if mongo_col is not None:
        try:
            docs = mongo_col.find({}).sort("status", 1)
            for d in docs:
                rows.append((
                    d.get("_id"),
                    d.get("first_name", ""),
                    d.get("last_name", ""),
                    d.get("username", ""),
                    d.get("phone", ""),
                    d.get("status", "active"),
                    d.get("joined_at", ""),
                    d.get("left_at", ""),
                    d.get("updated_at", "")
                ))
        except Exception as e:
            print(f"MongoDB export read error: {e}")

    if not rows:
        with sqlite3.connect(SQLITE_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT user_id, first_name, last_name, username, phone, status, joined_at, left_at, updated_at
                FROM members
                ORDER BY status ASC, updated_at DESC
            """)
            rows = cursor.fetchall()

    active_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
    left_fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")

    for row_idx, row_data in enumerate(rows, start=2):
        for col_idx, value in enumerate(row_data, start=1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.value = value if value is not None else ""
            cell.border = thin_border
            cell.alignment = align_left

            if col_idx == 6:
                cell.alignment = align_center
                if str(value).lower() == "active":
                    cell.fill = active_fill
                else:
                    cell.fill = left_fill

    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    wb.save(filepath)
    return filepath

def record_message(user_id):
    """Member ke message count aur last seen ko increment karta hai."""
    now_str = get_ist_now().strftime("%Y-%m-%d %I:%M:%S %p")
    if mongo_col is not None:
        try:
            mongo_col.update_one(
                {"_id": user_id},
                {
                    "$inc": {"message_count": 1},
                    "$set": {"last_message_at": now_str}
                },
                upsert=False
            )
            return
        except Exception as e:
            print(f"MongoDB record message error: {e}")

    with sqlite3.connect(SQLITE_DB) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE members 
            SET message_count = COALESCE(message_count, 0) + 1,
                last_message_at = ?
            WHERE user_id = ?
        """, (now_str, user_id))
        conn.commit()

def get_user_history(query: str):
    """
    User ki poori history (join, leave, message count, name changes) nikalta hai.
    query: User ID (int/str) ya Username (@username).
    """
    clean_q = str(query).strip().lstrip("@")
    target_id = None
    if clean_q.isdigit():
        target_id = int(clean_q)

    user_doc = None
    if mongo_col is not None:
        try:
            if target_id:
                user_doc = mongo_col.find_one({"_id": target_id})
            if not user_doc:
                user_doc = mongo_col.find_one({
                    "username": {"$regex": f"^{re.escape(clean_q)}$", "$options": "i"}
                })

            if not user_doc:
                return None

            uid = user_doc["_id"]
            changes = []
            if mongo_name_col is not None:
                changes = list(mongo_name_col.find({"user_id": uid}).sort("changed_at", -1))

            return {
                "user_id": uid,
                "first_name": user_doc.get("first_name", ""),
                "last_name": user_doc.get("last_name", ""),
                "username": user_doc.get("username", ""),
                "status": user_doc.get("status", "active"),
                "joined_at": user_doc.get("joined_at", "N/A"),
                "left_at": user_doc.get("left_at"),
                "message_count": user_doc.get("message_count", 0),
                "last_message_at": user_doc.get("last_message_at", "N/A"),
                "name_changes": changes
            }
        except Exception as e:
            print(f"MongoDB whois error: {e}")
            return None

    # SQLite fallback
    with sqlite3.connect(SQLITE_DB) as conn:
        cursor = conn.cursor()
        if target_id:
            cursor.execute("""
                SELECT user_id, first_name, last_name, username, status, joined_at, left_at, message_count, last_message_at 
                FROM members WHERE user_id = ?
            """, (target_id,))
        else:
            cursor.execute("""
                SELECT user_id, first_name, last_name, username, status, joined_at, left_at, message_count, last_message_at 
                FROM members WHERE LOWER(username) = LOWER(?)
            """, (clean_q,))
        row = cursor.fetchone()
        if not row:
            return None

        uid = row[0]
        cursor.execute("""
            SELECT old_first_name, old_last_name, old_username, new_first_name, new_last_name, new_username, changed_at 
            FROM name_changes WHERE user_id = ? ORDER BY changed_at DESC
        """, (uid,))
        nc_rows = cursor.fetchall()
        changes = [
            {
                "old_first_name": c[0], "old_last_name": c[1], "old_username": c[2],
                "new_first_name": c[3], "new_last_name": c[4], "new_username": c[5],
                "changed_at": c[6]
            } for c in nc_rows
        ]

        return {
            "user_id": uid,
            "first_name": row[1] or "",
            "last_name": row[2] or "",
            "username": row[3] or "",
            "status": row[4] or "active",
            "joined_at": row[5] or "N/A",
            "left_at": row[6],
            "message_count": row[7] or 0,
            "last_message_at": row[8] or "N/A",
            "name_changes": changes
        }

def get_top_members(limit=10):
    """Sabse zyada messages bhejne wale top active members."""
    if mongo_col is not None:
        try:
            docs = mongo_col.find(
                {"status": "active", "message_count": {"$gt": 0}}
            ).sort("message_count", -1).limit(limit)
            return list(docs)
        except Exception as e:
            print(f"MongoDB top members error: {e}")

    with sqlite3.connect(SQLITE_DB) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, first_name, last_name, username, message_count
            FROM members
            WHERE status = 'active' AND message_count > 0
            ORDER BY message_count DESC
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        return [
            {
                "_id": r[0],
                "first_name": r[1] or "",
                "last_name": r[2] or "",
                "username": r[3] or "",
                "message_count": r[4] or 0
            } for r in rows
        ]

def get_inactive_members(days=15, limit=20):
    """Pichle `days` din se silent/inactive active members."""
    cutoff = get_ist_now() - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    if mongo_col is not None:
        try:
            query = {
                "status": "active",
                "$or": [
                    {"last_message_at": None},
                    {"last_message_at": {"$lt": cutoff_str}},
                    {"message_count": {"$in": [0, None]}}
                ]
            }
            return list(mongo_col.find(query).limit(limit))
        except Exception as e:
            print(f"MongoDB inactive error: {e}")

    with sqlite3.connect(SQLITE_DB) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, first_name, last_name, username, joined_at, message_count, last_message_at
            FROM members
            WHERE status = 'active' AND (last_message_at IS NULL OR last_message_at < ? OR message_count = 0 OR message_count IS NULL)
            LIMIT ?
        """, (cutoff_str, limit))
        rows = cursor.fetchall()
        return [
            {
                "_id": r[0],
                "first_name": r[1] or "",
                "last_name": r[2] or "",
                "username": r[3] or "",
                "joined_at": r[4] or "",
                "message_count": r[5] or 0,
                "last_message_at": r[6]
            } for r in rows
        ]

def get_daily_summary_stats():
    """Aaj ki 24 ghante ki summary (joins, lefts, name changes, totals)."""
    today_str = get_ist_now().strftime("%Y-%m-%d")

    if mongo_col is not None:
        try:
            active_total = mongo_col.count_documents({"status": "active"})
            total_members = mongo_col.count_documents({})
            joins_today = mongo_col.count_documents({
                "joined_at": {"$regex": f"^{today_str}"}
            })
            left_today = mongo_col.count_documents({
                "left_at": {"$regex": f"^{today_str}"}
            })
            name_changes_today = 0
            if mongo_name_col is not None:
                name_changes_today = mongo_name_col.count_documents({
                    "changed_at": {"$regex": f"^{today_str}"}
                })
            return {
                "joins": joins_today,
                "left": left_today,
                "name_changes": name_changes_today,
                "active_total": active_total,
                "total_members": total_members,
                "date": today_str
            }
        except Exception as e:
            print(f"MongoDB daily summary error: {e}")

    with sqlite3.connect(SQLITE_DB) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM members WHERE status = 'active'")
        active_total = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM members")
        total_members = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM members WHERE joined_at LIKE ?", (f"{today_str}%",))
        joins_today = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM members WHERE left_at LIKE ?", (f"{today_str}%",))
        left_today = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM name_changes WHERE changed_at LIKE ?", (f"{today_str}%",))
        name_changes_today = cursor.fetchone()[0]

        return {
            "joins": joins_today,
            "left": left_today,
            "name_changes": name_changes_today,
            "active_total": active_total,
            "total_members": total_members,
            "date": today_str
        }


# ========================================================
# ⚠️ WARNING SYSTEM (Gaali Filter ke liye auto-warn)
# ========================================================

def add_warning(user_id, reason="Bad word / Gaali"):
    """User ko ek warning deta hai. Returns total warnings count."""
    now_str = get_ist_now().strftime("%Y-%m-%d %I:%M:%S %p")

    if mongo_warn_col is not None:
        try:
            mongo_warn_col.insert_one({
                "user_id": user_id,
                "reason": reason,
                "warned_at": now_str,
            })
            count = mongo_warn_col.count_documents({"user_id": user_id})
            return count
        except Exception as e:
            print(f"MongoDB add_warning error: {e}")

    with sqlite3.connect(SQLITE_DB) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO warnings (user_id, reason, warned_at) VALUES (?, ?, ?)",
            (user_id, reason, now_str)
        )
        conn.commit()
        cursor.execute("SELECT COUNT(*) FROM warnings WHERE user_id = ?", (user_id,))
        return cursor.fetchone()[0]


def get_warnings(user_id):
    """User ki total warnings count return karta hai."""
    if mongo_warn_col is not None:
        try:
            return mongo_warn_col.count_documents({"user_id": user_id})
        except Exception as e:
            print(f"MongoDB get_warnings error: {e}")

    with sqlite3.connect(SQLITE_DB) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM warnings WHERE user_id = ?", (user_id,))
        return cursor.fetchone()[0]


def clear_warnings(user_id):
    """User ki saari warnings clear karta hai."""
    if mongo_warn_col is not None:
        try:
            mongo_warn_col.delete_many({"user_id": user_id})
            return True
        except Exception as e:
            print(f"MongoDB clear_warnings error: {e}")

    with sqlite3.connect(SQLITE_DB) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM warnings WHERE user_id = ?", (user_id,))
        conn.commit()
    return True


# ========================================================
# 📅 WEEKLY REPORT (Monday summary)
# ========================================================

def get_weekly_summary_stats():
    """Pichle 7 din ki summary (joins, lefts, name changes, top active)."""
    today = get_ist_now()
    week_ago = today - timedelta(days=7)
    week_ago_str = week_ago.strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")

    if mongo_col is not None:
        try:
            active_total = mongo_col.count_documents({"status": "active"})
            total_members = mongo_col.count_documents({})
            joins_week = mongo_col.count_documents({
                "joined_at": {"$gte": week_ago_str}
            })
            left_week = mongo_col.count_documents({
                "left_at": {"$gte": week_ago_str}
            })
            name_changes_week = 0
            if mongo_name_col is not None:
                name_changes_week = mongo_name_col.count_documents({
                    "changed_at": {"$gte": week_ago_str}
                })
            # Top 5 active members of the week
            top_5 = list(mongo_col.find(
                {"status": "active", "message_count": {"$gt": 0}},
                {"first_name": 1, "message_count": 1}
            ).sort("message_count", -1).limit(5))
            return {
                "joins": joins_week,
                "left": left_week,
                "name_changes": name_changes_week,
                "active_total": active_total,
                "total_members": total_members,
                "top_5": top_5,
                "from_date": week_ago_str,
                "to_date": today_str
            }
        except Exception as e:
            print(f"MongoDB weekly summary error: {e}")

    with sqlite3.connect(SQLITE_DB) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM members WHERE status = 'active'")
        active_total = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM members")
        total_members = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM members WHERE joined_at >= ?", (week_ago_str,))
        joins_week = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM members WHERE left_at >= ?", (week_ago_str,))
        left_week = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM name_changes WHERE changed_at >= ?", (week_ago_str,))
        name_changes_week = cursor.fetchone()[0]

        cursor.execute("""
            SELECT first_name, message_count FROM members
            WHERE status = 'active' AND message_count > 0
            ORDER BY message_count DESC LIMIT 5
        """)
        top_5_rows = cursor.fetchall()
        top_5 = [{"first_name": r[0], "message_count": r[1]} for r in top_5_rows]

        return {
            "joins": joins_week,
            "left": left_week,
            "name_changes": name_changes_week,
            "active_total": active_total,
            "total_members": total_members,
            "top_5": top_5,
            "from_date": week_ago_str,
            "to_date": today_str
        }


# ========================================================
# 👻 GHOST MEMBER CLEANER (Inactive members list for kick)
# ========================================================

def get_ghost_members(days=30, limit=50):
    """X din se bilkul silent active members ki list (kick ke liye)."""
    cutoff = get_ist_now() - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    if mongo_col is not None:
        try:
            query = {
                "status": "active",
                "$or": [
                    {"last_message_at": None},
                    {"last_message_at": {"$lt": cutoff_str}},
                    {"message_count": {"$in": [0, None]}}
                ]
            }
            return list(mongo_col.find(query).limit(limit))
        except Exception as e:
            print(f"MongoDB ghost members error: {e}")

    with sqlite3.connect(SQLITE_DB) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, first_name, last_name, username, joined_at, message_count, last_message_at
            FROM members
            WHERE status = 'active' AND (last_message_at IS NULL OR last_message_at < ? OR message_count = 0 OR message_count IS NULL)
            LIMIT ?
        """, (cutoff_str, limit))
        rows = cursor.fetchall()
        return [
            {
                "_id": r[0],
                "user_id": r[0],
                "first_name": r[1] or "",
                "last_name": r[2] or "",
                "username": r[3] or "",
                "joined_at": r[4] or "",
                "message_count": r[5] or 0,
                "last_message_at": r[6]
            } for r in rows
        ]
