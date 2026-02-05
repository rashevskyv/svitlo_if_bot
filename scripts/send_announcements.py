import asyncio
import logging
import os
import sys

# Add parent directory to sys.path to import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot
from dotenv import load_dotenv
from database.db import get_all_users, update_user_last_announcement
from main import CURRENT_ANNOUNCEMENT_ID

# Setup logging
logging.basicConfig(level=logging.INFO)
_LOGGER = logging.getLogger(__name__)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

ANNOUNCEMENT_TEXT = (
    "🆕 **Оновлення: Ще більше корисної інформації!**\n\n"
    "Тепер бот став ще зручнішим:\n\n"
    "• **🟢 Сповіщення про включення** — ми додали нагадування про те, коли світло має з'явитися за графіком. Тепер ви будете готові до повернення електроенергії!\n\n"
    "• **🌙 Гнучкий «Тихий режим»** — обирайте, як бот поводитиметься під час тихих годин: **🔕 Без звуку** (тихі сповіщення) або **🚫 Не надсилати** (повна тиша).\n\n"
    "⚙️ **Як налаштувати?**\n"
    "Перейдіть у: **Змінити налаштування** -> **🔔 Сповіщення**.\n\n"
    "⚠️ **Увага!** Функціонал працює у тестовому режимі, тому можливі помилки. Дякуємо, що допомагаєте нам ставати кращими! ⚡️"
)

async def main():
    if not BOT_TOKEN:
        print("BOT_TOKEN not found in .env")
        return

    bot = Bot(token=BOT_TOKEN)
    users = await get_all_users()
    
    # Get ID from command line or use current
    ann_id = sys.argv[1] if len(sys.argv) > 1 else CURRENT_ANNOUNCEMENT_ID
    
    print(f"Starting announcement broadcast (ID: {ann_id})...")
    
    count = 0
    for user in users:
        tg_id = user[0]
        last_ann_id = user[13] if len(user) > 13 else None
        
        if last_ann_id != ann_id:
            try:
                await bot.send_message(tg_id, ANNOUNCEMENT_TEXT, parse_mode="Markdown")
                await update_user_last_announcement(tg_id, ann_id)
                count += 1
                if count % 20 == 0:
                    print(f"Sent {count} messages...")
                await asyncio.sleep(0.05) # Rate limiting
            except Exception as e:
                _LOGGER.warning(f"Failed to send announcement to user {tg_id}: {e}")
    
    print(f"Success! Sent {count} announcements.")
    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
