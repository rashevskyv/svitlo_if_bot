import aiohttp
import json
import logging
import sys
import os
import time
from datetime import datetime
from typing import Any, Optional, Dict

_LOGGER = logging.getLogger(__name__)

# Додаємо шлях до папки svitlo_live
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(CURRENT_DIR)
CONST_PATH = os.path.join(REPO_ROOT, "external", "svitlo_live", "custom_components", "svitlo_live", "const.py")

def load_const_directly():
    """Завантажує константи безпосередньо з файлу, уникаючи імпорту __init__.py та HA залежностей."""
    import importlib.util
    import types
    
    # Надійне мокування homeassistant
    for mod_name in ['homeassistant', 'homeassistant.const', 'homeassistant.core', 'homeassistant.helpers']:
        if mod_name not in sys.modules:
            m = types.ModuleType(mod_name)
            if mod_name == 'homeassistant': m.__path__ = []
            sys.modules[mod_name] = m
    
    # Додаємо Platform у homeassistant.const
    sys.modules['homeassistant.const'].Platform = types.SimpleNamespace(
        SENSOR="sensor", BINARY_SENSOR="binary_sensor", CALENDAR="calendar"
    )

    try:
        if not os.path.exists(CONST_PATH):
            # Спробуємо знайти в поточному каталозі (якщо запущено з кореня)
            alt_path = os.path.join(os.getcwd(), "external", "svitlo_live", "custom_components", "svitlo_live", "const.py")
            if os.path.exists(alt_path):
                path = alt_path
            else:
                _LOGGER.error(f"File not found: {CONST_PATH}")
                return None
        else:
            path = CONST_PATH
            
        spec = importlib.util.spec_from_file_location("svitlo_live_const_temp", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as e:
        _LOGGER.error(f"Failed to load const directly from {CONST_PATH}: {e}")
        import traceback
        _LOGGER.debug(traceback.format_exc())
        return None

const_module = load_const_directly()

if const_module:
    DTEK_API_URL = getattr(const_module, "DTEK_API_URL", "https://dtek-api.svitlo-proxy.workers.dev/")
    REGIONS = getattr(const_module, "REGIONS", {"ivano-frankivska-oblast": "Івано-Франківська область"})
    API_REGION_MAP = getattr(const_module, "API_REGION_MAP", {})
    _LOGGER.info(f"Successfully loaded {len(REGIONS)} regions from {CONST_PATH}")
else:
    _LOGGER.warning("Using fallback values for REGIONS")
    DTEK_API_URL = "https://dtek-api.svitlo-proxy.workers.dev/"
    REGIONS = {"ivano-frankivska-oblast": "Івано-Франківська область"}
    API_REGION_MAP = {}

IF_API_URL = "https://be-svitlo.oe.if.ua/schedule-by-queue"
IF_QUEUES_URL = "https://be-svitlo.oe.if.ua/gpv-queue-list"
IF_REGION_ID = "ivano-frankivska-oblast"

_instance = None

class SvitloApiClient:
    def __new__(cls, *args, **kwargs):
        global _instance
        if _instance is None:
            _instance = super(SvitloApiClient, cls).__new__(cls)
            _instance._initialized = False
        return _instance

    def __init__(self, session: Optional[aiohttp.ClientSession] = None, cache_ttl: int = 60):
        if self._initialized:
            if session: self._session = session
            return
        self._session = session
        self._cached_data = None
        self._old_cached_data = None
        self._last_fetch_time = 0
        self._cache_ttl = cache_ttl # seconds
        self._etag = None
        self._region_hashes = {} # region_cpu -> hash
        self._pending_changes = set() # region_cpu
        self._initialized = True

    async def fetch_schedule(self, region: str, queue: str) -> Optional[dict[str, Any]]:
        """
        Отримує розклад для вказаної черги. Використовує кеш, якщо він актуальний.
        """
        now = time.time()
        # Якщо кеш застарів, пробуємо оновити
        if not self._cached_data or (now - self._last_fetch_time) > self._cache_ttl:
            await self._refresh_cache()
            
        if not self._cached_data:
            return None
            
        api_region_key = API_REGION_MAP.get(region, region)
        regions_list = self._cached_data.get("regions", [])
        region_obj = next((r for r in regions_list if r.get("cpu") == api_region_key), None)
        
        if not region_obj:
            _LOGGER.error(f"Region '{api_region_key}' not found in API")
            return None
        
        date_today = self._cached_data.get("date_today")
        date_tomorrow = self._cached_data.get("date_tomorrow")
        
        schedule = (region_obj.get("schedule") or {}).get(queue) or {}
        if not schedule:
            _LOGGER.warning(f"No schedule found for queue {queue} in region {region}")
            return None
        
        return {
            "region": region,
            "queue": queue,
            "date_today": date_today,
            "date_tomorrow": date_tomorrow,
            "schedule": schedule,
            "is_emergency": region_obj.get("emergency", False)
        }

    async def get_old_schedule(self, region: str, queue: str) -> Optional[dict[str, Any]]:
        """
        Повертає попередній розклад (до останнього оновлення кешу).
        """
        if not self._old_cached_data:
            return None
            
        api_region_key = API_REGION_MAP.get(region, region)
        regions_list = self._old_cached_data.get("regions", [])
        region_obj = next((r for r in regions_list if r.get("cpu") == api_region_key), None)
        
        if not region_obj:
            return None
        
        date_today = self._old_cached_data.get("date_today")
        date_tomorrow = self._old_cached_data.get("date_tomorrow")
        
        schedule = (region_obj.get("schedule") or {}).get(queue) or {}
        if not schedule:
            return None
        
        return {
            "region": region,
            "queue": queue,
            "date_today": date_today,
            "date_tomorrow": date_tomorrow,
            "schedule": schedule,
            "is_emergency": region_obj.get("emergency", False)
        }

        return changed_regions

    async def _fetch_if_schedule(self, queue: str) -> Optional[list]:
        """Отримує сирі дані розкладу для конкретної черги ІФ."""
        if self._session is None:
            return None
        try:
            url = f"{IF_API_URL}?queue={queue}"
            async with self._session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    _LOGGER.error(f"IF API error {resp.status} for queue {queue}")
                    return None
                return await resp.json()
        except Exception as e:
            _LOGGER.error(f"Failed to fetch IF schedule for queue {queue}: {e}")
            return None

    def _parse_if_schedule(self, raw_data: list, queue: str) -> Dict[str, Dict[str, int]]:
        """
        Перетворює формат ІФ (інтервали) у формат бота (30-хвилинна сітка).
        """
        parsed = {}
        for day_data in raw_data:
            date_str = day_data.get("eventDate") # "28.01.2026"
            if not date_str: continue
            
            # Конвертуємо DD.MM.YYYY в YYYY-MM-DD
            try:
                dt = datetime.strptime(date_str, "%d.%m.%Y")
                iso_date = dt.date().isoformat()
            except:
                continue
                
            day_schedule = {}
            # Ініціалізуємо сітку (0 - світло)
            for h in range(24):
                day_schedule[f"{h:02d}:00"] = 1
                day_schedule[f"{h:02d}:30"] = 1
            
            # Заповнюємо відключення
            intervals = day_data.get("queues", {}).get(queue, [])
            for interval in intervals:
                # interval: {"from": "06:00", "to": "10:30", "status": 1}
                # status 1 зазвичай означає відключення на цьому сайті
                start_str = interval.get("from")
                end_str = interval.get("to")
                if not start_str or not end_str: continue
                
                try:
                    start_h, start_m = map(int, start_str.split(":"))
                    end_h, end_m = map(int, end_str.split(":"))
                    
                    # Проходимо по 30-хвилинних слотах
                    curr_h, curr_m = start_h, start_m
                    while (curr_h < end_h) or (curr_h == end_h and curr_m < end_m):
                        day_schedule[f"{curr_h:02d}:{curr_m:02d}"] = 2 # 2 - немає світла
                        curr_m += 30
                        if curr_m >= 60:
                            curr_m = 0
                            curr_h += 1
                except Exception as e:
                    _LOGGER.error(f"Error parsing interval {start_str}-{end_str}: {e}")
                    
            parsed[iso_date] = day_schedule
        return parsed

    async def _refresh_cache(self) -> list[str]:
        """
        Завантажує повний JSON з API та оновлює кеш.
        Також довантажує актуальні дані для Івано-Франківська.
        """
        import hashlib
        close_session = False
        if self._session is None:
            self._session = aiohttp.ClientSession()
            close_session = True

        changed_regions = []
        _LOGGER.info("Refreshing global API cache...")
        
        # Зберігаємо попередній стан перед оновленням
        if self._cached_data:
            import copy
            self._old_cached_data = copy.deepcopy(self._cached_data)
            
        try:
            # 1. Отримуємо основні дані
            url_with_cache_bust = f"{DTEK_API_URL}?t={int(time.time())}"
            headers = {"Cache-Control": "no-cache", "Pragma": "no-cache"}
            if self._etag: headers["If-None-Match"] = self._etag

            async with self._session.get(url_with_cache_bust, headers=headers, timeout=30) as resp:
                if resp.status == 200:
                    self._etag = resp.headers.get("ETag")
                    raw_response = await resp.json(content_type=None)
                    body_str = raw_response.get("body")
                    if body_str:
                        self._cached_data = json.loads(body_str)
                elif resp.status == 304:
                    _LOGGER.info("Global API returned 304.")
                else:
                    _LOGGER.error(f"Global API error {resp.status}")

            if not self._cached_data:
                return []

            # 2. Окремо оновлюємо Івано-Франківськ
            if IF_REGION_ID in REGIONS:
                await self._update_if_region_data()

            # 3. Визначаємо змінені регіони
            for r in self._cached_data.get("regions", []):
                cpu = r.get("cpu")
                r_content = json.dumps({
                    "schedule": r.get("schedule"),
                    "emergency": r.get("emergency")
                }, sort_keys=True)
                r_hash = hashlib.md5(r_content.encode()).hexdigest()
                
                if self._region_hashes.get(cpu) != r_hash:
                    changed_regions.append(cpu)
                    self._region_hashes[cpu] = r_hash
                    self._pending_changes.add(cpu)
            
            self._last_fetch_time = time.time()
            _LOGGER.info(f"Cache refreshed. Changed regions: {len(changed_regions)}")

        except Exception as e:
            _LOGGER.error(f"Error refreshing cache: {e}")
        finally:
            if close_session:
                await self._session.close()
                self._session = None
        
        return changed_regions

    async def _update_if_region_data(self):
        """Оновлює дані для регіону ІФ безпосередньо з їхнього сайту."""
        api_region_key = API_REGION_MAP.get(IF_REGION_ID, IF_REGION_ID)
        regions_list = self._cached_data.get("regions", [])
        region_obj = next((r for r in regions_list if r.get("cpu") == api_region_key), None)
        
        if not region_obj: return

        # Отримуємо список черг, які вже є в кеші (або всі можливі)
        # Для ІФ черги зазвичай 1.1, 1.2, 2.1...
        queues = list(region_obj.get("schedule", {}).keys())
        if not queues:
            # Якщо в глобальному API порожньо, спробуємо дефолтні або отримаємо з сайту
            # Але поки що візьмемо ті, що є.
            return

        _LOGGER.info(f"Updating IF schedules for {len(queues)} queues...")
        new_if_schedules = {}
        for q in queues:
            raw_if = await self._fetch_if_schedule(q)
            if raw_if:
                parsed_if = self._parse_if_schedule(raw_if, q)
                if parsed_if:
                    new_if_schedules[q] = parsed_if
        
        if new_if_schedules:
            # Оновлюємо розклад у об'єкті регіону
            region_obj["schedule"] = new_if_schedules
            _LOGGER.info(f"Successfully updated IF schedules from direct source")

    def get_changed_regions(self, reset: bool = True) -> list[str]:
        """
        Повертає список CPU регіонів, які змінилися з моменту останнього виклику з reset=True.
        Це дозволяє різним сервісам (наприклад, check_updates) не пропускати зміни,
        навіть якщо кеш був оновлений іншим сервісом (наприклад, check_reminders).
        """
        changes = list(self._pending_changes)
        if reset:
            self._pending_changes.clear()
        return changes

    async def get_regions(self) -> Dict[str, str]:
        """
        Повертає список регіонів, використовуючи REGIONS з const.py.
        Це гарантує, що список міст у боті завжди збігається з HA компонентом.
        """
        return REGIONS

    async def get_active_regions(self) -> Dict[str, str]:
        """
        Повертає список регіонів, які мають хоча б одну чергу з розкладом.
        """
        now = time.time()
        if not self._cached_data or (now - self._last_fetch_time) > self._cache_ttl:
            await self._refresh_cache()
            
        if not self._cached_data:
            return REGIONS # Fallback
            
        active_cpus = set()
        for r in self._cached_data.get("regions", []):
            if r.get("schedule"):
                active_cpus.add(r.get("cpu"))
        
        # Фільтруємо REGIONS за допомогою API_REGION_MAP та active_cpus
        filtered = {}
        for reg_id, reg_name in REGIONS.items():
            api_key = API_REGION_MAP.get(reg_id, reg_id)
            if api_key in active_cpus:
                filtered[reg_id] = reg_name
        
        return filtered if filtered else REGIONS

    @staticmethod
    def get_status_at_time(schedule_data: dict, dt: datetime) -> str:
        """
        Визначає статус (on/off/unknown) для конкретного часу.
        """
        date_str = dt.date().isoformat()
        day_schedule = schedule_data["schedule"].get(date_str, {})
        
        hour = dt.hour
        minute = 30 if dt.minute >= 30 else 0
        time_key = f"{hour:02d}:{minute:02d}"
        
        code = day_schedule.get(time_key, 0)
        if code == 1: return "on"
        if code == 2: return "off"
        if code == 3: return "possible"
        return "unknown"
