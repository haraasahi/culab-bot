# bot/progress.py
# -*- coding: utf-8 -*-
"""
進捗メモの一時キャプチャ（/end_work 直後の自動保存）を
『1回だけ』有効にするワンショット実装。

・arm_progress_capture(...) で 10分の受付ウィンドウをセット
・consume_waiting(...) が True を返すのは最初の1通だけ（同時に状態を消費）
・/report はこの制限とは無関係に何度でも save_progress(...) を呼べる
・save_progress(...) は「同日のメモを連結して保存」
"""

import time
from typing import Dict, Tuple

from .db import connect
from .utils import today_start_ts, now_utc

# (guild_id, channel_id, user_id) -> expire_ts
_PENDING: Dict[Tuple[str, str, str], float] = {}

# 終了後10分間はメッセージを進捗として受け付ける
TTL_SEC = 10 * 60


def arm_progress_capture(guild_id: str, channel_id: str, user_id: str) -> None:
    """/end_work 実行時に呼ぶ。10分の受付ウィンドウを開始。"""
    _PENDING[(guild_id, channel_id, user_id)] = time.time() + TTL_SEC


def is_waiting(guild_id: str, channel_id: str, user_id: str) -> bool:
    """
    後方互換のため残しておく簡易チェック関数（消費しない）。
    新規実装では consume_waiting(...) を使ってください。
    """
    key = (guild_id, channel_id, user_id)
    exp = _PENDING.get(key)
    if exp is None:
        return False
    if exp < time.time():
        _PENDING.pop(key, None)
        return False
    return True


def consume_waiting(guild_id: str, channel_id: str, user_id: str) -> bool:
    """
    ワンショット判定。
    有効期限内なら True を返し、同時に状態を消費（削除）する。
    2通目以降は False になるため、自動保存は『1回だけ』になる。
    """
    key = (guild_id, channel_id, user_id)
    exp = _PENDING.get(key)
    if exp is None:
        return False

    now = time.time()
    if exp < now:
        _PENDING.pop(key, None)
        return False

    # ここで消費 → 1回きり
    _PENDING.pop(key, None)
    return True


def save_progress(guild_id: str, user_id: str, text: str, when_ts: int | None = None) -> None:
    """
    今日の progress に追記保存（同日複数回は連結、上書きしない）。
    /report からの保存や自動保存のどちらでも利用可能。
    """
    if when_ts is None:
        when_ts = now_utc()

    text = (text or "").strip()
    if not text:
        return  # 空文字は無視

    day_ts = today_start_ts(when_ts)
    with connect() as con:
        row = con.execute(
            """
            SELECT id, content
            FROM daily_progress
            WHERE user_id=? AND guild_id=? AND day_start_ts=?
            """,
            (user_id, guild_id, day_ts),
        ).fetchone()

        if row:
            # 既存に連結（改行でつなぐ）
            new_content = (row[1] + "\n" + text).strip()
            con.execute(
                "UPDATE daily_progress SET content=?, created_ts=? WHERE id=?",
                (new_content, when_ts, row[0]),
            )
        else:
            # 新規行として保存
            con.execute(
                """
                INSERT INTO daily_progress(user_id, guild_id, day_start_ts, content, created_ts)
                VALUES(?,?,?,?,?)
                """,
                (user_id, guild_id, day_ts, text, when_ts),
            )