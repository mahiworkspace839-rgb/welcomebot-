import os
import sqlite3
from datetime import datetime
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
SQLITE_DB = "members.db"

# MongoDB connection
mongo_client = None
mongo_db = None
mongo_col = None

if MONGO_URI:
    try:
        from pymongo import MongoClient
        mongo_client = MongoClient(MONGO_URI)
        mongo_db = mongo_client["telegram_tracker"]
        mongo_col = mongo_db["members"]
        print("✅ MongoDB Atlas se safaltapoorvak connect ho gaya!")
    except Exception as e:
        print(f"⚠️ MongoDB connection me warning: {e}. SQLite use kiya jayega.")
        mongo_col = None

def init_db():
    """Database tables/collections initialize karta hai."""
    if mongo_col is not None:
        # MongoDB me indexes create karein
        mongo_col.create_index("status")
        mongo_col.create_index("left_at")
        return

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
                updated_at TEXT
            )
        """)
        conn.commit()

def save_scanned_member(user_id, first_name, last_name, username, phone=None):
    """Puraane members ko initial scan ke waqt save karta hai."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if mongo_col is not None:
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
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if mongo_col is not None:
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
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if mongo_col is not None:
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

def get_stats():
    """Group ke current stats nikalta hai."""
    if mongo_col is not None:
        active_count = mongo_col.count_documents({"status": "active"})
        left_count = mongo_col.count_documents({"status": "left"})
        total_tracked = mongo_col.count_documents({})
        return {
            "active": active_count,
            "left": left_count,
            "total": total_tracked
        }

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

    # Rows retrieve karein
    rows = []
    if mongo_col is not None:
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
    else:
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
