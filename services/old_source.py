import logging

_LOGGER = logging.getLogger(__name__)

# Старий проксі svitlo.live: тут є області, яких немає в DTEK API
# (Сумська, Вінницька, Волинська, Миколаївська, Кіровоградська, Чернівецька, Донецька).
OLD_API_URL = "https://svitlo-proxy.svitlo-proxy.workers.dev"


def merge_old_regions(cache_data: dict, old_data, prior_data=None) -> None:
    """Додає регіони старого API, яких немає в DTEK. DTEK має пріоритет.
    Якщо старий API недоступний (old_data=None), бере ці регіони з prior_data."""
    # Прибираємо попередню копію (після 304 candidate вже містить старі регіони)
    cache_data["regions"] = [r for r in cache_data.get("regions", []) if r.get("_src") != "old"]
    have = {r.get("cpu") for r in cache_data["regions"]}
    if isinstance(old_data, dict):
        source = old_data.get("regions") or []
    else:
        source = [r for r in (prior_data or {}).get("regions", []) if r.get("_src") == "old"]
    for r in source:
        cpu = r.get("cpu")
        if cpu and cpu not in have and r.get("schedule"):
            cache_data["regions"].append({**r, "_src": "old"})
            have.add(cpu)


async def update_old_regions(client, cache_data: dict, prior_data=None) -> None:
    from services.api_client import BROWSER_HEADERS
    old_data = None
    try:
        async with client._session.get(OLD_API_URL, headers=BROWSER_HEADERS, timeout=15) as resp:
            if resp.status == 200:
                old_data = await resp.json(content_type=None)
            else:
                _LOGGER.warning("Old API error %s", resp.status)
    except Exception as e:
        _LOGGER.warning("Old API fetch failed (%s): %s", type(e).__name__, e)
    merge_old_regions(cache_data, old_data, prior_data)
