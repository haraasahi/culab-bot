# bot/charts.py
import io
from datetime import datetime, timedelta, date

import matplotlib
import matplotlib.pyplot as plt
from matplotlib import rcParams

from .config import WORK_TYPES, JST
from .db import connect

# ========= 日本語フォント設定（安全版） =========
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

# ========= 任意期間（start_date から days 日）のタイムライン（ガント風） =========
def make_timeline_range(
    user_id: str,
    guild_id: str,
    start_date: date | datetime,
    days: int = 7,
    title: str | None = None,
) -> io.BytesIO:
    """
    任意の開始日から days 日分（行ごとに1日）のタイムラインを描画。
    - 休憩はグレー、作業タイプは COLORS で色分け。
    - start_date は date/datetime どちらでもOK（JST解釈）。
    """
    if isinstance(start_date, datetime):
        sd = start_date.date()
    else:
        sd = start_date

    # JST での 0:00 タイムスタンプを起点にする
    day0 = datetime(sd.year, sd.month, sd.day, 0, 0, 0, tzinfo=JST)
    day_list = [day0 + timedelta(days=i) for i in range(days)]
    day_stamps = [int(d.timestamp()) for d in day_list]
    range_start = day_stamps[0]
    range_end = day_stamps[-1] + 86400

    # データ取得：範囲に重なるセッションのみ
    with connect() as con:
        rows = con.execute(
            """
            SELECT id, start_ts, end_ts, break_seconds, work_type
            FROM sessions
            WHERE user_id=? AND guild_id=? AND status='closed'
              AND NOT (end_ts <= ? OR start_ts >= ?)
            ORDER BY start_ts
            """,
            (user_id, guild_id, range_start, range_end),
        ).fetchall()

        breaks_map: dict[int, list[tuple[int, int]]] = {}
        for sid, *_ in rows:
            br = con.execute(
                """
                SELECT start_ts, end_ts FROM session_breaks
                WHERE session_id=? AND end_ts IS NOT NULL
                ORDER BY start_ts
                """,
                (sid,),
            ).fetchall()
            breaks_map[sid] = [(s, e) for s, e in br if e is not None]

    # 描画
    fig_h = 1.0 + 0.6 * len(day_list)
    plt.figure(figsize=(10, fig_h))
    ax = plt.gca()

    y_labels, y_pos = [], []
    # 上から順に start_date → ... → 最終日 の並び
    for idx, day_start in enumerate(day_stamps):
        d = datetime.fromtimestamp(day_start, JST)
        day_label = d.strftime("%m/%d(%a)")
        y = len(day_list) - idx  # 上から降順で並べる（見やすさ維持）
        y_labels.append(day_label)
        y_pos.append(y)

        # 当日のセッションを描画
        for sid, s_ts, e_ts, _, wtype in rows:
            clipped = _clip_to_day(s_ts, e_ts, day_start)
            if not clipped:
                continue
            s, e = clipped

            # 当日の休憩に切り出し
            day_breaks = []
            for b_s, b_e in breaks_map.get(sid, []):
                bclip = _clip_to_day(b_s, b_e, day_start)
                if bclip:
                    day_breaks.append(bclip)

            # 作業（タイプ色）
            for ws, we in _split_work_by_breaks(s, e, day_breaks):
                left = _to_local_hour(ws)
                width = _to_local_hour(we) - left
                ax.barh(y, width, left=left, height=0.35, color=COLORS.get(wtype, "#607D8B"))

            # 休憩（グレー）
            for bs, be in day_breaks:
                left = _to_local_hour(bs)
                width = _to_local_hour(be) - left
                ax.barh(y, width, left=left, height=0.35, color=COLORS["休憩"])

    ax.set_yticks(y_pos)
    ax.set_yticklabels(y_labels)
    ax.set_xlim(0, 24)
    ax.set_xticks(range(0, 25, 3))
    ax.set_xlabel("時刻")

    if title is None:
        t0 = day_list[0].strftime("%Y/%m/%d")
        t1 = (day_list[-1]).strftime("%Y/%m/%d")
        title = f"{t0}〜{t1} の作業タイムライン（タイプ別色分け、休憩=グレー）"
    ax.set_title(title)

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

# ========= 週間タイムライン（互換：月→日） =========
def make_timeline_week(user_id: str, guild_id: str, now_ts: int | None = None) -> io.BytesIO:
    """
    互換用：従来どおり「今週（Mon→Sun）」を描画。
    内部的には make_timeline_range を使う。
    """
    if now_ts is None:
        now_ts = int(datetime.now(JST).timestamp())
    today = datetime.fromtimestamp(now_ts, JST)
    monday = today - timedelta(days=today.weekday())
    monday0 = datetime(monday.year, monday.month, monday.day, 0, 0, 0, tzinfo=JST)
    return make_timeline_range(
        user_id=user_id,
        guild_id=guild_id,
        start_date=monday0.date(),
        days=7,
        title="今週の作業タイムライン（タイプ別色分け、休憩=グレー）",
    )

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