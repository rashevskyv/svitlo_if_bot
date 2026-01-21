import logging
from io import BytesIO
from typing import Dict, Optional, Tuple

_LOGGER = logging.getLogger(__name__)

class ImageCache:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ImageCache, cls).__new__(cls)
            cls._instance._cache = {} # (region, queue, mode, hash) -> List[BytesIO]
        return cls._instance

    def get(self, region: str, queue: str, mode: str, schedule_hash: str) -> Optional[list]:
        key = (region, queue, mode, schedule_hash)
        return self._cache.get(key)

    def set(self, region: str, queue: str, mode: str, schedule_hash: str, images: list):
        key = (region, queue, mode, schedule_hash)
        self._cache[key] = images
        _LOGGER.debug(f"Cached images for {region}/{queue} ({mode})")

    def clear_region(self, region: str):
        """Видаляє всі зображення для конкретного регіону (при оновленні графіку)."""
        keys_to_remove = [k for k in self._cache.keys() if k[0] == region]
        for k in keys_to_remove:
            del self._cache[k]
        if keys_to_remove:
            _LOGGER.info(f"Cleared {len(keys_to_remove)} cached images for region {region}")
