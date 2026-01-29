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
from typing import Optional

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

def is_change_relevant(old_sched: dict, new_sched: dict, mode: str, current_dt: datetime, last_update_dt: Optional[datetime] = None) -> bool:
    """
    Перевіряє, чи є зміни в розкладі релевантними для користувача залежно від режиму.
    Використовує абсолютні дати для порівняння, щоб уникнути помилкових спрацювань о 00:00.
    """
    if not old_sched: return True # Перший запуск
    
    # Перевірка зміни статусу аварії
    if old_sched.get("is_emergency") != new_sched.get("is_emergency"):
        return True

    from services.image_generator import convert_api_to_half_list
    
    new_date_today = new_sched["date_today"]
    new_date_tomorrow = new_sched["date_tomorrow"]
    
    def get_sched_for_date(sched_obj, date_str):
        """Допоміжна функція для отримання графіку за конкретну дату."""
        if sched_obj.get("date_today") == date_str:
            return convert_api_to_half_list(sched_obj["schedule"].get(date_str, {}))
        if sched_obj.get("date_tomorrow") == date_str:
            return convert_api_to_half_list(sched_obj["schedule"].get(date_str, {}))
        # Якщо дати немає в об'єкті (наприклад, старий графік за вчора), шукаємо прямо в schedule
        if date_str in sched_obj.get("schedule", {}):
            return convert_api_to_half_list(sched_obj["schedule"][date_str])
        return ["unknown"] * 48

    old_for_new_today = get_sched_for_date(old_sched, new_date_today)
    new_for_new_today = get_sched_for_date(new_sched, new_date_today)
    
    old_for_new_tomorrow = get_sched_for_date(old_sched, new_date_tomorrow)
    new_for_new_tomorrow = get_sched_for_date(new_sched, new_date_tomorrow)
    
    current_idx = current_dt.hour * 2 + (1 if current_dt.minute >= 30 else 0)
    
    def filter_unknown(sched):
        """Замінює 'unknown' на None для коректного порівняння."""
        return [None if s == "unknown" else s for s in sched]

    if mode == "dynamic":
        # ЛОГІКА ПІСЛЯ ОПІВНОЧІ (СЛІПА ЗОНА)
        if last_update_dt and current_dt.date() > last_update_dt.date():
            # Ми перейшли через 00:00. Користувач бачить "хвіст" вчорашнього дня
            # на місці сьогоднішнього вечора.
            last_hour_idx = last_update_dt.hour * 2 + (1 if last_update_dt.minute >= 30 else 0)
            
            # Вчорашній хвіст, який зараз відображається у користувача як майбутнє
            # (Отримуємо вчорашній графік із "старих" даних)
            yesterday_iso = last_update_dt.date().isoformat()
            yesterday_sched = get_sched_for_date(old_sched, yesterday_iso)
            yesterday_tail = yesterday_sched[last_hour_idx:]
            
            # Сьогоднішній графік на той самий період
            today_tail = new_for_new_today[last_hour_idx:]
            
            # Перевіряємо, чи є в сьогоднішньому хвості відключення
            has_blackouts_today = any(s in ["off", "possible"] for s in today_tail)
            
            # Якщо сьогодні там інша ситуація (не така як вчора) ТА є відключення -> оновлюємо
            if has_blackouts_today and today_tail != yesterday_tail:
                _LOGGER.info(f"Midnight trigger: today's tail has blackouts and differs from yesterday's tail.")
                return True

        # СТАНДАРТНА ЛОГІКА (ПОТОЧНЕ ВІКНО 24г)
        relevant_old = filter_unknown(old_for_new_today[current_idx:] + old_for_new_tomorrow[:current_idx])
        relevant_new = filter_unknown(new_for_new_today[current_idx:] + new_for_new_tomorrow[:current_idx])
        
        for o, n in zip(relevant_old, relevant_new):
            if n is not None and n != o:
                return True
        return False
    else:
        # Для classic та list релевантні зміни від зараз до кінця дня сьогодні
        # ТА весь день завтра
        relevant_old = filter_unknown(old_for_new_today[current_idx:] + old_for_new_tomorrow)
        relevant_new = filter_unknown(new_for_new_today[current_idx:] + new_for_new_tomorrow)
        
        for o, n in zip(relevant_old, relevant_new):
            if n is not None and n != o:
                return True
        return False

