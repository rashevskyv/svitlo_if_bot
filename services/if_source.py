import asyncio
import json
import logging
import os
from datetime import datetime, timezone, timedelta

_LOGGER = logging.getLogger(__name__)

def source_timestamp(data):
    if not isinstance(data, dict) or not isinstance(data.get("schedules"), dict):
        return None
    try:
        for queue, days in data["schedules"].items():
            if not isinstance(queue, str) or not isinstance(days, dict):
                return None
            for day, grid in days.items():
                datetime.strptime(day, "%Y-%m-%d")
                if not isinstance(grid, dict):
                    return None
                for slot, status in grid.items():
                    moment = datetime.strptime(slot, "%H:%M")
                    if moment.minute not in (0, 30) or status not in (0, 1, 2, 3):
                        return None
        timestamp = datetime.fromisoformat(data["updated_at"])
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - timestamp
        return timestamp if timedelta(0) <= age <= timedelta(minutes=10) else None
    except (KeyError, TypeError, ValueError):
        return None

async def update_if_region_data(client, cache_data=None):
    from services.api_client import API_REGION_MAP, IF_REGION_ID, REPO_ROOT
    """Оновлює дані для регіону ІФ безпосередньо з їхнього сайту."""
    cache_data = cache_data if cache_data is not None else client._cached_data
    api_region_key = API_REGION_MAP.get(IF_REGION_ID, IF_REGION_ID)
    regions_list = cache_data.get("regions", [])
    region_obj = next((r for r in regions_list if r.get("cpu") == api_region_key), None)

    if not region_obj: return

    # 0. Спробуємо завантажити локальний кеш, якщо він є
    local_data = None
    data_path = os.path.join(REPO_ROOT, "data", "if_schedules.json")
    if os.path.exists(data_path):
        try:
            with open(data_path, "r", encoding="utf-8") as f:
                local_data = json.load(f)
            _LOGGER.info(f"Loaded IF data from local sync file: {data_path}")
        except Exception as e:
            _LOGGER.error(f"Failed to load local IF data: {e}")

    # 0.1 Спробуємо завантажити з Gist, якщо є конфігурація
    gist_id = os.getenv("GIST_ID")
    if gist_id and client._session:
        try:
            # Використовуємо пряме посилання на raw файл для швидкості
            # Для цього нам потрібен нікнейм, але ми можемо отримати його через API
            # або просто спробувати URL rashevskyv (власник репозиторію)
            gist_raw_url = f"https://gist.githubusercontent.com/rashevskyv/{gist_id}/raw/if_schedules.json"
            async with client._session.get(gist_raw_url, timeout=10) as resp:
                if resp.status == 200:
                    gist_data = await resp.json(content_type=None)
                    gist_time = source_timestamp(gist_data)
                    local_time = source_timestamp(local_data)
                    if gist_time and (not local_time or gist_time > local_time):
                        local_data = gist_data
                        _LOGGER.info("Selected fresh IF data from GitHub Gist")
                else:
                    _LOGGER.warning(f"Failed to fetch Gist {gist_id}: {resp.status}")
        except Exception as e:
            _LOGGER.error(f"Error fetching IF data from Gist: {e}")

    # 1. Отримуємо список черг з сайту
    if client._if_api_blocked:
        _LOGGER.info("IF API is marked as blocked, skipping direct update. Using data from global proxy.")
        queues = []
    else:
        queues = await client._fetch_if_queues()

    # 2. Якщо не вдалося (або заблоковано), використовуємо ті черги, що є в об'єкті регіону
    if not queues:
        queues = list(region_obj.get("schedule", {}).keys())

    # 3. Якщо і в об'єкті пусто, використовуємо хардкод (fallback)
    if not queues:
        _LOGGER.warning("No queues found for IF, using fallback list")
        queues = [f"{g}.{s}" for g in range(1, 7) for s in range(1, 3)] # 1.1 ... 6.2

    new_if_schedules = {}
    if client._if_api_blocked:
        _LOGGER.info(f"IF schedules for {len(queues)} queues will be taken from global proxy or local sync (geo-blocked direct access).")
    else:
        _LOGGER.info(f"Updating IF schedules for {len(queues)} queues: {queues}")
        # ... direct update logic ...
        semaphore = asyncio.Semaphore(4)
        async def fetch_queue(queue):
            async with semaphore:
                if client._if_api_blocked:
                    return
                raw = await client._fetch_if_schedule(queue)
                parsed = client._parse_if_schedule(raw, queue) if raw else {}
                if parsed:
                    new_if_schedules[queue] = parsed
        try:
            await asyncio.wait_for(asyncio.gather(*(fetch_queue(q) for q in queues)), timeout=15)
        except asyncio.TimeoutError:
            _LOGGER.warning("IF batch timed out; retaining completed queues and fallback data")

    # A source without a valid timestamp is never a fresh fallback.
    if source_timestamp(local_data):
        for q_id, q_sched in local_data["schedules"].items():
            new_if_schedules.setdefault(q_id, q_sched)
    elif local_data is not None:
        _LOGGER.warning("Ignoring stale or invalid IF sync data")

    if new_if_schedules:
        # Оновлюємо розклад у об'єкті регіону
        # Ми об'єднуємо нові дані з існуючими, щоб не втратити графік за попередній день
        # відразу після опівночі, якщо він ще потрібен.
        if "schedule" not in region_obj:
            region_obj["schedule"] = {}

        for q_id, q_sched in new_if_schedules.items():
            if q_id not in region_obj["schedule"]:
                region_obj["schedule"][q_id] = {}

            # Розумне об'єднання для ІФ: не затираємо відоме невідомим
            old_q_sched = region_obj["schedule"][q_id]
            for date_str, day_grid in q_sched.items():
                if date_str not in old_q_sched:
                    old_q_sched[date_str] = day_grid
                else:
                    # Порівнюємо по слотах
                    for slot, status in day_grid.items():
                        # Якщо новий статус "unknown" (0), а старий був відомий — залишаємо старий
                        if status == 0 and old_q_sched[date_str].get(slot, 0) != 0:
                            continue
                        old_q_sched[date_str][slot] = status

        _LOGGER.info("Updated IF schedules for %d queues", len(new_if_schedules))
