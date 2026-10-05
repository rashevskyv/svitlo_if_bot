import logging
import time
from typing import Any
from datetime import datetime
from aiogram.types import BufferedInputFile, InputMediaPhoto
from database.db import get_user
from services.utils import escape_markdown

_LOGGER = logging.getLogger(__name__)

async def _timed_send(operation, tg_id, queue):
    started = time.perf_counter()
    try:
        return await operation
    finally:
        _LOGGER.info("Telegram schedule send user=%s queue=%s took %.3fs",
                     tg_id, queue, time.perf_counter() - started)

async def _send_schedule(target: Any, tg_id: int, api_client, silent: bool = False):
    """
    Універсальна функція для відправки графіку.
    Використовує ImageCache для classic/list режимів.
    """
    from services.image_generator import generate_schedule_image_async, convert_api_to_half_list, get_next_event_info, is_schedule_empty
    from services.image_cache import ImageCache
    from aiogram.types import Message
    import hashlib
    import json
    from database.db import update_user_hash

    _LOGGER.info(f"Attempting to send schedule for user {tg_id} (silent={silent})")
    user = await get_user(tg_id)
    if not user:
        _LOGGER.warning(f"User {tg_id} not found in database")
        if isinstance(target, Message):
            await target.answer("Ви ще не зареєстровані. Будь ласка, скористайтеся командою /start")
        return

    # user: (tg_id, region_id, queue_id_json, hash, mode, rem_min, last_rem, last_upd, notif_en, qh_s, qh_e, qh_silent, last_status_rem, last_ann)
    _, region_id, queue_id_json, _, mode, _, _, _, notif_enabled, qh_start, qh_end, qh_silent, _, _ = user
    if not mode: mode = "classic"

    try:
        queues = json.loads(queue_id_json)
        if not isinstance(queues, list):
            queues = [{"id": str(queue_id_json), "alias": str(queue_id_json)}]
    except (ValueError, TypeError):
        queues = [{"id": queue_id_json, "alias": queue_id_json}]

    # Надсилаємо вступне повідомлення першим
    if hasattr(target, "answer"):
        from handlers.registration import get_main_keyboard
        await target.answer("Ось ваш актуальний графік:", reply_markup=get_main_keyboard(), disable_notification=silent)

    all_schedules = {}
    delivery_success = True
    unavailable = []
    img_cache = ImageCache()
    now_dt = datetime.now()

    for q in queues:
        started = time.perf_counter()
        try:
            schedule_data = await api_client.fetch_schedule(region_id, q["id"])
        finally:
            _LOGGER.info("Schedule data user=%s queue=%s took %.3fs",
                         tg_id, q["id"], time.perf_counter() - started)
        if not schedule_data:
            delivery_success = False
            unavailable.append(str(q.get("alias", q["id"])))
            continue

        all_schedules[q["id"]] = schedule_data["schedule"]
        sched_hash = hashlib.md5(json.dumps(schedule_data["schedule"], sort_keys=True).encode()).hexdigest()

        # Спробуємо взяти з кешу (тільки для classic та list)
        cached_images = None
        if mode in ["classic", "list"]:
            cached_images = img_cache.get(region_id, q["id"], mode, sched_hash)

        if cached_images:
            images_to_send = cached_images
        else:
            today_data = schedule_data["schedule"].get(schedule_data["date_today"], {})
            tomorrow_data = schedule_data["schedule"].get(schedule_data["date_tomorrow"], {})

            today_half = convert_api_to_half_list(today_data)
            tomorrow_half = convert_api_to_half_list(tomorrow_data)

            # В режимах classic/list приховуємо завтра зовсім, якщо там немає даних.
            # В режимі dynamic нам потрібні всі дані для коректного накладання шарів.
            if mode != "dynamic" and is_schedule_empty(tomorrow_half):
                tomorrow_half_for_gen = []
            else:
                tomorrow_half_for_gen = tomorrow_half

            # Для dynamic завжди показуємо маркер часу.
            # Для інших - ні (вони кешуються без маркера).
            show_marker = (mode == "dynamic")

            # Отримуємо назву регіону з REGIONS
            from services.api_client import REGIONS
            reg_name = REGIONS.get(region_id, "Unknown Region")

            # Отримуємо username бота
            bot = target.bot if hasattr(target, "bot") else None
            bot_username = None
            if bot:
                bot_info = await bot.get_me()
                bot_username = bot_info.username

            images_to_send = await generate_schedule_image_async(
                today_half, tomorrow_half_for_gen, now_dt, mode, q["id"],
                show_time_marker=show_marker,
                region_name=reg_name,
                bot_username=bot_username
            )

            # Кешуємо, якщо це не dynamic
            if mode in ["classic", "list"]:
                img_cache.set(region_id, q["id"], mode, sched_hash, images_to_send)

        # Формуємо текстовий прогноз
        today_half = convert_api_to_half_list(schedule_data["schedule"].get(schedule_data["date_today"], {}))
        tomorrow_half = convert_api_to_half_list(schedule_data["schedule"].get(schedule_data["date_tomorrow"], {}))
        forecast_text = get_next_event_info(today_half, tomorrow_half, now_dt)

        # Додаємо повідомлення про відсутність графіку на завтра
        if is_schedule_empty(tomorrow_half):
            forecast_text += "\n\n⚠️ **Графіку на завтра ще немає.**"

        # Додаємо час запиту та статус сповіщень в підпис
        timestamp_str = now_dt.strftime("%H:%M")

        notif_status_str = ""
        if not notif_enabled:
            notif_status_str = "\n⚠️ **Сповіщення вимкнені**"
        elif qh_start and qh_end:
            mode_desc = "без звуку" if qh_silent else "пропускаються"
            notif_status_str = f"\n🌙 Тихі години: **{qh_start}-{qh_end} ({mode_desc})**"

        queue_media = []
        for i, img_buf in enumerate(images_to_send):
            photo = BufferedInputFile(img_buf.getvalue(), filename=f"schedule_{q['id']}_{i}.png")
            # Додаємо підпис тільки до першого фото кожної черги
            alias = escape_markdown(q['alias'])
            caption = f"📍 **{alias}**\n{forecast_text}{notif_status_str}\n\n🕒 _Запитано о {timestamp_str}_" if i == 0 else None
            queue_media.append(InputMediaPhoto(media=photo, caption=caption, parse_mode="Markdown"))

        if not queue_media:
            delivery_success = False
            continue

        # Надсилаємо розклад для цієї черги
        if hasattr(target, "answer_photo"):
            if len(queue_media) > 1:
                await _timed_send(target.answer_media_group(queue_media, disable_notification=silent), tg_id, q["id"])
            else:
                await _timed_send(target.answer_photo(
                    queue_media[0].media,
                    caption=queue_media[0].caption,
                    parse_mode="Markdown",
                    disable_notification=silent
                ), tg_id, q["id"])
        elif hasattr(target, "send_photo"):
            if len(queue_media) > 1:
                await _timed_send(target.send_media_group(tg_id, queue_media, disable_notification=silent), tg_id, q["id"])
            else:
                await _timed_send(target.send_photo(
                    tg_id,
                    queue_media[0].media,
                    caption=queue_media[0].caption,
                    parse_mode="Markdown",
                    disable_notification=silent
                ), tg_id, q["id"])
        else:
            delivery_success = False

    # Оновлюємо хеш користувача
    delivery_success = delivery_success and bool(queues)
    if all_schedules and delivery_success:
        sched_str = json.dumps(all_schedules, sort_keys=True)
        new_hash = hashlib.md5(sched_str.encode()).hexdigest()
        await update_user_hash(tg_id, new_hash)
    else:
        if hasattr(target, "answer"):
            if unavailable:
                aliases = ", ".join(str(alias) for alias in unavailable)
                await target.answer(f"Не вдалося отримати розклад для черг: {aliases}. Спробуйте повторно налаштувати чергу через /start.")
            else:
                await target.answer("Не вдалося надіслати графік. Спробуйте ще раз.")
    return delivery_success

async def send_schedule(target: Any, tg_id: int, silent: bool = False, api_client=None):
    if api_client is None:
        from services.api_client import SvitloApiClient
        api_client = SvitloApiClient()
    started = time.perf_counter()
    try:
        result = await _send_schedule(target, tg_id, api_client, silent)
        _LOGGER.info("Schedule response for %s completed in %.3fs", tg_id, time.perf_counter() - started)
        return result
    except Exception as exc:
        _LOGGER.error("Schedule response for %s failed (%s) after %.3fs", tg_id, type(exc).__name__, time.perf_counter() - started)
        raise
