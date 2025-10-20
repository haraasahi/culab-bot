# bot/charts.py
import io
from datetime import datetime, timedelta

import matplotlib
import matplotlib.pyplot as plt
from matplotlib import rcParams

from .config import WORK_TYPES, JST
from .db import connect

# ========= 日本語フォント設定（安全版） =========
# スキャンせず、OSにありそうな日本語フォント名を優先指定。
# インストール済みのものが自動で選ばれます（macならヒラギノで表示されるはず）。
JP_FONTS = [
    "Hiragino Sans", "Hiragino Kaku Gothic ProN",  # mac
    "Yu Gothic", "Meiryo",                         # Windows
    "Noto Sans CJK JP", "Noto Sans JP", "IPAexGothic", "TakaoGothic",  # Linux系
    "MS Gothic", "MS PGothic", "Arial Unicode MS",
]

rcParams["font.family"] = "sans-serif"
rcParams["font.sans-serif"] = JP_FONTS + rcParams.get("font.sans-serif", [])
rcParams["axes.unicode_minus"] = False  # －が豆腐になるのを防ぐ

# ========= 単純な棒グラフ（週/日） =========
def make_bar_chart(data: dict, title: str) -> io.BytesIO:
    hours = [v / 3600 for v in data.values()]
    labels = list(data.keys())

    plt.figure(figsize=(5.8, 3.2))
    plt.bar(labels, hours)
    plt.title(title)
    plt.ylabel("時間 (h)")
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()
    return buf

# ========= カラーマップ =========
COLORS = {
    "研究": "#1976D2",
    "勉強": "#43A047",
    "資料作成": "#FB8C00",
    "その他": "#8E24AA",
    "休憩": "#9E9E9E",  # グレー
}

# ========= タイムライン描画のユーティリティ =========
def _clip_to_day(s_ts: int, e_ts: int, day_start_ts: int):
    """区間 [s,e] を当日 [day_start, day_end) に切り出す。重なりが無ければ None。"""
    day_end_ts = day_start_ts + 86400
    s = max(s_ts, day_start_ts)
    e = min(e_ts, day_end_ts)
    if e <= s:
        return None
    return (s, e)

def _split_work_by_breaks(session_start: int, session_end: int, breaks: list[tuple[int,int]]):
    """セッション区間から休憩区間を引いて作業区間を返す。"""
    segs = []
    cur = session_start
    for b_s, b_e in sorted(breaks):
        if b_e <= cur or b_s >= session_end:
            continue
        if cur < b_s:
            segs.append((cur, min(b_s, session_end)))
        cur = max(cur, b_e)
        if cur >= session_end:
            break
    if cur < session_end:
        segs.append((cur, session_end))
    return [(s, e) for s, e in segs if e > s]

def _to_local_hour(ts: int) -> float:
    dt = datetime.fromtimestamp(ts, JST)
    return dt.hour + dt.minute / 60 + dt.second / 3600

