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
        # user: (tg_id, region_id, queue_id_json, hash, mode, rem_min, last_rem, last_upd, notif_en, qh_s, qh_e, qh_silent, last_status_rem, last_ann)
        tg_id, region_id, queue_id_json, _, _, reminder_min, last_rem, _, notif_enabled, qh_start, qh_end, qh_silent, _, _ = user
        
        if not notif_enabled or not reminder_min or reminder_min <= 0:
            continue
            
        in_qh = is_now_quiet_hours(qh_start, qh_end, now)
        if in_qh:
            if not qh_silent:
                _LOGGER.info(f"User {tg_id} is in quiet hours ({qh_start}-{qh_end}) and 'Skip' mode is active. Skipping reminder.")
                continue
            silent = True
            _LOGGER.info(f"User {tg_id} is in quiet hours ({qh_start}-{qh_end}). Reminder will be silent.")
        else:
            silent = False
            
        try:
            queues_data = json.loads(queue_id_json)
            if not isinstance(queues_data, list):
                queues_data = [{"id": str(queue_id_json), "alias": str(queue_id_json)}]
        except:
            queues_data = [{"id": queue_id_json, "alias": queue_id_json}]
            
        # Групуємо події за часом: {time: {'off': [aliases], 'on': [aliases]}}
        upcoming_events = {}
        
        for q in queues_data:
            schedule_data = await api_client.fetch_schedule(region_id, q["id"])
            if not schedule_data:
                continue
                
            today_half = convert_api_to_half_list(schedule_data["schedule"].get(schedule_data["date_today"], {}))
            tomorrow_half = convert_api_to_half_list(schedule_data["schedule"].get(schedule_data["date_tomorrow"], {}))
            all_half = today_half + tomorrow_half
            
            # Поточний індекс (кожні 30 хв)
            current_idx = now.hour * 2 + (1 if now.minute >= 30 else 0)
            
            # Шукаємо ПЕРШУ подію кожного типу від зараз
            found_off = False
            found_on = False
            
            for i in range(current_idx, len(all_half)):
                # Відключення
                if not found_off and all_half[i] in ["off", "possible"]:
                    if i == 0 or all_half[i-1] not in ["off", "possible"]:
                        off_hour = (i % 48) // 2
                        off_min = (i % 2) * 30
                        off_date = now.date() if i < 48 else now.date() + timedelta(days=1)
                        off_time = datetime.combine(off_date, datetime.min.time()) + timedelta(hours=off_hour, minutes=off_min)
                        
                        if off_time not in upcoming_events:
                            upcoming_events[off_time] = {'off': [], 'on': []}
                        upcoming_events[off_time]['off'].append(q["alias"])
                        found_off = True
                
                # Включення (restore)
                if not found_on and all_half[i] in ["on", "unknown"]:
                    # Включення — це перехід від off/possible до on/unknown
                    if i > 0 and all_half[i-1] in ["off", "possible"]:
                        on_hour = (i % 48) // 2
                        on_min = (i % 2) * 30
                        on_date = now.date() if i < 48 else now.date() + timedelta(days=1)
                        on_time = datetime.combine(on_date, datetime.min.time()) + timedelta(hours=on_hour, minutes=on_min)
                        
                        if on_time not in upcoming_events:
                            upcoming_events[on_time] = {'off': [], 'on': []}
                        upcoming_events[on_time]['on'].append(q["alias"])
                        found_on = True
                
                if found_off and found_on:
                    break

        if not upcoming_events:
            continue

        # Знаходимо найближчий час події серед усіх черг
        earliest_time = min(upcoming_events.keys())
        diff_min = (earliest_time - now).total_seconds() / 60
        
        events_at_time = upcoming_events[earliest_time]
        
        # Визначаємо пріоритетний тип події для ID (або комбінований)
        # Якщо в один час і вкл і викл (різні черги), генеруємо спільний ID
        event_types = []
        if events_at_time['off']: event_types.append("off")
        if events_at_time['on']: event_types.append("on")
        
        event_id = f"{'_'.join(event_types)}_{earliest_time.strftime('%Y%m%d%H%M')}"
        
        if 0 < diff_min <= reminder_min:
            if last_rem != event_id:
                _LOGGER.info(f"Sending reminder for {tg_id} at {earliest_time}: {events_at_time}")
                
                lines = []
                if events_at_time['off']:
                    aliases = ", ".join([f"**{a}**" for a in events_at_time['off']])
                    word = "чергою" if len(events_at_time['off']) == 1 else "чергами"
                    lines.append(f"🔴 очікується **відключення** світла за {word} {aliases}")
                
                if events_at_time['on']:
                    aliases = ", ".join([f"**{a}**" for a in events_at_time['on']])
                    word = "чергою" if len(events_at_time['on']) == 1 else "чергами"
                    lines.append(f"🟢 очікується **включення** світла за {word} {aliases}")
                
                try:
                    msg = f"⚠️ **Нагадування!**\nЧерез {int(diff_min)} хв ({earliest_time.strftime('%H:%M')}):\n" + "\n".join(lines)
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
