import asyncio
import logging
import hashlib
import json
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime

from database.db import init_db, get_all_users, update_user_hash
from services.api_client import SvitloApiClient
from services.image_generator import convert_api_to_half_list
from handlers import registration

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
_LOGGER = logging.getLogger(__name__)

# Завантаження змінних середовища
env_path = os.path.join(os.path.dirname(__file__), '.env')
_LOGGER.info(f"Loading environment variables from: {env_path}")
loaded = load_dotenv(env_path)
_LOGGER.info(f"load_dotenv() result: {loaded}")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHECK_INTERVAL_STR = os.getenv("CHECK_INTERVAL", "30")
CHECK_INTERVAL = int(CHECK_INTERVAL_STR)

if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
    _LOGGER.error(f"BOT_TOKEN is invalid or missing! Value: {repr(BOT_TOKEN)}")
    exit(1)

_LOGGER.info(f"Bot token loaded (starts with: {str(BOT_TOKEN)[:5]}...)")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
api_client = SvitloApiClient()
scheduler = AsyncIOScheduler()

async def check_updates():
    """
    Періодична перевірка оновлень розкладу.
    """
    _LOGGER.info("Checking for updates...")
    users = await get_all_users()
    
    # Кешуємо результати API за (region, queue)
    cache = {}
    
    for user in users:
        tg_id, region_id, queue_id, last_hash = user
        
        cache_key = (region_id, queue_id)
        if cache_key not in cache:
            schedule_data = await api_client.fetch_schedule(region_id, queue_id)
            if schedule_data:
                # Створюємо хеш розкладу
                sched_str = json.dumps(schedule_data["schedule"], sort_keys=True)
                new_hash = hashlib.md5(sched_str.encode()).hexdigest()
                cache[cache_key] = (schedule_data, new_hash)
            else:
                continue
        
        schedule_data, new_hash = cache[cache_key]
        
        if new_hash != last_hash:
            _LOGGER.info(f"Schedule changed for user {tg_id} (Queue {queue_id})")
            
            try:
                await bot.send_message(tg_id, "🔔 Розклад оновився!")
                # Використовуємо універсальну функцію з registration для відправки графіку
                from handlers.registration import send_schedule
                await send_schedule(bot, tg_id) # Передаємо bot замість message для фонових задач
                await update_user_hash(tg_id, new_hash)
            except Exception as e:
                _LOGGER.error(f"Failed to notify user {tg_id}: {e}")

async def main():
    # Ініціалізація БД
    await init_db()
    
    # Реєстрація роутерів
    dp.include_router(registration.router)
    
    # Налаштування планувальника
    _LOGGER.info(f"Starting scheduler with interval {CHECK_INTERVAL} minutes")
    scheduler.add_job(check_updates, "interval", minutes=CHECK_INTERVAL)
    scheduler.start()
    
    _LOGGER.info("Starting bot polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        _LOGGER.info("Bot stopped")
