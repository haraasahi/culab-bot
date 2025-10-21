# bot/db.py
# -*- coding: utf-8 -*-
"""
SQLite コネクション管理とスキーマ初期化。

- get_db(): 共有コネクションを返す（calendar_cmds などが使用）
- connect(): 互換API（内部的には get_db を返す）
- init_db(): 全テーブル作成（既存 + calendar_events）
- close_open_sessions_at_startup(): 起動時に未終了セッションをクローズ

テーブル一覧（CREATE IF NOT EXISTS）:
- sessions
- session_breaks
- daily_progress
- events                 # 既存（ユーザー個別の予定）
- calendar_events        # 追加（学年/ALL 向けカレンダー）
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .config import DB_PATH  # 既存設定を使用

# ------------------------------------------------------------
# コネクション（共有）
# ------------------------------------------------------------
_DB_FILE = Path(DB_PATH)
_DB_FILE.parent.mkdir(parents=True, exist_ok=True)

_conn_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def get_db() -> sqlite3.Connection:
    """
    共有の SQLite コネクションを返す。
    - check_same_thread=False でスレッド間使用を許可
    - row_factory=sqlite3.Row で dictライクにアクセス可能
    - WAL / foreign_keys を有効化
    """
    global _conn
    with _conn_lock:
        if _conn is None:
            _conn = sqlite3.connect(_DB_FILE, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            try:
                _conn.execute("PRAGMA journal_mode=WAL;")
            except Exception:
                pass
            try:
                _conn.execute("PRAGMA foreign_keys=ON;")
            except Exception:
                pass
        return _conn


# 互換：既存コードは with connect() as con: を使っているため残す
def connect() -> sqlite3.Connection:
    """
    互換API。内部的には共有コネクションを返す。
    Context Manager で利用しても接続は閉じられません（commit/rollbackのみ）。
    """
    return get_db()


def close_db() -> None:
    """明示的に共有コネクションを閉じたい場合のみ使用（通常不要）。"""
    global _conn
    with _conn_lock:
        if _conn is not None:
            try:
                _conn.close()
            finally:
                _conn = None


# ------------------------------------------------------------
# スキーマ初期化
# ------------------------------------------------------------
def init_db():
    """
    すべてのテーブルを CREATE IF NOT EXISTS で作成します。
    既存のスキーマを壊さず、必要なインデックスも併せて作成します。
    """
    con = get_db()

    # sessions
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            guild_id TEXT NOT NULL,
            start_ts INTEGER NOT NULL,
            end_ts INTEGER,
            break_seconds INTEGER NOT NULL DEFAULT 0,
            break_started_ts INTEGER,
            status TEXT NOT NULL CHECK(status IN ('working','on_break','closed')),
            work_type TEXT NOT NULL DEFAULT 'その他',
            break_alert_sent INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_guild_status ON sessions(user_id, guild_id, status);"
    )

    # session_breaks
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS session_breaks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            start_ts INTEGER NOT NULL,
            end_ts INTEGER,
            FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_breaks_session ON session_breaks(session_id);")

    # daily_progress（日次の自由記述メモ）
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_progress(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            guild_id TEXT NOT NULL,
            day_start_ts INTEGER NOT NULL,  -- JSTでその日の0:00のエポック
            content TEXT NOT NULL,
            created_ts INTEGER NOT NULL
        );
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_progress_user_day ON daily_progress(user_id, guild_id, day_start_ts);"
    )

    # events（既存：ユーザー個別のイベント）
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            guild_id TEXT NOT NULL,
            title TEXT NOT NULL,
            start_ts INTEGER NOT NULL,
            end_ts INTEGER NOT NULL,
            is_online INTEGER NOT NULL DEFAULT 0,
            place TEXT
        );
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_events_guild_start ON events(guild_id, start_ts);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_events_user_start ON events(user_id, start_ts);")

    # --------------------------------------------------------
    # 追加：calendar_events（学年別/ALLのカレンダー）
    #   - grade: 'B3'|'B4'|'M'|'D'|'researcher'|'ALL'
    #   - date: 'YYYY-MM-DD'
    #   - start_time/end_time: 'HH:MM'
    #   - location_type: 'online' or 'offline'
    #   - location_detail: テキスト補足
    #   - created_by: 登録者の user_id
    # --------------------------------------------------------
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            grade TEXT NOT NULL,
            title TEXT NOT NULL,
            date TEXT NOT NULL,        -- 'YYYY-MM-DD'
            start_time TEXT NOT NULL,  -- 'HH:MM'
            end_time TEXT NOT NULL,    -- 'HH:MM'
            location_type TEXT NOT NULL,   -- 'online' or 'offline'
            location_detail TEXT,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_cal_guild_grade_date ON calendar_events(guild_id, grade, date);"
    )

    con.commit()


# ------------------------------------------------------------
# 起動時の未終了セッションのクローズ
# ------------------------------------------------------------
def close_open_sessions_at_startup(close_ts: int):
    """
    Bot起動時に、未終了のセッションを全て close_ts 時刻でクローズする。
    on_break 中だったものは break_seconds に（close_ts - break_started_ts）を加算。
    開いている休憩区間(session_breaks の end_ts=NULL)も同時に close する。
    """
    con = get_db()

    rows = con.execute(
        """
        SELECT id, status, break_started_ts, break_seconds
        FROM sessions
        WHERE status != 'closed'
        """
    ).fetchall()

    for row in rows:
        sid = row["id"]
        status = row["status"]
        bst = row["break_started_ts"]
        bsec = row["break_seconds"]

        if status == "on_break" and bst is not None:
            bsec = (bsec or 0) + max(0, close_ts - int(bst))

        con.execute(
            """
            UPDATE sessions
            SET end_ts = ?, break_seconds = ?, break_started_ts = NULL, status = 'closed'
            WHERE id = ?
            """,
            (close_ts, bsec or 0, sid),
        )

    # 未クローズの休憩レコードもまとめて終了
    con.execute(
        """
        UPDATE session_breaks
        SET end_ts = ?
        WHERE end_ts IS NULL
        """,
        (close_ts,),
    )

    con.commit()