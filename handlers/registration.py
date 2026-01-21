import logging
import re
import json
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, BufferedInputFile, InputMediaPhoto
from services.api_client import SvitloApiClient
from database.db import add_or_update_user, get_user
from services.image_generator import convert_api_to_half_list
from datetime import datetime
from typing import List, Dict, Any

router = Router()
api_client = SvitloApiClient()
_LOGGER = logging.getLogger(__name__)

def get_main_keyboard():
    buttons = [
        [KeyboardButton(text="📊 Поточний статус")],
        [KeyboardButton(text="⚙️ Змінити налаштування")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

class Registration(StatesGroup):
    waiting_for_macro_region = State()
    waiting_for_region = State()
    waiting_for_queue = State()
    waiting_for_settings_choice = State()
    waiting_for_display_mode = State()
    waiting_for_reminder_time = State()

# Ключові слова для групування областей (динамічно)
MACRO_GROUPS_KEYWORDS = {
    "Захід": ["Львів", "Франківськ", "Закарпат", "Тернопіль", "Хмельницьк", "Рівне", "Волин", "Чернівець"],
    "Центр та Північ": ["Київ", "Житомир", "Вінницьк", "Черкас", "Чернігів", "Полтав", "Кіровоград", "Сум"],
    "Південь": ["Одес", "Миколаїв", "Херсон", "Запорізьк"],
    "Схід": ["Харків", "Дніпро", "Донецьк", "Луганськ"]
}

async def get_grouped_regions():
    """Групує всі доступні області з api_client за макрорегіонами."""
    all_regions = await api_client.get_regions()
    grouped = {group: {} for group in MACRO_GROUPS_KEYWORDS}
    grouped["Інші"] = {}
    
    for reg_id, reg_name in all_regions.items():
        found = False
        for group, keywords in MACRO_GROUPS_KEYWORDS.items():
            if any(kw.lower() in reg_name.lower() for kw in keywords):
                grouped[group][reg_id] = reg_name
                found = True
                break
        if not found:
            grouped["Інші"][reg_id] = reg_name
            
    # Видаляємо порожні групи
    return {k: v for k, v in grouped.items() if v}

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    _LOGGER.info(f"User {message.from_user.id} started registration/restart")
    await state.clear() # Завжди очищуємо стан при /start
    
    grouped = await get_grouped_regions()
    await state.update_data(grouped_regions=grouped)
    
    # Створюємо клавіатуру з макрорегіонами
    buttons = [[KeyboardButton(text=name)] for name in grouped.keys()]
    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=True)
    
    await message.answer(
        "Привіт! Я бот для моніторингу світла.\n\n"
        "Виберіть ваш регіон або **введіть назву вашого міста/області вручну** (наприклад, Калуш, Київ, Львів):", 
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await state.set_state(Registration.waiting_for_macro_region)

# Пріоритетні обробники для головного меню (працюють навіть у станах реєстрації)
@router.message(F.text.contains("Поточний статус"))
async def priority_status(message: Message, state: FSMContext):
    await cmd_status(message, state)

@router.message(F.text.contains("Змінити налаштування"))
async def priority_settings(message: Message, state: FSMContext):
    await cmd_settings(message, state)

@router.message(Registration.waiting_for_macro_region)
async def process_macro_region(message: Message, state: FSMContext):
    user_input = message.text
    
    if user_input == "⬅️ Назад":
        await state.clear()
        await cmd_start(message, state)
        return

    data = await state.get_data()
    grouped = data.get("grouped_regions", {})
    if not grouped:
        grouped = await get_grouped_regions()
        await state.update_data(grouped_regions=grouped)

    all_regions = await api_client.get_regions()
    
    # 1. Перевірка, чи це макрорегіон
    if user_input in grouped:
        filtered_regions = grouped[user_input]
        await state.update_data(regions=all_regions, current_macro=user_input)
        
        buttons = [[KeyboardButton(text=name)] for name in filtered_regions.values()]
        buttons.append([KeyboardButton(text="⬅️ Назад")])
        keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=True)
        
        await message.answer(f"Вибрано: {user_input}. Тепер виберіть вашу область:", reply_markup=keyboard)
        await state.set_state(Registration.waiting_for_region)
        return

    # 2. Спроба знайти регіон за назвою (ручне введення)
    found_regions = {k: v for k, v in all_regions.items() if user_input.lower() in v.lower()}
    
    if len(found_regions) == 1:
        # Знайдено рівно один збіг - вибираємо його
        reg_id, reg_name = list(found_regions.items())[0]
        await state.update_data(region_id=reg_id, region_name=reg_name, regions=all_regions)
        
        buttons = [[KeyboardButton(text="⬅️ Назад")]]
        keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
        
        await message.answer(f"Знайдено: {reg_name}. Тепер введіть номер вашої черги (наприклад, 4.2 або 5):\n\n"
                             "Можна вказати декілька черг через кому та дати їм назви, наприклад:\n"
                             "`4 (Дім), 5.2 (Робота)`", 
                             reply_markup=keyboard,
                             parse_mode="Markdown")
        await state.set_state(Registration.waiting_for_queue)
    elif len(found_regions) > 1:
        # Знайдено декілька збігів - пропонуємо вибрати
        buttons = [[KeyboardButton(text=name)] for name in found_regions.values()]
        buttons.append([KeyboardButton(text="⬅️ Назад")])
        keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=True)
        await message.answer(f"Знайдено декілька варіантів за запитом '{user_input}'. Уточніть, будь ласка:", reply_markup=keyboard)
        await state.update_data(regions=all_regions)
        await state.set_state(Registration.waiting_for_region)
    else:
        await message.answer("На жаль, нічого не знайдено за таким запитом. Спробуйте вибрати зі списку або введіть іншу назву.")

