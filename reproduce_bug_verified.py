
import asyncio
import hashlib
import json
from datetime import datetime
from typing import Optional

# Mocking parts of main.py and services
class MockLogger:
    def info(self, msg): print(f"[INFO] {msg}")
    def debug(self, msg): print(f"[DEBUG] {msg}")
    def error(self, msg): print(f"[ERROR] {msg}")
    def warning(self, msg): print(f"[WARNING] {msg}")

_LOGGER = MockLogger()

# The FIXED version of is_change_relevant
def is_change_relevant_fixed(old_sched: dict, new_sched: dict, mode: str, current_dt: datetime, last_update_dt: Optional[datetime] = None) -> bool:
    if not old_sched:
        _LOGGER.debug(f"is_change_relevant: old_sched is None, returning True (notifying on hash change after restart/history loss)")
        return True
    
    # ... (rest of the logic doesn't matter for the "None" case)
    return True

# Simulate the FIXED logic in check_updates
async def simulate_check_updates_fixed(last_hash: str, old_s: Optional[dict], new_s: dict):
    _LOGGER.info(f"Simulating check_updates with last_hash={last_hash}")
    
    # Calculate new hash (simplified)
    user_schedules = {"1": new_s["schedule"]}
    new_hash = hashlib.md5(json.dumps(user_schedules, sort_keys=True).encode()).hexdigest()
    
    is_relevant = False
    if new_hash != last_hash:
        _LOGGER.info(f"Hash changed: {last_hash} -> {new_hash}")
        
        # FIXED logic:
        if not old_s or (old_s and new_s and old_s["schedule"] == new_s["schedule"]):
            _LOGGER.info("History lost or unavailable, forcing relevance.")
            is_relevant = True
        else:
            if new_s and is_change_relevant_fixed(old_s, new_s, "classic", datetime.now()):
                is_relevant = True
        
        if not is_relevant and last_hash is not None:
            _LOGGER.info("Skipping notification (irrelevant)")
        elif last_hash is not None:
            _LOGGER.info("NOTIFYING USER!")
    else:
        _LOGGER.info("No hash change.")

async def main():
    # Case 1: Restart (old_s is None)
    print("\n--- TEST CASE: Restart with different schedule ---")
    old_h = "old_hash_from_db"
    new_s = {"schedule": {"2026-01-31": {"12:00": 2}}} # Different from old_h
    await simulate_check_updates_fixed(old_h, None, new_s)
    
    # Case 2: History Loss (old_s == new_s)
    print("\n--- TEST CASE: History loss (race condition) ---")
    old_h = "old_hash_from_db"
    new_s = {"schedule": {"2026-01-31": {"12:00": 2}}} # Different from old_h
    # intermediate refresh happened, so old_s is now same as new_s
    await simulate_check_updates_fixed(old_h, new_s, new_s)

if __name__ == "__main__":
    asyncio.run(main())
