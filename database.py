import sqlite3
from datetime import datetime
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

DB_NAME = "members.db"

def get_connection():
    return sqlite3.connect(DB_NAME)

def init_db():
    """Database aur members table create karta hai."""
    with get_connection() as conn:
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
    with get_connection() as conn:
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
    with get_connection() as conn:
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
    with get_connection() as conn:
        cursor = conn.cursor()
        # Pehle check karo ki user pehle se database me hai ya nahi
        cursor.execute("SELECT user_id FROM members WHERE user_id = ?", (user_id,))
        exists = cursor.fetchone()

        if exists:
            cursor.execute("""
                UPDATE members
                SET status = 'left', left_at = ?, updated_at = ?
                WHERE user_id = ?
            """, (now_str, now_str, user_id))
        else:
            # Agar record me pehle se nahi tha, to naya entry banao left status ke sath
            cursor.execute("""
                INSERT INTO members (user_id, first_name, last_name, username, status, joined_at, left_at, updated_at)
                VALUES (?, ?, ?, ?, 'left', 'Unknown', ?, ?)
            """, (user_id, first_name, last_name, username, now_str, now_str))
        conn.commit()

def get_stats():
    """Group ke current stats nikalta hai."""
    with get_connection() as conn:
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
    with get_connection() as conn:
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

    # Header columns
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

    # Header styling
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

    # Fetch data
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, first_name, last_name, username, phone, status, joined_at, left_at, updated_at
            FROM members
            ORDER BY status ASC, updated_at DESC
        """)
        rows = cursor.fetchall()

    active_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid") # light green
    left_fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")   # light red/orange

    for row_idx, row_data in enumerate(rows, start=2):
        for col_idx, value in enumerate(row_data, start=1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.value = value if value is not None else ""
            cell.border = thin_border
            cell.alignment = align_left

            # Highlight status column
            if col_idx == 6:
                cell.alignment = align_center
                if str(value).lower() == "active":
                    cell.fill = active_fill
                else:
                    cell.fill = left_fill

    # Auto-adjust column widths
    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    wb.save(filepath)
    return filepath
