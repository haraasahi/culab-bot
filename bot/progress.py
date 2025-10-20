# bot/progress.py
import time
from typing import Dict, Tuple
from .db import connect
from .utils import today_start_ts, now_utc

# (guild_id, channel_id, user_id) -> expire_ts
_PENDING: Dict[Tuple[str, str, str], float] = {}
TTL_SEC = 10 * 60  # 終了後10分間はメッセージで進捗を追記OK

def arm_progress_capture(guild_id: str, channel_id: str, user_id: str):
    _PENDING[(guild_id, channel_id, user_id)] = time.time() + TTL_SEC

def is_waiting(guild_id: str, channel_id: str, user_id: str) -> bool:
    key = (guild_id, channel_id, user_id)
    exp = _PENDING.get(key)
    if exp is None:
        return False
    if exp < time.time():
        _PENDING.pop(key, None)
        return False
    return True

def save_progress(guild_id: str, user_id: str, text: str, when_ts: int | None = None):
    """今日のprogressに1行追記（上書きではなく“追加”）"""
    if when_ts is None:
        when_ts = now_utc()
    day_ts = today_start_ts(when_ts)
    with connect() as con:
        con.execute("""
            INSERT INTO daily_progress(user_id, guild_id, day_start_ts, content, created_ts)
            VALUES(?,?,?,?,?)
        """, (user_id, guild_id, day_ts, text.strip(), when_ts))
