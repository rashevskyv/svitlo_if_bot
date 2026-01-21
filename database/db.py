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
                display_mode TEXT DEFAULT 'classic'
            )
        """)
        await db.commit()

        # Міграції для існуючих БД
        try:
            await db.execute("ALTER TABLE users ADD COLUMN display_mode TEXT DEFAULT 'classic'")
        except aiosqlite.OperationalError:
            pass
            
        try:
            await db.execute("ALTER TABLE users ADD COLUMN reminder_minutes INTEGER DEFAULT 0")
        except aiosqlite.OperationalError:
            pass

        try:
            await db.execute("ALTER TABLE users ADD COLUMN last_reminder_at TEXT")
        except aiosqlite.OperationalError:
            pass
            
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
            SELECT telegram_id, region_id, queue_id, last_schedule_hash, display_mode, reminder_minutes, last_reminder_at 
            FROM users WHERE telegram_id = ?
        """, (telegram_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return row
            return None

async def get_all_users() -> List[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT telegram_id, region_id, queue_id, last_schedule_hash, display_mode, reminder_minutes, last_reminder_at 
            FROM users
        """) as cursor:
            return await cursor.fetchall()

async def update_user_hash(telegram_id: int, schedule_hash: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET last_schedule_hash = ? WHERE telegram_id = ?", (schedule_hash, telegram_id))
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
            SELECT telegram_id, region_id, queue_id, last_schedule_hash, display_mode, reminder_minutes, last_reminder_at 
            FROM users WHERE region_id = ?
        """, (region_id,)) as cursor:
            return await cursor.fetchall()
