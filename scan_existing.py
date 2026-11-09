"""
scan_existing.py - Group ke sabhi puraane real members ko scan karta hai aur bots ko skip karta hai.
"""
import os
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient
import database

load_dotenv()

# Configuration
API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")
GROUP_TARGET = os.getenv("TELEGRAM_GROUP")  # e.g. "my_group_username" ya invite link ya chat ID

async def main():
    database.init_db()

    api_id = API_ID or input("Apna Telegram API ID darj karein: ").strip()
    api_hash = API_HASH or input("Apna Telegram API HASH darj karein: ").strip()
    group_target = GROUP_TARGET or input("Apne Telegram Group ka Username (@group_name) ya Link darj karein: ").strip()

    print("\n[+] Telegram Client se connect ho raha hai...")
    client = TelegramClient('session_scanner', int(api_id), api_hash)
    await client.start()

    print(f"[+] Group data scan shuru ho raha hai: {group_target}")
    
    total_found = 0
    bots_skipped = 0
    human_saved = 0

    try:
        group = await client.get_entity(group_target)
        
        async for user in client.iter_participants(group):
            total_found += 1

            # Strict Bot Filter: Agar user bot hai to skip karein
            if user.bot:
                bots_skipped += 1
                continue

            # Human member ko database me save karein
            database.save_scanned_member(
                user_id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                username=user.username,
                phone=getattr(user, 'phone', None)
            )
            human_saved += 1
            if human_saved % 50 == 0:
                print(f" -> {human_saved} human members database me save ho chuke hain...")

        print("\n" + "="*45)
        print("✅ Scan Safaltapoorvak Pura Hua!")
        print(f" -> Total Accounts Scan Huye : {total_found}")
        print(f" -> Bots Skip Huye (Ignored)   : {bots_skipped}")
        print(f" -> Real Human Members Saved   : {human_saved}")
        print("="*45)

        # Turant Excel file generate kar dena taaki aap dekh sakein
        excel_file = database.export_to_excel("existing_members_scanned.xlsx")
        print(f"\n📁 Initial Excel report generate ho gayi hai: {excel_file}")
        print("Ab aap 'bot.py' chala sakte hain live tracking ke liye.\n")

    except Exception as e:
        print(f"\n❌ Error scan ke waqt: {e}")
    finally:
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
