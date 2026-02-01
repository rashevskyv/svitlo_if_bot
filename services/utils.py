import logging
from datetime import datetime
from typing import Optional

_LOGGER = logging.getLogger(__name__)

def is_now_quiet_hours(start_str: Optional[str], end_str: Optional[str], current_dt: datetime) -> bool:
    """
    Перевіряє, чи поточний час входить у тихі години користувача.
    """
    if not start_str or not end_str:
        return False
    
    try:
        h_s, m_s = map(int, start_str.split(':'))
        h_e, m_e = map(int, end_str.split(':'))
        
        now_time = current_dt.time()
        start_time = now_time.replace(hour=h_s, minute=m_s, second=0, microsecond=0)
        end_time = now_time.replace(hour=h_e, minute=m_e, second=0, microsecond=0)
        
        if start_time < end_time:
            # Денний інтервал, наприклад 08:00 - 22:00
            return start_time <= now_time <= end_time
        else:
            # Нічний інтервал, наприклад 22:00 - 08:00
            return now_time >= start_time or now_time <= end_time
    except Exception as e:
        _LOGGER.error(f"Error parsing quiet hours: {e}")
        return False
