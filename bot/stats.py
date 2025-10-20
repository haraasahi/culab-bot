# bot/stats.py
from typing import Dict
from .db import connect
from .utils import week_start_ts, today_start_ts

def get_active(con, user_id: str, guild_id: str):
    cur = con.execute("""
        SELECT id, user_id, guild_id, start_ts, end_ts, break_seconds, break_started_ts, status, work_type, break_alert_sent
        FROM sessions
        WHERE user_id=? AND guild_id=? AND status!='closed'
        ORDER BY id DESC LIMIT 1
    """, (user_id, guild_id))
    return cur.fetchone()

def sum_week(con, user_id: str, guild_id: str, now_ts: int) -> int:
    ws = week_start_ts(now_ts)
    rows = con.execute("""
        SELECT start_ts, end_ts, break_seconds
        FROM sessions
        WHERE user_id=? AND guild_id=? AND status='closed' AND end_ts>=?
    """, (user_id, guild_id, ws)).fetchall()
    total = 0
    for start_ts, end_ts, b in rows:
        if end_ts is None: continue
        total += max(0, (end_ts - start_ts) - b)
    return total

def sum_week_by_type(con, user_id: str, guild_id: str, now_ts: int) -> Dict[str, int]:
    ws = week_start_ts(now_ts)
    rows = con.execute("""
        SELECT work_type, start_ts, end_ts, break_seconds
        FROM sessions
        WHERE user_id=? AND guild_id=? AND status='closed' AND end_ts>=?
    """, (user_id, guild_id, ws)).fetchall()
    agg: Dict[str, int] = {}
    for t, s, e, b in rows:
        if e is None: continue
        sec = max(0, (e - s) - b)
        agg[t] = agg.get(t, 0) + sec
    return agg

def sum_day_by_type(con, user_id: str, guild_id: str, now_ts: int) -> Dict[str, int]:
    ds = today_start_ts(now_ts)
    de = ds + 86400
    rows = con.execute("""
        SELECT work_type, start_ts, end_ts, break_seconds
        FROM sessions
        WHERE user_id=? AND guild_id=? AND status='closed' AND end_ts BETWEEN ? AND ?
    """, (user_id, guild_id, ds, de)).fetchall()
    agg: Dict[str, int] = {}
    for t, s, e, b in rows:
        sec = max(0, (e - s) - b)
        agg[t] = agg.get(t, 0) + sec
    return agg
