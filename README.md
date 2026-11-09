# 🤖 Telegram Group Member Tracker & Analytics

Yeh system aapke Telegram group ke **sab puraane members** ko scan karke save karta hai, aur uske baad **real-time me har Join aur Leave ko live track** karta hai.

---

## 🌟 Mukhya Features:
1. **Existing Members One-Click Scan:** Puraane saare members ko database aur Excel sheet me export karta hai.
2. **Strict Bot Filter:** Group ke kisi bhi bot ko record nahi karega — sirf **real human members** ko track karega.
3. **Live Join & Leave Detection:** Jab koi join ya leave karega, turant database me status aur timestamp update hoga.
4. **Excel Export (`/export`):** Kisi bhi waqt Excel file generate karke send kar deta hai.
5. **Admin Commands:** `/stats`, `/left`, `/export` sirf Admins chala sakte hain.

---

## 🛠️ Setup Guide:

### Step 1: Libraries Install Karein
Command prompt / terminal me run karein:
```bash
pip install -r requirements.txt
```

---

### Step 2: Puraane Members Scan Karein (`scan_existing.py`)
1. [my.telegram.org](https://my.telegram.org) par ja kar apna **API ID** aur **API HASH** prapt karein.
2. File run karein:
   ```bash
   python scan_existing.py
   ```
3. Script aapse `API ID`, `API HASH`, aur `Group username` mangegi.
4. Scan khatam hote hi saare human members `members.db` me save ho jayenge aur ek `existing_members_scanned.xlsx` Excel file generate ho jayegi.

---

### Step 3: Live Bot Chalayein (`bot.py`)
1. Telegram par `@BotFather` se bot create karein aur **Bot Token** lein.
2. Apne group me us Bot ko add karein aur use **Administrator** banayein.
3. Script run karein:
   ```bash
   python bot.py
   ```
4. Bot live ho jayega aur 24/7 background me Join aur Leave monitor karega!

---

## 💬 Bot Commands (Group ya Bot ke DM me):
- `/stats` - Active aur Left members ka total count dekhein.
- `/left` - Haal hi me group chhodne walo ki list dekhein.
- `/export` - Sabhi members ka pura report (Excel File) download karein.
