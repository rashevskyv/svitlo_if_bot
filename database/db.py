import aiosqlite
import os
import json
from typing import List, Tuple, Optional, Dict

DB_PATH = os.path.join(os.path.dirname(__file__), "bot_database.db")

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                region_id TEXT NOT NULL,
                queue_id TEXT NOT NULL,
                last_schedule_hash TEXT,
                display_mode TEXT DEFAULT 'classic',
                reminder_minutes INTEGER DEFAULT 0,
                last_reminder_at TEXT,
                last_updated_at TEXT,
                notifications_enabled INTEGER DEFAULT 1,
                quiet_hours_start TEXT,
                quiet_hours_end TEXT,
                quiet_hours_silent INTEGER DEFAULT 1,
                last_status_reminder_at TEXT,
                last_announcement_id TEXT
            )
        """)
        await db.commit()

        # Міграції для існуючих БД (додаємо колонки по одній, якщо їх немає)
        migrations = [
            "ALTER TABLE users ADD COLUMN last_schedule_hash TEXT",
            "ALTER TABLE users ADD COLUMN display_mode TEXT DEFAULT 'classic'",
            "ALTER TABLE users ADD COLUMN reminder_minutes INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN last_reminder_at TEXT",
            "ALTER TABLE users ADD COLUMN last_updated_at TEXT",
            "ALTER TABLE users ADD COLUMN notifications_enabled INTEGER DEFAULT 1",
            "ALTER TABLE users ADD COLUMN quiet_hours_start TEXT",
            "ALTER TABLE users ADD COLUMN quiet_hours_end TEXT",
            "ALTER TABLE users ADD COLUMN last_status_reminder_at TEXT",
            "ALTER TABLE users ADD COLUMN quiet_hours_silent INTEGER DEFAULT 1",
            "ALTER TABLE users ADD COLUMN last_announcement_id TEXT"
        ]

        for migration in migrations:
            try:
                await db.execute(migration)
            except aiosqlite.OperationalError:
                pass # Колонка вже існує
        
        await db.commit()

async def add_or_update_user(telegram_id: int, region_id: str, queue_data: List[Dict[str, str]]):
    """
    queue_data: list of dicts like [{"id": "4", "alias": "Home"}, {"id": "5.2", "alias": "Work"}]
    """
    queue_json = json.dumps(queue_data)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (telegram_id, region_id, queue_id)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET 
                region_id = excluded.region_id,
                queue_id = excluded.queue_id
        """, (telegram_id, region_id, queue_json))
        
        await db.commit()

async def get_user(telegram_id: int) -> Optional[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT telegram_id, region_id, queue_id, last_schedule_hash, display_mode, reminder_minutes, last_reminder_at, last_updated_at,
                   notifications_enabled, quiet_hours_start, quiet_hours_end, quiet_hours_silent, last_status_reminder_at, last_announcement_id
            FROM users WHERE telegram_id = ?
        """, (telegram_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return row
            return None

async def get_all_users() -> List[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT telegram_id, region_id, queue_id, last_schedule_hash, display_mode, reminder_minutes, last_reminder_at, last_updated_at,
                   notifications_enabled, quiet_hours_start, quiet_hours_end, quiet_hours_silent, last_status_reminder_at, last_announcement_id
            FROM users
        """) as cursor:
            return await cursor.fetchall()

async def update_user_hash(telegram_id: int, schedule_hash: str):
    from datetime import datetime
    now_str = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET last_schedule_hash = ?, last_updated_at = ? WHERE telegram_id = ?", (schedule_hash, now_str, telegram_id))
        await db.commit()

async def update_user_display_mode(telegram_id: int, display_mode: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET display_mode = ? WHERE telegram_id = ?", (display_mode, telegram_id))
        await db.commit()

async def update_user_reminder(telegram_id: int, minutes: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET reminder_minutes = ? WHERE telegram_id = ?", (minutes, telegram_id))
        await db.commit()

async def update_user_last_reminder(telegram_id: int, timestamp: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET last_reminder_at = ? WHERE telegram_id = ?", (timestamp, telegram_id))
        await db.commit()

async def get_users_by_queue(region_id: str, queue_id: str) -> List[int]:
    """
    This function needs to be updated because queue_id is now a JSON string.
    However, it's mostly used for notifications, which we handle in check_updates.
    Let's keep it for compatibility but it might need a more complex query or post-processing.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT telegram_id, queue_id FROM users WHERE region_id = ?", (region_id,)) as cursor:
            rows = await cursor.fetchall()
            matching_users = []
            for tg_id, q_json in rows:
                try:
                    queues = json.loads(q_json)
                    if isinstance(queues, list) and any(q.get("id") == queue_id for q in queues):
                        matching_users.append(tg_id)
                    elif not isinstance(queues, list) and str(queues) == queue_id:
                        # Handle case where json.loads returned a single value (e.g. float or int)
                        matching_users.append(tg_id)
                except:
                    # Fallback for old data
                    if q_json == queue_id:
                        matching_users.append(tg_id)
            return matching_users

async def get_unique_queues_by_region(region_id: str) -> List[str]:
    """
    Повертає список всіх унікальних ID черг, які використовуються користувачами в цьому регіоні.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT queue_id FROM users WHERE region_id = ?", (region_id,)) as cursor:
            rows = await cursor.fetchall()
            unique_queues = set()
            for (q_json,) in rows:
                try:
                    queues = json.loads(q_json)
                    if isinstance(queues, list):
                        for q in queues:
                            unique_queues.add(q["id"])
                    else:
                        unique_queues.add(str(q_json))
                except:
                    unique_queues.add(str(q_json))
            return list(unique_queues)

async def get_users_by_region(region_id: str) -> List[Tuple]:
    """
    Повертає всіх користувачів конкретного регіону.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT telegram_id, region_id, queue_id, last_schedule_hash, display_mode, reminder_minutes, last_reminder_at, last_updated_at,
                   notifications_enabled, quiet_hours_start, quiet_hours_end, quiet_hours_silent, last_status_reminder_at, last_announcement_id
            FROM users WHERE region_id = ?
        """, (region_id,)) as cursor:
            return await cursor.fetchall()

async def update_user_notifications(telegram_id: int, enabled: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET notifications_enabled = ? WHERE telegram_id = ?", (enabled, telegram_id))
        await db.commit()

async def update_user_quiet_hours(telegram_id: int, start_time: Optional[str], end_time: Optional[str]):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET quiet_hours_start = ?, quiet_hours_end = ? WHERE telegram_id = ?", (start_time, end_time, telegram_id))
        await db.commit()

async def update_user_quiet_hours_mode(telegram_id: int, is_silent: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET quiet_hours_silent = ? WHERE telegram_id = ?", (is_silent, telegram_id))
        await db.commit()

async def update_user_status_reminder(telegram_id: int, timestamp: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET last_status_reminder_at = ? WHERE telegram_id = ?", (timestamp, telegram_id))
        await db.commit()

async def update_user_last_announcement(telegram_id: int, announcement_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET last_announcement_id = ? WHERE telegram_id = ?", (announcement_id, telegram_id))
        await db.commit()
