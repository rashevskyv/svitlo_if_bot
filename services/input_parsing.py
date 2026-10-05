import re
from typing import List, Dict

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


def parse_time_interval(text: str):
    """Парсить текст у два часових значення HH:MM."""
    # Знаходимо всі цифри в рядку
    nums = re.findall(r"(\d+)", text)
    if not nums: return None

    if len(nums) == 2:
        # Може бути 23 08 або 23-08
        h1, h2 = int(nums[0]), int(nums[1])
        if 0 <= h1 <= 23 and 0 <= h2 <= 23:
            return f"{h1:02d}:00", f"{h2:02d}:00"
    elif len(nums) == 4:
        # Може бути 23 00 08 30
        h1, m1, h2, m2 = map(int, nums)
        if 0 <= h1 <= 23 and 0 <= m1 <= 59 and 0 <= h2 <= 23 and 0 <= m2 <= 59:
            return f"{h1:02d}:{m1:02d}", f"{h2:02d}:{m2:02d}"

    # Спроба регуляркою для HH:MM HH:MM
    matches = re.findall(r"(\d{1,2})[:\s-](\d{2})", text)
    if len(matches) == 2:
        try:
            h1, m1 = map(int, matches[0])
            h2, m2 = map(int, matches[1])
            if 0 <= h1 <= 23 and 0 <= m1 <= 59 and 0 <= h2 <= 23 and 0 <= m2 <= 59:
                return f"{h1:02d}:{m1:02d}", f"{h2:02d}:{m2:02d}"
        except: pass

    return None
