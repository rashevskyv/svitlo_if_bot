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

class SvitloApiClient:
    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self._session = session

    async def fetch_schedule(self, region: str, queue: str) -> Optional[dict[str, Any]]:
        """
        Отримує розклад для вказаної черги в будь-якій області.
        Використовує URL та мапінг з svitlo_live.
        """
        close_session = False
        if self._session is None:
            self._session = aiohttp.ClientSession()
            close_session = True

        # Визначаємо правильний ключ регіону (як у coordinator.py)
        api_region_key = API_REGION_MAP.get(region, region)
        _LOGGER.info(f"Fetching schedule for region: {region} (API key: {api_region_key}), queue: {queue}")
        
        try:
            # Додаємо мітку часу та заголовки для обходу кешу (cache busting)
            url_with_cache_bust = f"{DTEK_API_URL}?t={int(time.time())}"
            headers = {
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
            async with self._session.get(url_with_cache_bust, headers=headers, timeout=30) as resp:
                if resp.status != 200:
                    _LOGGER.error(f"HTTP {resp.status} for {DTEK_API_URL}")
                    return None
                
                raw_response = await resp.json(content_type=None)
                body_str = raw_response.get("body")
                if not body_str:
                    _LOGGER.error("API response missing 'body'")
                    return None
                
                data = json.loads(body_str)
                regions_list = data.get("regions", [])
                region_obj = next((r for r in regions_list if r.get("cpu") == api_region_key), None)
                
                if not region_obj:
                    _LOGGER.error(f"Region '{api_region_key}' not found in API")
                    return None
                
                date_today = data.get("date_today")
                date_tomorrow = data.get("date_tomorrow")
                
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

        except Exception as e:
            _LOGGER.error(f"Error fetching schedule: {e}")
            return None
        finally:
            if close_session:
                await self._session.close()
                self._session = None

    async def get_regions(self) -> Dict[str, str]:
        """
        Повертає список регіонів, використовуючи REGIONS з const.py.
        Це гарантує, що список міст у боті завжди збігається з HA компонентом.
        """
        return REGIONS

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
        return "unknown"