# ========= 週間タイムライン（ガント風） =========
def make_timeline_week(user_id: str, guild_id: str, now_ts: int | None = None) -> io.BytesIO:
    """今週(月→日)を行ごとに並べ、時間帯ごとの作業タイプ＆休憩を色分けして描画。"""
    if now_ts is None:
        now_ts = int(datetime.now(JST).timestamp())

    today = datetime.fromtimestamp(now_ts, JST)
    monday = today - timedelta(days=today.weekday())
    days = [datetime(monday.year, monday.month, monday.day, tzinfo=JST) + timedelta(days=i) for i in range(7)]
    day_stamps = [int(d.timestamp()) for d in days]

    # データ取得
    with connect() as con:
        rows = con.execute("""
            SELECT id, start_ts, end_ts, break_seconds, work_type
            FROM sessions
            WHERE user_id=? AND guild_id=? AND status='closed' AND end_ts>=?
            ORDER BY start_ts
        """, (user_id, guild_id, day_stamps[0])).fetchall()

        breaks_map = {}
        for sid, *_ in rows:
            br = con.execute("""
                SELECT start_ts, end_ts FROM session_breaks
                WHERE session_id=? AND end_ts IS NOT NULL ORDER BY start_ts
            """, (sid,)).fetchall()
            breaks_map[sid] = [(s, e) for s, e in br if e is not None]

    fig_h = 1.0 + 0.6 * len(days)
    plt.figure(figsize=(10, fig_h))
    ax = plt.gca()

    y_labels, y_pos = [], []
    for idx, day_start in enumerate(day_stamps):
        day_label = datetime.fromtimestamp(day_start, JST).strftime("%m/%d(%a)")
        y = len(days) - idx
        y_labels.append(day_label)
        y_pos.append(y)

        # 当日のセッションを描画
        for sid, s_ts, e_ts, _, wtype in rows:
            clipped = _clip_to_day(s_ts, e_ts, day_start)
            if not clipped:
                continue
            s, e = clipped

            # 休憩を当日に切り出し
            day_breaks = []
            for b_s, b_e in breaks_map.get(sid, []):
                bclip = _clip_to_day(b_s, b_e, day_start)
                if bclip:
                    day_breaks.append(bclip)

            # 作業セグメントをタイプ色で
            for ws, we in _split_work_by_breaks(s, e, day_breaks):
                left = _to_local_hour(ws)
                width = _to_local_hour(we) - left
                ax.barh(y, width, left=left, height=0.35, color=COLORS.get(wtype, "#607D8B"))

            # 休憩はグレー
            for bs, be in day_breaks:
                left = _to_local_hour(bs)
                width = _to_local_hour(be) - left
                ax.barh(y, width, left=left, height=0.35, color=COLORS["休憩"])

    ax.set_yticks(y_pos)
    ax.set_yticklabels(y_labels)
    ax.set_xlim(0, 24)
    ax.set_xticks(range(0, 25, 3))
    ax.set_xlabel("時刻")
    ax.set_title("今週の作業タイムライン（タイプ別色分け、休憩=グレー）")
    ax.grid(axis="x", linestyle=":", alpha=0.6)

    # 凡例
    handles = [matplotlib.patches.Patch(color=COLORS[t], label=t) for t in WORK_TYPES]
    handles.append(matplotlib.patches.Patch(color=COLORS["休憩"], label="休憩"))
    ax.legend(handles=handles, loc="upper right", ncols=min(3, len(handles)))

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=160)
    buf.seek(0)
    plt.close()
    return buf

# ========= 日次タイムライン =========
def make_timeline_day(user_id: str, guild_id: str, target_ts: int | None = None) -> io.BytesIO:
    """指定日の1日分を1行でタイムライン表示（色分け＋休憩グレー）。"""
    if target_ts is None:
        target_ts = int(datetime.now(JST).timestamp())
    day_start = int(datetime.fromtimestamp(target_ts, JST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

    with connect() as con:
        rows = con.execute("""
            SELECT id, start_ts, end_ts, break_seconds, work_type
            FROM sessions
            WHERE user_id=? AND guild_id=? AND status='closed' AND end_ts BETWEEN ? AND ?
            ORDER BY start_ts
        """, (user_id, guild_id, day_start, day_start + 86400)).fetchall()

        br_map = {}
        for sid, *_ in rows:
            br = con.execute("""
                SELECT start_ts, end_ts FROM session_breaks
                WHERE session_id=? AND end_ts IS NOT NULL ORDER BY start_ts
            """, (sid,)).fetchall()
            br_map[sid] = [(s, e) for s, e in br if e is not None]

    plt.figure(figsize=(10, 2.2))
    ax = plt.gca()
    y = 1

    for sid, s_ts, e_ts, _, wtype in rows:
        clipped = _clip_to_day(s_ts, e_ts, day_start)
        if not clipped:
            continue
        s, e = clipped

        day_breaks = []
        for b_s, b_e in br_map.get(sid, []):
            bclip = _clip_to_day(b_s, b_e, day_start)
            if bclip:
                day_breaks.append(bclip)

        for ws, we in _split_work_by_breaks(s, e, day_breaks):
            left = _to_local_hour(ws)
            width = _to_local_hour(we) - left
            ax.barh(y, width, left=left, height=0.35, color=COLORS.get(wtype, "#607D8B"))

        for bs, be in day_breaks:
            left = _to_local_hour(bs)
            width = _to_local_hour(be) - left
            ax.barh(y, width, left=left, height=0.35, color=COLORS["休憩"])

    ax.set_yticks([y])
    label = datetime.fromtimestamp(day_start, JST).strftime("%m/%d(%a)")
    ax.set_yticklabels([label])
    ax.set_xlim(0, 24)
    ax.set_xticks(range(0, 25, 3))
    ax.set_xlabel("時刻")
    ax.set_title("今日の作業タイムライン（タイプ別色分け、休憩=グレー）")
    ax.grid(axis="x", linestyle=":", alpha=0.6)

    handles = [matplotlib.patches.Patch(color=COLORS[t], label=t) for t in WORK_TYPES]
    handles.append(matplotlib.patches.Patch(color=COLORS["休憩"], label="休憩"))
    ax.legend(handles=handles, loc="upper right", ncols=min(3, len(handles)))

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=160)
    buf.seek(0)
    plt.close()
    return buf