@router.message(Registration.waiting_for_region)
async def process_region(message: Message, state: FSMContext):
    user_input = message.text
    
    if user_input == "⬅️ Назад":
        await cmd_start(message, state)
        return

    data = await state.get_data()
    regions = data.get("regions")
    if not regions:
        regions = await api_client.get_regions()
        await state.update_data(regions=regions)
    
    # Шукаємо ID регіону за назвою (точний збіг або підрядок)
    region_id = next((k for k, v in regions.items() if v == user_input), None)
    
    if not region_id:
        # Спробуємо знайти за підрядком, якщо точного збігу немає
        found = {k: v for k, v in regions.items() if user_input.lower() in v.lower()}
        if len(found) == 1:
            region_id, user_input = list(found.items())[0]
        else:
            await message.answer("Будь ласка, виберіть область зі списку або введіть назву точніше.")
            return
    
    await state.update_data(region_id=region_id, region_name=user_input)
    _LOGGER.info(f"User {message.from_user.id} selected region: {user_input} ({region_id})")
    
    buttons = [[KeyboardButton(text="⬅️ Назад")]]
    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    
    await message.answer(
        f"Ви вибрали: {user_input}.\n"
        f"Це охоплює всі міста та населені пункти цієї області.\n\n"
        f"Тепер введіть номер вашої черги (наприклад, 4.2 або 5):\n\n"
        "Можна вказати декілька черг через кому та дати їм назви, наприклад:\n"
        "`4 (Дім), 5.2 (Робота)`", 
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await state.set_state(Registration.waiting_for_queue)

def parse_queues(input_str: str) -> List[Dict[str, str]]:
    """
    Parses input string like "4, 5.2 (Work), 6 (Home)" into a list of dicts.
    """
    # Split by comma
    parts = [p.strip() for p in input_str.split(",")]
    result = []
    for part in parts:
        # Match "queue (alias)" or just "queue"
        match = re.match(r"^([\d.]+)\s*(?:\(([^)]+)\))?$", part)
        if match:
            q_id = match.group(1)
            alias = match.group(2) or q_id
            result.append({"id": q_id, "alias": alias})
        else:
            # Fallback for simple strings if regex fails
            result.append({"id": part, "alias": part})
    return result

@router.message(Registration.waiting_for_queue)
async def process_queue(message: Message, state: FSMContext):
    user_input = message.text.strip()
    
    if user_input == "⬅️ Назад":
        data = await state.get_data()
        # Повертаємось до вибору області в межах того ж макрорегіону
        macro = data.get("current_macro")
        if macro:
            message.text = macro
            await process_macro_region(message, state)
        else:
            await cmd_start(message, state)
        return

    queue_data = parse_queues(user_input)
    data = await state.get_data()
    region_id = data.get("region_id")
    
    # Перевірка хоча б однієї черги через API
    valid_queues = []
    ignored_queues = []
    for q in queue_data:
        schedule_data = await api_client.fetch_schedule(region_id, q["id"])
        if schedule_data:
            valid_queues.append(q)
        else:
            ignored_queues.append(q["id"])
    
    if not valid_queues:
        await message.answer("Не вдалося знайти розклад для жодної з вказаних черг. Перевірте правильність вводу та спробуйте ще раз.")
        return
    
    # Зберігаємо користувача
    await add_or_update_user(message.from_user.id, region_id, valid_queues)
    _LOGGER.info(f"User {message.from_user.id} registered with queues {valid_queues} in region {region_id}")
    
    queues_str = ", ".join([f"{q['id']} ({q['alias']})" if q['id'] != q['alias'] else q['id'] for q in valid_queues])
    msg = f"Ви успішно зареєстровані! Область: {data['region_name']}, Черги: {queues_str}."
    
    if ignored_queues:
        msg += f"\n\n⚠️ **Наступні черги були ігноровані (не знайдено в API):** {', '.join(ignored_queues)}"
        
    await message.answer(
        msg + "\n\nВи можете змінити вигляд графіку в меню 'Змінити налаштування'.\n\n"
        "Ось ваш поточний розклад:",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )
    
    # Відправляємо графік (за замовчуванням classic)
    await send_schedule(message, message.from_user.id)
    await state.clear()

@router.message(F.text == "⚙️ Змінити налаштування")
async def cmd_settings(message: Message, state: FSMContext):
    await state.clear() # Очищуємо стан, якщо користувач натиснув кнопку меню
    
    # Перевірка реєстрації
    user = await get_user(message.from_user.id)
    if not user:
        await cmd_start(message, state)
        return

    buttons = [
        [KeyboardButton(text="🌍 Змінити регіон/чергу")],
        [KeyboardButton(text="🎨 Змінити вигляд графіку")],
        [KeyboardButton(text="🔔 Налаштувати нагадування")],
        [KeyboardButton(text="⬅️ Назад")]
    ]
    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    await message.answer("Що саме ви хочете змінити?", reply_markup=keyboard)
    await state.set_state(Registration.waiting_for_settings_choice)

@router.message(Registration.waiting_for_settings_choice)
async def process_settings_choice(message: Message, state: FSMContext):
    choice = message.text
    
    if choice == "🌍 Змінити регіон/чергу":
        await cmd_start(message, state)
    elif choice == "🎨 Змінити вигляд графіку":
        # ... (existing logic for display mode)
        buttons = [
            [KeyboardButton(text="🕒 Коло (Доба)")],
            [KeyboardButton(text="🔮 Коло (Прогноз)")],
            [KeyboardButton(text="📝 Список")],
            [KeyboardButton(text="⬅️ Назад")]
        ]
        keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
        
        description = (
            "🎨 **Оберіть режим відображення графіку:**\n\n"
            "🕒 **Коло (Доба)**\n"
            "• Класичний вигляд на 24 години (00-23).\n"
            "• Зручно для планування всього дня.\n"
            "• Перемикання між сьогодні/завтра.\n\n"
            "🔮 **Коло (Прогноз)**\n"
            "• Показує 24 години вперед від **зараз**.\n"
            "• Стрілка показує поточний час.\n"
            "• Сектори після 00:00 — це вже ранок завтра.\n\n"
            "📝 **Список**\n"
            "• Текстові картки з інтервалами.\n"
            "• Тільки конкретний час відключень.\n"
            "• Легко читати тривалість."
        )
        await message.answer(description, reply_markup=keyboard, parse_mode="Markdown")
        await state.set_state(Registration.waiting_for_display_mode)
    elif choice == "🔔 Налаштувати нагадування":
        user = await get_user(message.from_user.id)
        current_rem = user[5] if user and len(user) > 5 else 0
        
        status_text = f"🔔 Зараз нагадування: **{'вимкнено' if current_rem == 0 else f'за {current_rem} хв'}**."
        
        buttons = [
            [KeyboardButton(text="❌ Вимкнути")],
            [KeyboardButton(text="5 хв"), KeyboardButton(text="10 хв"), KeyboardButton(text="15 хв")],
            [KeyboardButton(text="30 хв"), KeyboardButton(text="45 хв"), KeyboardButton(text="60 хв")],
            [KeyboardButton(text="⬅️ Назад")]
        ]
        keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
        
        await message.answer(
            f"{status_text}\n\n"
            "📌 **Ви можете обрати варіант з кнопок або просто вказати будь-яке число хвилин вручну.**\n\n"
            "Наприклад, просто напишіть `20` або `120`.",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        await state.set_state(Registration.waiting_for_reminder_time)
    elif choice == "⬅️ Назад":
        await message.answer("Повертаємось до головного меню.", reply_markup=get_main_keyboard())
        await state.clear()
    else:
        # Якщо користувач натиснув іншу кнопку (наприклад, з головного меню), 
        # але він у стані waiting_for_settings_choice - перенаправляємо
        if choice == "📊 Поточний статус":
            await cmd_status(message, state)
        else:
            await message.answer("Будь ласка, оберіть варіант з кнопок.")

@router.message(Registration.waiting_for_display_mode)
async def process_display_mode(message: Message, state: FSMContext):
    if message.text == "⬅️ Назад":
        await cmd_settings(message, state)
        return

    mode_map = {
        "🕒 Коло (Доба)": "classic",
        "🔮 Коло (Прогноз)": "dynamic",
        "📝 Список": "list"
    }
    
    user_mode = message.text
    if user_mode not in mode_map:
        if user_mode == "📊 Поточний статус":
            await cmd_status(message, state)
        else:
            await message.answer("Будь ласка, оберіть режим з кнопок.")
        return
        
    db_mode = mode_map[user_mode]
    from database.db import update_user_display_mode
    await update_user_display_mode(message.from_user.id, db_mode)
    
    await message.answer(
        f"Налаштування збережено! Режим: {user_mode}.",
        reply_markup=get_main_keyboard()
    )
    
    # Відправляємо оновлений графік
    await send_schedule(message, message.from_user.id)
    await state.clear()

@router.message(Registration.waiting_for_reminder_time)
async def process_reminder_time(message: Message, state: FSMContext):
    text = message.text
    
    if text == "⬅️ Назад":
        await cmd_settings(message, state)
        return
        
    if text == "❌ Вимкнути":
        minutes = 0
    else:
        # Витягуємо число з тексту (наприклад "15 хв" -> 15)
        match = re.search(r"(\d+)", text)
        if match:
            minutes = int(match.group(1))
            if minutes < 1 or minutes > 1440:
                await message.answer("Будь ласка, введіть число від 1 до 1440 (24 години).")
                return
        else:
            await message.answer("Будь ласка, введіть число хвилин (наприклад, 15) або оберіть варіант з кнопок.")
            return

    from database.db import update_user_reminder
    await update_user_reminder(message.from_user.id, minutes)
    
    if minutes == 0:
        await message.answer("Нагадування вимкнено.", reply_markup=get_main_keyboard())
    else:
        await message.answer(f"Налаштовано! Я нагадаю вам про відключення за **{minutes} хв**.", reply_markup=get_main_keyboard(), parse_mode="Markdown")
    
    await state.clear()

# Глобальний обробник для незареєстрованих користувачів
@router.message()
async def global_handler(message: Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    if not user:
        _LOGGER.info(f"Unregistered user {message.from_user.id} sent message: {message.text}. Redirecting to /start")
        await cmd_start(message, state)
        return
    
    # Якщо користувач зареєстрований, але ми тут - значить він натиснув щось не те або стан збився
    if message.text == "⬅️ Назад":
        await message.answer("Повертаємось до головного меню.", reply_markup=get_main_keyboard())
        await state.clear()
        return

    # Якщо це просто текст, який ми не знаємо як обробити
    await message.answer("Я вас не зрозумів. Будь ласка, скористайтеся кнопками меню.", reply_markup=get_main_keyboard())

async def send_schedule(target: Any, tg_id: int):
    """
    Універсальна функція для відправки графіку.
    Використовує ImageCache для classic/list режимів.
    """
    from services.image_generator import generate_schedule_image, convert_api_to_half_list, get_next_event_info, is_schedule_empty
    from services.image_cache import ImageCache
    from aiogram import Bot
    from aiogram.types import Message
    import hashlib
    import json
    from database.db import update_user_hash
    
    _LOGGER.info(f"Attempting to send schedule for user {tg_id}")
    user = await get_user(tg_id)
    if not user:
        _LOGGER.warning(f"User {tg_id} not found in database")
        if isinstance(target, Message):
            await target.answer("Ви ще не зареєстровані. Будь ласка, скористайтеся командою /start")
        return
    
    # user: (tg_id, region_id, queue_id_json, hash, mode, reminder_min, last_rem)
    _, region_id, queue_id_json, _, mode = user[:5]
    if not mode: mode = "classic"
    
    try:
        queues = json.loads(queue_id_json)
        if not isinstance(queues, list):
            queues = [{"id": str(queue_id_json), "alias": str(queue_id_json)}]
    except Exception as e:
        queues = [{"id": queue_id_json, "alias": queue_id_json}]
    
    all_schedules = {}
    img_cache = ImageCache()
    now_dt = datetime.now()
    
    for q in queues:
        schedule_data = await api_client.fetch_schedule(region_id, q["id"])
        if not schedule_data:
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
            
            # В режимі dynamic ми завжди показуємо 24 години вперед
            # В інших режимах приховуємо завтра, якщо там немає даних (тільки unknown або все green)
            tomorrow_is_empty = is_schedule_empty(tomorrow_half)
            
            if mode in ["classic", "list"] and tomorrow_is_empty:
                tomorrow_half_for_gen = []
            else:
                tomorrow_half_for_gen = tomorrow_half
            
            # Для dynamic завжди показуємо маркер часу. 
            # Для інших - ні (вони кешуються без маркера).
            show_marker = (mode == "dynamic")
            
            images_to_send = generate_schedule_image(
                today_half, tomorrow_half_for_gen, now_dt, mode, q["alias"], show_time_marker=show_marker
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
        
        # Додаємо час запиту в підпис
        timestamp_str = now_dt.strftime("%H:%M")
        
        queue_media = []
        for i, img_buf in enumerate(images_to_send):
            photo = BufferedInputFile(img_buf.getvalue(), filename=f"schedule_{q['id']}_{i}.png")
            # Додаємо підпис тільки до першого фото кожної черги
            caption = f"📍 **{q['alias']}**\n{forecast_text}\n\n🕒 _Запитано о {timestamp_str}_" if i == 0 else None
            queue_media.append(InputMediaPhoto(media=photo, caption=caption, parse_mode="Markdown"))
        
        if not queue_media:
            continue

        # Надсилаємо розклад для цієї черги
        if hasattr(target, "answer_photo"):
            if len(queue_media) > 1:
                await target.answer_media_group(queue_media)
            else:
                await target.answer_photo(
                    queue_media[0].media,
                    caption=queue_media[0].caption,
                    parse_mode="Markdown"
                )
        elif hasattr(target, "send_photo"):
            if len(queue_media) > 1:
                await target.send_media_group(tg_id, queue_media)
            else:
                await target.send_photo(
                    tg_id,
                    queue_media[0].media,
                    caption=queue_media[0].caption,
                    parse_mode="Markdown"
                )

    # Оновлюємо хеш користувача
    if all_schedules:
        sched_str = json.dumps(all_schedules, sort_keys=True)
        new_hash = hashlib.md5(sched_str.encode()).hexdigest()
        await update_user_hash(tg_id, new_hash)
        
        # Додаємо клавіатуру в кінці, якщо це повідомлення
        if hasattr(target, "answer"):
            from handlers.registration import get_main_keyboard
            await target.answer("Ось ваш актуальний графік:", reply_markup=get_main_keyboard())
    else:
        if hasattr(target, "answer"):
            await target.answer("Не вдалося отримати розклад для жодної з ваших черг.")

@router.message(F.text.contains("Поточний статус"))
@router.message(Command("status"))
async def cmd_status(message: Message, state: FSMContext):
    _LOGGER.info(f"Button 'Поточний статус' clicked by user {message.from_user.id}")
    await state.clear()
    await send_schedule(message, message.from_user.id)

# Глобальний обробник для всього іншого
@router.message()
async def global_handler(message: Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    text = message.text or ""
    
    # Якщо бот не розуміє — робимо /start (перезапуск реєстрації/меню)
    _LOGGER.info(f"Confused user {message.from_user.id} sent: {text}. Redirecting to /start as requested.")
    await state.clear()
    
    if not user:
        await message.answer("Ви ще не зареєстровані. Починаємо реєстрацію...")
    else:
        await message.answer("Я вас не зовсім зрозумів, тому перезапускаю головне меню...")
    
    await cmd_start(message, state)
