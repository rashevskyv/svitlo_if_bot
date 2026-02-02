import asyncio
import logging
import hashlib
import json
import os
import aiohttp
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile, InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
from typing import Optional

from database.db import init_db, get_all_users, update_user_hash
from services.api_client import SvitloApiClient
from services.utils import is_now_quiet_hours
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

CURRENT_ANNOUNCEMENT_ID = "forecast_update_2026_02"

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
    Improved version of is_change_relevant that checks all available dates.
    """
    if not old_sched:
        _LOGGER.debug(f"is_change_relevant: old_sched is None, returning True (notifying on hash change after restart/history loss)")
        return True
    
    # Перевірка зміни статусу аварії
    if old_sched.get("is_emergency") != new_sched.get("is_emergency"):
        _LOGGER.info(f"is_change_relevant: Emergency status changed: {old_sched.get('is_emergency')} -> {new_sched.get('is_emergency')}")
        return True

    from services.image_generator import convert_api_to_half_list
    
    def get_sched_for_date(sched_obj, date_str):
        if sched_obj.get("date_today") == date_str:
            return convert_api_to_half_list(sched_obj["schedule"].get(date_str, {}))
        if sched_obj.get("date_tomorrow") == date_str:
            return convert_api_to_half_list(sched_obj["schedule"].get(date_str, {}))
        if date_str in sched_obj.get("schedule", {}):
            return convert_api_to_half_list(sched_obj["schedule"][date_str])
        return ["unknown"] * 48

    def filter_unknown(sched):
        return [None if s == "unknown" else s for s in sched]

    current_idx = current_dt.hour * 2 + (1 if current_dt.minute >= 30 else 0)
    today_iso = current_dt.date().isoformat()
    
    # ЛОГІКА ПЕРЕВІРКИ ВСІХ ДОСТУПНИХ ДАТ
    new_dates = sorted(new_sched["schedule"].keys())
    for date_str in new_dates:
        # Для сьогоднішнього дня перевіряємо тільки майбутнє
        if date_str == today_iso:
            start_idx = current_idx
        elif date_str < today_iso:
            continue # Минуле не цікавить
        else:
            start_idx = 0 # Майбутні дні перевіряємо повністю
            
        old_day = filter_unknown(get_sched_for_date(old_sched, date_str))[start_idx:]
        new_day = filter_unknown(get_sched_for_date(new_sched, date_str))[start_idx:]
        
        for i, (o, n) in enumerate(zip(old_day, new_day)):
            if n is not None and n != o:
                _LOGGER.info(f"is_change_relevant: Found change on {date_str} at idx {start_idx + i}: {o} -> {n}")
                return True

    # Окрема логіка для динамічного режиму (сліпа зона опівночі)
    if mode == "dynamic" and last_update_dt and current_dt.date() > last_update_dt.date():
        yesterday_iso = last_update_dt.date().isoformat()
        yesterday_sched = get_sched_for_date(old_sched, yesterday_iso)
        last_hour_idx = last_update_dt.hour * 2 + (1 if last_update_dt.minute >= 30 else 0)
        yesterday_tail = yesterday_sched[last_hour_idx:]
        
        new_today_sched = get_sched_for_date(new_sched, today_iso)
        today_tail = new_today_sched[last_hour_idx:]
        
        if any(s in ["off", "possible"] for s in today_tail) and today_tail != yesterday_tail:
            _LOGGER.info(f"is_change_relevant: Midnight trigger (dynamic).")
            return True
            
    _LOGGER.debug(f"is_change_relevant: No relevant changes found for mode {mode}")
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
        _LOGGER.info("No regions changed, checking for catch-up updates.")

    bot_info = await bot.get_me()
    bot_username = bot_info.username

    img_cache = ImageCache()
    
    # Словник для мапінгу CPU -> region_id (з REGIONS)
    cpu_to_region_id = {API_REGION_MAP.get(rid, rid): rid for rid in REGIONS.keys()}

    for cpu in changed_region_cpus:
        region_id = cpu_to_region_id.get(cpu)
        if not region_id:
            _LOGGER.debug(f"Changed region CPU '{cpu}' not found in REGIONS map, ignoring.")
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

    # 2. Перевіряємо ВСІХ користувачів на необхідність оновлення (catch-up)
    from database.db import get_all_users
    all_users = await get_all_users()
    _LOGGER.info(f"Checking updates for {len(all_users)} users...")
    
    for user in all_users:
            # user: (tg_id, region_id, queue_id, hash, mode, rem, last_rem, last_upd, notif_en, qh_s, qh_e, qh_silent, last_status_rem, last_ann)
            tg_id, region_id, queue_id_json, last_hash, mode, _, _, last_update_str, notif_enabled, qh_start, qh_end, qh_silent, last_status_rem_str, _ = user
            
            now_dt = datetime.now()

            # --- ПЕРЕВІРКА НАГАДУВАННЯ ПРО ВИМКНЕНІ СПОВІЩЕННЯ (РАЗ НА 2 ДОБИ) ---
            if not notif_enabled:
                should_remind = False
                if not last_status_rem_str:
                    should_remind = True
                else:
                    try:
                        last_rem_dt = datetime.fromisoformat(last_status_rem_str)
                        if (now_dt - last_rem_dt).total_seconds() > 2 * 24 * 3600:
                            should_remind = True
                    except: should_remind = True
                
                if should_remind:
                    from database.db import update_user_status_reminder
                    await update_user_status_reminder(tg_id, now_dt.isoformat())
                    buttons = [
                        [InlineKeyboardButton(text="🔔 Увімкнути сповіщення", callback_data="enable_notifs")],
                        [InlineKeyboardButton(text="🔕 Лишити вимкненими", callback_data="keep_notifs_off")]
                    ]
                    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
                    try:
                        await bot.send_message(
                            tg_id, 
                            "👋 Привіт! Ваші сповіщення про оновлення графіка **вимкнені** вже понад 2 доби.\nБажаєте увімкнути їх знову?",
                            reply_markup=keyboard,
                            parse_mode="Markdown"
                        )
                    except: pass
                continue # Якщо сповіщення вимкнені, оновлення не слати далі

            # --- ПЕРЕВІРКА ТИХИХ ГОДИН ---
            in_qh = is_now_quiet_hours(qh_start, qh_end, now_dt)
            if in_qh:
                if not qh_silent:
                    _LOGGER.info(f"User {tg_id} is in quiet hours ({qh_start}-{qh_end}) and 'Skip' mode is active. Skipping notification.")
                    continue
                silent = True
                _LOGGER.info(f"User {tg_id} is in quiet hours ({qh_start}-{qh_end}). Sending silent notification.")
            else:
                silent = False

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
                    # Отримуємо старий та новий розклади синхронно з одного стану клієнта
                    old_s = await api_client.get_old_schedule(region_id, q["id"])
                    new_s = await api_client.fetch_schedule(region_id, q["id"])
                    
                    # Якщо старий розклад недоступний або ідентичний новому (через перезапис кешу),
                    # а хеш змінився - значить зміни були, але ми втратили "попередній" стан.
                    # В такому випадку обов'язково вважаємо зміни релевантними, щоб не пропустити оновлення.
                    if not old_s or (old_s and new_s and old_s["schedule"] == new_s["schedule"]):
                        _LOGGER.info(f"History lost or unavailable for user {tg_id} (queue {q['id']}), forcing relevance.")
                        is_relevant = True
                        break
                        
                    if new_s and is_change_relevant(old_s, new_s, mode, now_dt, last_update_dt):
                        is_relevant = True
                        break
                    else:
                        _LOGGER.debug(f"Change for queue {q['id']} deemed irrelevant for user {tg_id}")
                
                if not is_relevant and last_hash is not None:
                    _LOGGER.info(f"Skipping notification for user {tg_id} (irrelevant changes for mode {mode}). Status unchanged in relevant time slots.")
                    await update_user_hash(tg_id, new_hash)
                    continue

                if last_hash is not None:
                    _LOGGER.info(f"Notifying user {tg_id} about schedule change (is_relevant={is_relevant}, mode={mode}, silent={silent})")
                    try:
                        await bot.send_message(tg_id, "🔔 Розклад оновився!", disable_notification=silent)
                        await send_schedule(bot, tg_id, silent=silent)
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
    
    # Feature Announcements at 12:00
    scheduler.add_job(broadcast_announcements, "cron", hour=12, minute=0)
    
    scheduler.start()
    
    # Негайна перевірка при старті
    _LOGGER.info("Performing initial update check on startup...")
    await check_updates()
    
    _LOGGER.info("Starting bot polling...")
    try:
        await dp.start_polling(bot)
    finally:
        await session.close()

async def broadcast_announcements():
    from database.db import get_all_users, update_user_last_announcement
    users = await get_all_users()
    _LOGGER.info(f"Checking for feature announcements (ID: {CURRENT_ANNOUNCEMENT_ID})...")
    
    count = 0
    for user in users:
        # user: (tg_id, region_id, queue_id_json, hash, mode, rem, last_rem, last_upd, notif_en, qh_s, qh_e, last_status_rem, last_ann)
        tg_id = user[0]
        mode = user[4]
        last_ann_id = user[12] if len(user) > 12 else None
        
        # Надсилаємо тільки тим, хто використовує режим "Прогноз" (dynamic)
        if last_ann_id != CURRENT_ANNOUNCEMENT_ID and mode == "dynamic":
            text = (
                "🆕 **Оновлення відображення графіка!**\n\n"
                "Ми покращили ваш режим «Коло (Прогноз)», зробивши його ще інформативнішим:\n"
                "• 📈 **«Наскрізний» прогноз** — Тепер завтрашні відключення, які раніше були «сховані» під поточним днем, відображаються **зовнішньою дугою**. Ви бачитимете їх заздалегідь!\n"
                "• 📝 **Детальні інтервали** — До опису під графіком додано чіткий текстовий список усіх часових проміжків відключень на сьогодні та завтра.\n\n"
                "Ці оновлення вже активовані для вашого профілю! 🚀"
            )
            try:
                await bot.send_message(tg_id, text, parse_mode="Markdown")
                await update_user_last_announcement(tg_id, CURRENT_ANNOUNCEMENT_ID)
                count += 1
                await asyncio.sleep(0.05) # Rate limiting
            except Exception as e:
                _LOGGER.warning(f"Failed to send announcement to user {tg_id}: {e}")
    
    if count > 0:
        _LOGGER.info(f"Sent {count} announcements.")

@dp.callback_query(F.data == "enable_notifs")
async def process_enable_notifs(callback: CallbackQuery):
    from database.db import update_user_notifications
    await update_user_notifications(callback.from_user.id, 1)
    await callback.message.edit_text("✅ Сповіщення увімкнено! Тепер ви отримуватимете оновлення графіка.")
    await callback.answer()

@dp.callback_query(F.data == "keep_notifs_off")
async def process_keep_notifs_off(callback: CallbackQuery):
    await callback.message.edit_text("👌 Зрозумів. Сповіщення залишаються вимкненими. Я запитаю вас знову через 2 дні.")
    await callback.answer()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        _LOGGER.info("Bot stopped")