async def check_updates():
    """
    Періодична перевірка оновлень розкладу.
    Оптимізовано: спочатку перевіряємо змінені регіони, потім сповіщаємо користувачів.
    """
    from database.db import get_users_by_region, get_unique_queues_by_region
    from services.image_cache import ImageCache
    from services.image_generator import generate_schedule_image, convert_api_to_half_list
    from services.api_client import REGIONS, API_REGION_MAP
    
    _LOGGER.info("Checking for updates...")
    await api_client._refresh_cache()
    changed_region_cpus = api_client.get_changed_regions(reset=True)
    
    if not changed_region_cpus:
        _LOGGER.info("No regions changed.")
        return

    bot_info = await bot.get_me()
    bot_username = bot_info.username

    img_cache = ImageCache()
    
    # Словник для мапінгу CPU -> region_id (з REGIONS)
    cpu_to_region_id = {API_REGION_MAP.get(rid, rid): rid for rid in REGIONS.keys()}

    for cpu in changed_region_cpus:
        region_id = cpu_to_region_id.get(cpu)
        if not region_id:
            _LOGGER.warning(f"Changed region CPU '{cpu}' not found in REGIONS map")
            continue
            
        _LOGGER.info(f"Region '{region_id}' changed. Processing updates...")
        
        # 1. Очищуємо старий кеш зображень для цього регіону
        img_cache.clear_region(region_id)
        
        # 2. Знаходимо всі унікальні черги в цьому регіоні
        unique_queues = await get_unique_queues_by_region(region_id)
        _LOGGER.info(f"Pre-generating images for {len(unique_queues)} queues in {region_id}")
        
        # 3. Попередньо генеруємо зображення для всіх черг (classic та list)
        # Це робиться один раз на регіон, а не для кожного користувача
        for q_id in unique_queues:
            schedule_data = await api_client.fetch_schedule(region_id, q_id)
            if not schedule_data: continue
            
            today_half = convert_api_to_half_list(schedule_data["schedule"].get(schedule_data["date_today"], {}))
            tomorrow_half = convert_api_to_half_list(schedule_data["schedule"].get(schedule_data["date_tomorrow"], {}))
            
            # Хеш розкладу для ключа кешу
            sched_hash = hashlib.md5(json.dumps(schedule_data["schedule"], sort_keys=True).encode()).hexdigest()
            
            from services.image_generator import is_schedule_empty
            tomorrow_is_empty = is_schedule_empty(tomorrow_half)

            for mode in ["classic", "list"]:
                # Приховуємо завтра, якщо воно порожнє
                tomorrow_half_for_gen = [] if tomorrow_is_empty else tomorrow_half
                
                # Для кешу генеруємо БЕЗ часової відмітки
                images = generate_schedule_image(
                    today_half, tomorrow_half_for_gen, datetime.now(), mode, q_id, 
                    show_time_marker=False,
                    region_name=REGIONS.get(region_id),
                    bot_username=bot_username
                )
                img_cache.set(region_id, q_id, mode, sched_hash, images)

        # 4. Сповіщаємо користувачів цього регіону
        users = await get_users_by_region(region_id)
        for user in users:
            # Розпаковуємо значення (tg_id, region_id, queue_id, hash, mode, reminder, last_reminder, last_update)
            tg_id, _, queue_id_json, last_hash, mode, _, _, last_update_str = user
            
            last_update_dt = None
            if last_update_str:
                try:
                    last_update_dt = datetime.fromisoformat(last_update_str)
                except:
                    pass
            
            # Отримуємо актуальні розклади для всіх черг користувача
            try:
                queues = json.loads(queue_id_json)
                if not isinstance(queues, list):
                    queues = [{"id": str(queue_id_json), "alias": str(queue_id_json)}]
            except:
                queues = [{"id": queue_id_json, "alias": queue_id_json}]
            
            user_schedules = {}
            for q in queues:
                s_data = await api_client.fetch_schedule(region_id, q["id"])
                if s_data:
                    user_schedules[q["id"]] = s_data["schedule"]
            if not user_schedules: continue
            
            new_hash = hashlib.md5(json.dumps(user_schedules, sort_keys=True).encode()).hexdigest()
            
            if new_hash != last_hash:
                # Перевірка релевантності змін
                is_relevant = False
                now_dt = datetime.now()
                for q in queues:
                    old_s = await api_client.get_old_schedule(region_id, q["id"])
                    new_s = await api_client.fetch_schedule(region_id, q["id"])
                    
                    # Якщо старий розклад недоступний або ідентичний новому (через перезапис кешу),
                    # а хеш змінився - значить зміни були, але ми втратили "попередній" стан.
                    # В такому випадку вважаємо зміни релевантними.
                    if old_s and new_s and old_s["schedule"] == new_s["schedule"]:
                        _LOGGER.warning(f"Old schedule lost (cache overwritten) for user {tg_id}, assuming change is relevant.")
                        is_relevant = True
                        break
                        
                    if new_s and is_change_relevant(old_s, new_s, mode, now_dt, last_update_dt):
                        is_relevant = True
                        break
                
                if not is_relevant and last_hash is not None:
                    _LOGGER.info(f"Skipping notification for user {tg_id} (irrelevant changes for mode {mode})")
                    await update_user_hash(tg_id, new_hash)
                    continue

                if last_hash is not None:
                    _LOGGER.info(f"Notifying user {tg_id} about schedule change")
                    try:
                        await bot.send_message(tg_id, "🔔 Розклад оновився!")
                        await send_schedule(bot, tg_id)
                    except Exception as e:
                        err_msg = str(e)
                        if "Forbidden: bot was blocked by the user" in err_msg or "chat not found" in err_msg:
                            _LOGGER.warning(f"User {tg_id} blocked the bot. Removing from DB.")
                            from database.db import DB_PATH
                            import aiosqlite
                            async with aiosqlite.connect(DB_PATH) as db:
                                await db.execute("DELETE FROM users WHERE telegram_id = ?", (tg_id,))
                                await db.commit()
                        else:
                            _LOGGER.error(f"Failed to notify user {tg_id}: {e}")
                
                # Завжди оновлюємо хеш, навіть якщо це перший запуск
                await update_user_hash(tg_id, new_hash)

