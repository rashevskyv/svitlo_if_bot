import asyncio
import logging
import json
from datetime import datetime, timedelta
from aiogram import Bot
from database.db import get_all_users, update_user_last_reminder
from services.api_client import SvitloApiClient
from services.image_generator import convert_api_to_half_list

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
        tg_id, region_id, queue_id_json, _, _, reminder_min, last_rem = user
        
        if not reminder_min or reminder_min <= 0:
            continue
            
        try:
            queues = json.loads(queue_id_json)
            if not isinstance(queues, list):
                queues = [{"id": str(queue_id_json), "alias": str(queue_id_json)}]
        except:
            queues = [{"id": queue_id_json, "alias": queue_id_json}]
            
        for q in queues:
            schedule_data = await api_client.fetch_schedule(region_id, q["id"])
            if not schedule_data:
                continue
                
            today_half = convert_api_to_half_list(schedule_data["schedule"].get(schedule_data["date_today"], {}))
            tomorrow_half = convert_api_to_half_list(schedule_data["schedule"].get(schedule_data["date_tomorrow"], {}))
            
            all_half = today_half + tomorrow_half
            
            # Знаходимо найближче відключення
            # Поточний індекс у списку 48+48 сегментів
            current_idx = now.hour * 2 + (1 if now.minute >= 30 else 0)
            
            next_off_idx = -1
            for i in range(current_idx, len(all_half)):
                if all_half[i] == "off":
                    # Перевіряємо, чи це початок блоку відключення
                    if i == 0 or all_half[i-1] != "off":
                        next_off_idx = i
                        break
            
            if next_off_idx != -1:
                # Час початку відключення
                off_hour = next_off_idx // 2
                off_min = (next_off_idx % 2) * 30
                
                # Дата відключення (сьогодні або завтра)
                off_date = now.date() if next_off_idx < 48 else now.date() + timedelta(days=1)
                off_time = datetime.combine(off_date, datetime.min.time()) + timedelta(hours=off_hour, minutes=off_min)
                
                # Скільки залишилося до відключення
                diff = (off_time - now).total_seconds() / 60
                
                # Унікальний ідентифікатор цього відключення для запобігання дублів
                event_id = f"{q['id']}_{off_time.strftime('%Y%m%d%H%M')}"
                
                if 0 < diff <= reminder_min:
                    if last_rem != event_id:
                        _LOGGER.info(f"Sending reminder to {tg_id} for event {event_id}")
                        try:
                            msg = f"⚠️ **Нагадування!**\nЧерез {int(diff)} хв очікується відключення світла за чергою **{q['alias']}** ({off_time.strftime('%H:%M')})."
                            await bot.send_message(tg_id, msg, parse_mode="Markdown")
                            await update_user_last_reminder(tg_id, event_id)
                        except Exception as e:
                            _LOGGER.error(f"Failed to send reminder to {tg_id}: {e}")
                elif diff > reminder_min + 30:
                    # Якщо до відключення ще далеко, можна очистити last_rem (необов'язково, але корисно для логіки)
                    pass
