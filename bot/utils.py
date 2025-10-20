# bot/utils.py
import time
from datetime import datetime, timedelta
from .config import JST

def now_utc() -> int:
    return int(time.time())

def fmt_duration(sec: int) -> str:
    if sec < 0:
        sec = 0
    h = sec // 3600
    m = (sec % 3600) // 60
    return f"{h}時間{m:02d}分"

def week_start_ts(now_ts: int | None = None) -> int:
    if now_ts is None:
        now_ts = now_utc()
    now_local = datetime.fromtimestamp(now_ts, JST)
    monday = now_local - timedelta(days=now_local.weekday())
    start_local = datetime(monday.year, monday.month, monday.day, 0, 0, 0, tzinfo=JST)
    return int(start_local.timestamp())

def today_start_ts(now_ts: int | None = None) -> int:
    if now_ts is None:
        now_ts = now_utc()
    now_local = datetime.fromtimestamp(now_ts, JST)
    start_local = datetime(now_local.year, now_local.month, now_local.day, 0, 0, 0, tzinfo=JST)
    return int(start_local.timestamp())
