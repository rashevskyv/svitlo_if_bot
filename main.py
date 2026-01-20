import asyncio
import logging
import hashlib
import json
import os
import aiohttp
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime

from database.db import init_db, get_all_users, update_user_hash
from services.api_client import SvitloApiClient
from handlers.registration import send_schedule
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
CHECK_INTERVAL_STR = os.getenv("CHECK_INTERVAL", "10")
CHECK_INTERVAL = int(CHECK_INTERVAL_STR)

if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
    _LOGGER.error(f"BOT_TOKEN is invalid or missing! Value: {repr(BOT_TOKEN)}")
    exit(1)

_LOGGER.info(f"Bot token loaded (starts with: {str(BOT_TOKEN)[:5]}...)")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# Глобальні об'єкти, що ініціалізуються в main()
api_client = None
session = None

async def check_updates():
    """
    Періодична перевірка оновлень розкладу.
    """
    _LOGGER.info("Checking for updates...")
    users = await get_all_users()
    
    # Кешуємо результати API за (region, queue)
    cache = {}
    
    for user in users:
        # user: (tg_id, region_id, queue_id_json, last_hash, mode)
        tg_id, region_id, queue_id_json, last_hash, mode = user
        
        try:
            queues = json.loads(queue_id_json)
            if not isinstance(queues, list):
                queues = [{"id": str(queue_id_json), "alias": str(queue_id_json)}]
        except:
            # Fallback for old data
            queues = [{"id": queue_id_json, "alias": queue_id_json}]
        
        user_schedules = {}
        skip_user = False
        
        for q in queues:
            q_id = q["id"]
            cache_key = (region_id, q_id)
            if cache_key not in cache:
                schedule_data = await api_client.fetch_schedule(region_id, q_id)
                if schedule_data:
                    cache[cache_key] = schedule_data
                else:
                    # If any queue fails, we might want to skip or continue with others
                    # For now, let's just skip this queue
                    continue
            
            if cache_key in cache:
                user_schedules[q_id] = cache[cache_key]["schedule"]
        
        if not user_schedules:
            continue
            
        # Створюємо хеш всіх розкладів користувача
        sched_str = json.dumps(user_schedules, sort_keys=True)
        new_hash = hashlib.md5(sched_str.encode()).hexdigest()
        
        if new_hash != last_hash:
            dates = []
            for q_id, sched in user_schedules.items():
                dates.extend(sched.keys())
            unique_dates = sorted(list(set(dates)))
            _LOGGER.info(f"Schedule changed for user {tg_id}. Dates in schedule: {unique_dates}")
            
            try:
                await bot.send_message(tg_id, "🔔 Розклад оновився!")
                # send_schedule вже оновлює хеш у базі
                await send_schedule(bot, tg_id)
            except Exception as e:
                _LOGGER.error(f"Failed to notify user {tg_id}: {e}")

async def main():
    global api_client, session
    
    # Ініціалізація БД
    await init_db()
    
    # Ініціалізація мережевої сесії та клієнта
    session = aiohttp.ClientSession()
    api_client = SvitloApiClient(session=session)
    
    # Реєстрація роутерів
    dp.include_router(registration.router)
    
    # Налаштування планувальника
    _LOGGER.info(f"Starting scheduler with interval {CHECK_INTERVAL} minutes (aligned to absolute time)")
    scheduler.add_job(check_updates, "cron", minute=f"*/{CHECK_INTERVAL}")
    scheduler.start()
    
    _LOGGER.info("Starting bot polling...")
    try:
        await dp.start_polling(bot)
    finally:
        await session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        _LOGGER.info("Bot stopped")
