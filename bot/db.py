# bot/db.py
import sqlite3
from .config import DB_PATH

def connect():
    return sqlite3.connect(DB_PATH)

def init_db():
    with connect() as con:
        con.execute("""
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
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_user_guild_status ON sessions(user_id, guild_id, status);")

        con.execute("""
        CREATE TABLE IF NOT EXISTS session_breaks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            start_ts INTEGER NOT NULL,
            end_ts INTEGER,
            FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_breaks_session ON session_breaks(session_id);")

        # ★ 進捗メモを日単位で保存
        con.execute("""
        CREATE TABLE IF NOT EXISTS daily_progress(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            guild_id TEXT NOT NULL,
            day_start_ts INTEGER NOT NULL,  -- JSTでその日の0:00のエポック
            content TEXT NOT NULL,
            created_ts INTEGER NOT NULL
        );
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_progress_user_day ON daily_progress(user_id, guild_id, day_start_ts);")

# bot/db.py に追記
def close_open_sessions_at_startup(close_ts: int):
    """
    Bot起動時に、未終了のセッションを全て close_ts 時刻でクローズする。
    on_break 中だったものは break_seconds に（close_ts - break_started_ts）を加算。
    開いている休憩区間(session_breaks の end_ts=NULL)も同時にcloseする。
    """
    with connect() as con:
        rows = con.execute("""
            SELECT id, status, break_started_ts, break_seconds
            FROM sessions
            WHERE status!='closed'
        """).fetchall()

        for sid, status, bst, bsec in rows:
            if status == 'on_break' and bst is not None:
                bsec = (bsec or 0) + max(0, close_ts - int(bst))
            con.execute("""
                UPDATE sessions
                SET end_ts=?, break_seconds=?, break_started_ts=NULL, status='closed'
                WHERE id=?
            """, (close_ts, bsec or 0, sid))

        # 未クローズの休憩レコードもまとめて終了
        con.execute("""
            UPDATE session_breaks
            SET end_ts=?
            WHERE end_ts IS NULL
        """, (close_ts,))
