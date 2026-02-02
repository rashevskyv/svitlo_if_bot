import asyncio
import logging
import json
from datetime import datetime, timedelta
from aiogram import Bot
from database.db import get_all_users, update_user_last_reminder
from services.api_client import SvitloApiClient
from services.image_generator import convert_api_to_half_list
from services.utils import is_now_quiet_hours

_LOGGER = logging.getLogger(__name__)

async def check_reminders(bot: Bot, api_client: SvitloApiClient):
    """
    Перевіряє всіх користувачів на наявність майбутніх відключень 
    та надсилає нагадування за N хвилин.
    """
    _LOGGER.debug("Checking reminders...")
    users = await get_all_users()
    now = datetime.now()
    
    for user in users:
        # user: (tg_id, region_id, queue_id_json, last_hash, mode, reminder_min, last_rem, last_upd, notif_enabled, qh_start, qh_end, last_status_rem, last_ann)
        tg_id, region_id, queue_id_json, _, _, reminder_min, last_rem, _, notif_enabled, qh_start, qh_end, _, _ = user
        
        if not notif_enabled or not reminder_min or reminder_min <= 0:
            continue
            
        silent = is_now_quiet_hours(qh_start, qh_end, now)
        if silent:
            _LOGGER.info(f"User {tg_id} is in quiet hours ({qh_start}-{qh_end}). Reminder will be silent.")
            
        try:
            queues_data = json.loads(queue_id_json)
            if not isinstance(queues_data, list):
                queues_data = [{"id": str(queue_id_json), "alias": str(queue_id_json)}]
        except:
            queues_data = [{"id": queue_id_json, "alias": queue_id_json}]
            
        # Групуємо відключення за часом: {off_time: [alias1, alias2]}
        upcoming_off_events = {}
        
        for q in queues_data:
            schedule_data = await api_client.fetch_schedule(region_id, q["id"])
            if not schedule_data:
                continue
                
            today_half = convert_api_to_half_list(schedule_data["schedule"].get(schedule_data["date_today"], {}))
            tomorrow_half = convert_api_to_half_list(schedule_data["schedule"].get(schedule_data["date_tomorrow"], {}))
            all_half = today_half + tomorrow_half
            
            # Поточний індекс (кожні 30 хв)
            current_idx = now.hour * 2 + (1 if now.minute >= 30 else 0)
            
            # Шукаємо ПЕРШЕ відключення від зараз
            for i in range(current_idx, len(all_half)):
                if all_half[i] == "off":
                    if i == 0 or all_half[i-1] != "off":
                        off_hour = (i % 48) // 2
                        off_min = (i % 2) * 30
                        off_date = now.date() if i < 48 else now.date() + timedelta(days=1)
                        off_time = datetime.combine(off_date, datetime.min.time()) + timedelta(hours=off_hour, minutes=off_min)
                        
                        if off_time not in upcoming_off_events:
                            upcoming_off_events[off_time] = []
                        upcoming_off_events[off_time].append(q["alias"])
                        break 

        if not upcoming_off_events:
            continue

        # Знаходимо найближчий час відключення серед усіх черг
        earliest_off_time = min(upcoming_off_events.keys())
        diff_min = (earliest_off_time - now).total_seconds() / 60
        
        # Ідентифікатор нагадування базується ТІЛЬКИ на часі, щоб уникнути спаму при декількох чергах
        event_id = earliest_off_time.strftime("%Y%m%d%H%M")
        
        if 0 < diff_min <= reminder_min:
            if last_rem != event_id:
                relevant_aliases = upcoming_off_events[earliest_off_time]
                aliases_str = ", ".join([f"**{a}**" for a in relevant_aliases])
                queue_word = "чергою" if len(relevant_aliases) == 1 else "чергами"
                
                _LOGGER.info(f"Sending reminder to {tg_id} for time {event_id} (queues: {relevant_aliases})")
                
                try:
                    msg = f"⚠️ **Нагадування!**\nЧерез {int(diff_min)} хв очікується відключення світла за {queue_word} {aliases_str} ({earliest_off_time.strftime('%H:%M')})."
                    await bot.send_message(tg_id, msg, parse_mode="Markdown", disable_notification=silent)
                    await update_user_last_reminder(tg_id, event_id)
                except Exception as e:
                    err_msg = str(e).lower()
                    if "forbidden" in err_msg or "chat not found" in err_msg:
                        _LOGGER.warning(f"User {tg_id} blocked the bot. Removing.")
                        from database.db import DB_PATH
                        import aiosqlite
                        async with aiosqlite.connect(DB_PATH) as db:
                            await db.execute("DELETE FROM users WHERE telegram_id = ?", (tg_id,))
                            await db.commit()
                    else:
                        _LOGGER.error(f"Failed to send reminder to {tg_id}: {e}")
            else:
                _LOGGER.debug(f"Reminder for {tg_id} at {event_id} already sent.")
