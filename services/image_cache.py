import logging
from collections import OrderedDict
from io import BytesIO
from typing import Dict, Optional, Tuple

_LOGGER = logging.getLogger(__name__)

class ImageCache:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ImageCache, cls).__new__(cls)
            cls._instance._cache = OrderedDict() # bounded (region, queue, mode, hash) cache
        return cls._instance

    def get(self, region: str, queue: str, mode: str, schedule_hash: str) -> Optional[list]:
        key = (region, queue, mode, schedule_hash)
        value = self._cache.get(key)
        if value is not None:
            self._cache.move_to_end(key)
        return value

    def set(self, region: str, queue: str, mode: str, schedule_hash: str, images: list):
        key = (region, queue, mode, schedule_hash)
        for old in [k for k in self._cache if k[:3] == key[:3] and k != key]:
            del self._cache[old]
        self._cache[key] = images
        self._cache.move_to_end(key)
        while len(self._cache) > 100:
            self._cache.popitem(last=False)
        _LOGGER.debug(f"Cached images for {region}/{queue} ({mode})")

    def clear_region(self, region: str):
        """Видаляє всі зображення для конкретного регіону (при оновленні графіку)."""
        keys_to_remove = [k for k in self._cache.keys() if k[0] == region]
        for k in keys_to_remove:
            del self._cache[k]
        if keys_to_remove:
            _LOGGER.info(f"Cleared {len(keys_to_remove)} cached images for region {region}")
