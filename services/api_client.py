import aiohttp
import asyncio
import json
import logging
import sys
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Dict

_LOGGER = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# Додаємо шлях до папки svitlo_live
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# Fallback to current directory if we can't find the parent reliably in some environments
REPO_ROOT = os.getenv("REPO_ROOT", os.path.dirname(CURRENT_DIR))
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

# Проксі прибрав "dnipropetrovska-oblast": дані Дніпра тепер у dnipro-dnem та dnipro-cek.
# Старі користувачі (region_id в БД) йдуть на dnipro-dnem; ЦЕК доступний як окремий регіон.
API_REGION_MAP["dnipropetrovska-oblast"] = "dnipro-dnem"
REGIONS.pop("dnipropetrovska-oblast", None)
REGIONS["dnipro-dnem"] = "м. Дніпро (ДніпроЕнерго)"
REGIONS["dnipro-cek"] = "м. Дніпро (ЦЕК)"

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

    def __init__(self, session: Optional[aiohttp.ClientSession] = None, cache_ttl: Optional[int] = None):
        if self._initialized:
            if session is not None: self._session = session
            if cache_ttl is not None: self._cache_ttl = cache_ttl
            return
        self._session = session
        self._cached_data = None
        self._old_cached_data = None
        self._last_fetch_time = 0
        self._cache_ttl = cache_ttl if cache_ttl is not None else 60
        self._refresh_task = None
        self._last_refresh_completed = 0.0
        self._etag = None
        self._region_hashes = {} # region_cpu -> hash
        self._pending_changes = set() # region_cpu
        self._unavailable = set()
        self._if_api_blocked = False
        self._initialized = True

    async def fetch_schedule(self, region: str, queue: str) -> Optional[dict[str, Any]]:
        started = time.perf_counter()
        try:
            return await self._fetch_schedule(region, queue)
        finally:
            _LOGGER.debug("Schedule data lookup %r/%r took %.3fs", region, queue, time.perf_counter() - started)

    async def _fetch_schedule(self, region: str, queue: str) -> Optional[dict[str, Any]]:
        """
        Отримує розклад для вказаної черги. Використовує кеш, якщо він актуальний.
        """
        now = time.time()
        # Перевірка зміни дня
        last_fetch_dt = datetime.fromtimestamp(self._last_fetch_time) if self._last_fetch_time else None
        day_changed = last_fetch_dt and last_fetch_dt.date() != datetime.now().date()
        
        # Якщо кеш застарів АБО змінився день, пробуємо оновити
        if not self._cached_data or (now - self._last_fetch_time) > self._cache_ttl or day_changed:
            await self._refresh_cache(force=bool(day_changed))
            
        if not self._cached_data:
            return None
            
        api_region_key = API_REGION_MAP.get(region, region)
        regions_list = self._cached_data.get("regions", [])
        region_obj = next((r for r in regions_list if r.get("cpu") == api_region_key), None)
        
        if not region_obj:
            key = (region, queue)
            if key not in self._unavailable:
                _LOGGER.error("Schedule unavailable for region %r, queue %r; ask user to reconfigure", region, queue)
                self._unavailable.add(key)
            return None
        
        date_today = self._cached_data.get("date_today")
        date_tomorrow = self._cached_data.get("date_tomorrow")
        
        schedule = (region_obj.get("schedule") or {}).get(queue) or {}
        if not schedule:
            key = (region, queue)
            if key not in self._unavailable:
                _LOGGER.warning("Schedule unavailable for region %r, queue %r; ask user to reconfigure", region, queue)
                self._unavailable.add(key)
            return None
        self._unavailable.discard((region, queue))
        
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
            async with self._session.get(url, headers=BROWSER_HEADERS, timeout=15) as resp:
                if resp.status == 403:
                    _LOGGER.warning(f"IF API access forbidden (403) for queue {queue}. Likely geo-blocked.")
                    self._if_api_blocked = True
                    return None
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
            if not isinstance(day_data, dict):
                continue
            queues = day_data.get("queues")
            if not isinstance(queues, dict) or queue not in queues:
                continue
            if not isinstance(queues[queue], list):
                continue
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
            intervals = queues[queue]
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
                    
                    # FIX: Handle midnight as end of day
                    effective_end_h = end_h
                    if end_h == 0 and end_m == 0 and start_h > 0:
                        effective_end_h = 24
                    
                    while (curr_h < effective_end_h) or (curr_h == effective_end_h and curr_m < end_m):
                        day_schedule[f"{curr_h:02d}:{curr_m:02d}"] = 2 # 2 - немає світла
                        curr_m += 30
                        if curr_m >= 60:
                            curr_m = 0
                            curr_h += 1
                except Exception as e:
                    _LOGGER.error(f"Error parsing interval {start_str}-{end_str}: {e}")
                    
            parsed[iso_date] = day_schedule
        return parsed

    async def _refresh_cache(self, force: bool = False) -> list[str]:
        started = time.perf_counter()
        last_fetch_dt = datetime.fromtimestamp(self._last_fetch_time) if self._last_fetch_time else None
        day_changed = last_fetch_dt and last_fetch_dt.date() != datetime.now().date()
        if not force and not day_changed and self._cached_data and time.time() - self._last_fetch_time <= self._cache_ttl:
            return []
        task = self._refresh_task
        if task is None or task.done():
            if time.monotonic() - self._last_refresh_completed < 15:
                return []
            task = asyncio.create_task(self._refresh_cache_with_timeout())
            self._refresh_task = task
            task.add_done_callback(self._mark_refresh_completed)
        try:
            return await asyncio.shield(task)
        finally:
            _LOGGER.info("Cache refresh wait and work %.3fs", time.perf_counter() - started)

    async def _refresh_cache_with_timeout(self):
        try:
            return await asyncio.wait_for(self._refresh_cache_locked(), timeout=45)
        except asyncio.TimeoutError:
            _LOGGER.error("Cache refresh timed out")
            return []

    def _mark_refresh_completed(self, task):
        self._last_refresh_completed = time.monotonic()
        if self._refresh_task is task:
            self._refresh_task = None
        if not task.cancelled():
            task.exception()

    async def stop_refresh(self):
        task = self._refresh_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _refresh_cache_locked(self) -> list[str]:
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
        
        # Визначаємо, чи змінився день з моменту останнього оновлення
        day_changed = False
        today_iso = datetime.now().date().isoformat()
        if self._last_fetch_time:
            last_dt = datetime.fromtimestamp(self._last_fetch_time)
            if last_dt.date().isoformat() != today_iso:
                day_changed = True

        # Зберігаємо попередній стан перед оновленням
        import copy
        prior_data = copy.deepcopy(self._cached_data)
        candidate = copy.deepcopy(prior_data)
        candidate_etag = self._etag
            
        try:
            # 1. Отримуємо основні дані
            url_with_cache_bust = f"{DTEK_API_URL}?t={int(time.time())}"
            headers = BROWSER_HEADERS.copy()
            if self._etag: headers["If-None-Match"] = self._etag

            async with self._session.get(url_with_cache_bust, headers=headers, timeout=30) as resp:
                if resp.status == 200:
                    candidate_etag = resp.headers.get("ETag")
                    raw_response = await resp.json(content_type=None)
                    body_str = raw_response.get("body")
                    if not body_str:
                        raise ValueError("Global API response has no body")
                    new_data = json.loads(body_str)
                    if not isinstance(new_data, dict) or not isinstance(new_data.get("regions"), list):
                        raise ValueError("Global API response has invalid regions")
                    self._merge_with_old_data(new_data)
                    candidate = copy.deepcopy(new_data)
                    self._sync_cache_dates(candidate)
                elif resp.status == 304:
                    _LOGGER.info("Global API returned 304.")
                    if not candidate:
                        return []
                    self._sync_cache_dates(candidate)
                else:
                    _LOGGER.error(f"Global API error {resp.status}")
                    return []

            if not candidate:
                return []

            # 1.1 Додаємо області, які є лише в старому API
            from services.old_source import update_old_regions
            await update_old_regions(self, candidate, prior_data)

            # 2. Окремо оновлюємо Івано-Франківськ
            if IF_REGION_ID in REGIONS:
                await self._update_if_region_data(candidate)

            # 3. Визначаємо змінені регіони
            new_hashes = {}
            for r in candidate.get("regions", []):
                cpu = r.get("cpu")
                r_content = json.dumps({
                    "schedule": r.get("schedule"),
                    "emergency": r.get("emergency")
                }, sort_keys=True)
                r_hash = hashlib.md5(r_content.encode()).hexdigest()
                
                if self._region_hashes.get(cpu) != r_hash:
                    changed_regions.append(cpu)
                new_hashes[cpu] = r_hash

            self._cached_data = candidate
            self._etag = candidate_etag
            self._region_hashes = new_hashes
            self._pending_changes.update(changed_regions)
            
            self._last_fetch_time = time.time()
            if candidate != prior_data:
                self._old_cached_data = prior_data
            _LOGGER.info(f"Cache refreshed. Changed regions: {len(changed_regions)}")

        except asyncio.CancelledError:
            self._cached_data = prior_data
            raise
        except Exception as e:
            self._cached_data = prior_data
            _LOGGER.error(f"Error refreshing cache ({type(e).__name__}): {e}")
            return []
        finally:
            if close_session:
                await self._session.close()
                self._session = None
        
        # Якщо день змінився, ми хочемо перевірити оновлення для ВСІХ регіонів,
        # навіть якщо їхній вміст не змінився (щоб обробити "сліпі зони" після 00:00)
        if day_changed:
            _LOGGER.info("Day changed, marking all regions as pending changes for update check")
            for region in candidate.get("regions", []):
                if region.get("schedule"):
                    self._pending_changes.add(region.get("cpu"))

        return changed_regions

    async def _fetch_if_queues(self) -> list[str]:
        """Отримує список доступних черг з сайту ІФ."""
        if self._session is None:
            return []
        try:
            async with self._session.get(IF_QUEUES_URL, headers=BROWSER_HEADERS, timeout=15) as resp:
                if resp.status == 403:
                    _LOGGER.warning("IF Queues API access forbidden (403). Likely geo-blocked.")
                    self._if_api_blocked = True
                    return []
                if resp.status != 200:
                    _LOGGER.error(f"IF Queues API error {resp.status}")
                    return []
                data = await resp.json()
                # Очікуємо список об'єктів [{"code": "1.1", ...}, ...]
                if isinstance(data, list):
                    return [q.get("code") for q in data if isinstance(q, dict) and q.get("code")]
                return []
        except Exception as e:
            _LOGGER.error(f"Failed to fetch IF queues: {e}")
            return []

    async def _update_if_region_data(self, cache_data=None):
        from services.if_source import update_if_region_data
        await update_if_region_data(self, cache_data)

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

    def _sync_cache_dates(self, cache_data=None):
        """Забезпечує відповідність date_today та date_tomorrow системному часу."""
        cache_data = cache_data if cache_data is not None else self._cached_data
        if not cache_data: return
        
        from datetime import timedelta
        now_date = datetime.now().date()
        iso_today = now_date.isoformat()
        iso_tomorrow = (now_date + timedelta(days=1)).isoformat()
        
        cache_data["date_today"] = iso_today
        cache_data["date_tomorrow"] = iso_tomorrow

    def _merge_with_old_data(self, new_data: dict):
        """Об'єднує нові дані з кешу з попередніми, запобігаючи втраті відомих статусів."""
        if not self._cached_data: return
        
        old_regions = {r["cpu"]: r for r in self._cached_data.get("regions", [])}
        
        for new_r in new_data.get("regions", []):
            cpu = new_r.get("cpu")
            if cpu not in old_regions: continue
            
            old_r = old_regions[cpu]
            if "schedule" not in old_r: continue
            if "schedule" not in new_r: 
                new_r["schedule"] = old_r["schedule"]
                continue
                
            # Об'єднуємо черги
            for q_id, old_q_sched in old_r["schedule"].items():
                if q_id not in new_r["schedule"]:
                    new_r["schedule"][q_id] = old_q_sched
                    continue
                
                new_q_sched = new_r["schedule"][q_id]
                # Об'єднуємо дати
                for date_str, old_day_grid in old_q_sched.items():
                    if date_str not in new_q_sched:
                        new_q_sched[date_str] = old_day_grid
                    else:
                        # Розумне об'єднання слотів
                        for slot, old_status in old_day_grid.items():
                            new_status = new_q_sched[date_str].get(slot, 0)
                            # Якщо новий статус "unknown" (0), а старий був відомий — залишаємо старий
                            if new_status == 0 and old_status != 0:
                                new_q_sched[date_str][slot] = old_status