async def main():
    global api_client, session
    
    # Ініціалізація БД
    await init_db()
    
    # Ініціалізація мережевої сесії та клієнта
    session = aiohttp.ClientSession()
    api_client = SvitloApiClient(session=session, cache_ttl=CHECK_INTERVAL * 60)
    
    # Реєстрація роутерів
    dp.include_router(registration.router)
    
    # Глобальний обробник помилок
    from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
    from aiogram.types import ErrorEvent
    
    @dp.error()
    async def global_error_handler(event: ErrorEvent):
        exception = event.exception
        if isinstance(exception, TelegramForbiddenError) or (isinstance(exception, TelegramBadRequest) and "chat not found" in str(exception).lower()):
            tg_id = None
            if event.update.message:
                tg_id = event.update.message.from_user.id
            elif event.update.callback_query:
                tg_id = event.update.callback_query.from_user.id
            
            if tg_id:
                _LOGGER.warning(f"User {tg_id} blocked the bot or chat not found. Removing from DB.")
                from database.db import DB_PATH
                import aiosqlite
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("DELETE FROM users WHERE telegram_id = ?", (tg_id,))
                    await db.commit()
            else:
                _LOGGER.warning(f"Telegram error (Forbidden/NotFound) but user ID not found in update: {exception}")
            return True # Помилка оброблена
        
        _LOGGER.error(f"Unhandled exception: {exception}", exc_info=True)
        return False
    
    # Налаштування планувальника
    _LOGGER.info(f"Starting scheduler with interval {CHECK_INTERVAL} minutes (aligned to absolute time)")
    scheduler.add_job(check_updates, "cron", minute=f"*/{CHECK_INTERVAL}")
    
    from services.reminder_service import check_reminders
    scheduler.add_job(check_reminders, "interval", minutes=1, args=[bot, api_client])
    
    scheduler.start()
    
    # Негайна перевірка при старті
    _LOGGER.info("Performing initial update check on startup...")
    await check_updates()
    
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
