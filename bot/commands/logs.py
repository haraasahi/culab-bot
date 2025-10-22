# bot/commands/logs.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import datetime as dt
from zoneinfo import ZoneInfo
from typing import Optional, Dict

import discord
from discord import app_commands

from ..db import get_db

# タイムゾーン（JST固定。config 未依存）
JST = ZoneInfo("Asia/Tokyo")

# 集計対象のタイプ
WORK_TYPES = ["研究", "勉強", "資料作成", "その他"]


# ===== Utils =====
def _now() -> dt.datetime:
    return dt.datetime.now(JST)


def _jst_midnight(d: dt.date) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=JST)


def _unix(ts_dt: dt.datetime) -> int:
    return int(ts_dt.timestamp())


def _fmt_duration(sec: int) -> str:
    sec = max(0, int(sec))
    h = sec // 3600
    m = (sec % 3600) // 60
    if h and m:
        return f"{h}時間{m}分"
    if h:
        return f"{h}時間"
    return f"{m}分"


def _weekday_jp(d: dt.date) -> str:
    return "月火水木金土日"[d.weekday() % 7]


def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    """区間 [a_start,a_end) と [b_start,b_end) の重なり秒"""
    s = max(a_start, b_start)
    e = min(a_end, b_end)
    return max(0, e - s)


def _sum_type_durations(guild_id: int, user_id: int, r_start: int, r_end: int) -> Dict[str, int]:
    """
    sessions から期間内のタイプ別稼働秒を集計（休憩はセッション長に按分して除外）。
    期間とセッションのオーバーラップを考慮。
    """
    con = get_db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT start_ts, end_ts, break_seconds, work_type
        FROM sessions
        WHERE guild_id = ? AND user_id = ? AND end_ts IS NOT NULL
          AND NOT (end_ts <= ? OR start_ts >= ?)
        """,
        (str(guild_id), str(user_id), r_start, r_end),
    )
    totals = {t: 0 for t in WORK_TYPES}
    for start_ts, end_ts, brk, wtype in cur.fetchall():
        if not end_ts:
            continue
        ov = _overlap(start_ts, end_ts, r_start, r_end)
        if ov <= 0:
            continue
        total_len = max(1, end_ts - start_ts)
        brk = int(brk or 0)
        brk_share = int(round(brk * (ov / total_len)))
        work_sec = max(0, ov - brk_share)
        totals[wtype if wtype in totals else "その他"] += work_sec
    return totals


def _load_progress(guild_id: int, user_id: int, day0_ts: int) -> str:
    """
    daily_progress から当日のメモを取得（同日複数は結合して返す）。
    空なら ""。
    """
    con = get_db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT content FROM daily_progress
        WHERE guild_id = ? AND user_id = ? AND day_start_ts = ?
        ORDER BY id ASC
        """,
        (str(guild_id), str(user_id), day0_ts),
    )
    rows = cur.fetchall()
    if not rows:
        return ""
    return "\n".join(r[0] for r in rows)


def _file_from_img_obj(img_obj) -> Optional[discord.File]:
    """charts 側の戻り値（path / bytes / PIL.Image）を discord.File に変換。"""
    try:
        if isinstance(img_obj, str):
            return discord.File(img_obj, filename="week_timeline.png")
        if isinstance(img_obj, (bytes, bytearray)):
            bio = io.BytesIO(img_obj)
            bio.seek(0)
            return discord.File(bio, filename="week_timeline.png")
        if hasattr(img_obj, "save"):  # PIL.Image 互換
            bio = io.BytesIO()
            img_obj.save(bio, format="PNG")
            bio.seek(0)
            return discord.File(bio, filename="week_timeline.png")
    except Exception:
        return None
    return None


def _try_build_last7_chart(guild_id: int, user_id: int, start_date: dt.date) -> Optional[discord.File]:
    """
    直近7日チャートを生成して discord.File を返す。
    charts に make_timeline_range があれば優先し、無ければ make_timeline_week にフォールバック。
    """
    try:
        from .. import charts as _charts
    except Exception:
        return None

    # 1) 任意の範囲レンダラがあれば使う
    if hasattr(_charts, "make_timeline_range"):
        try:
            fn = getattr(_charts, "make_timeline_range")
            # 多様なシグネチャに寛容
            try:
                obj = fn(start_date=start_date, days=7, user_id=user_id, guild_id=guild_id)
            except TypeError:
                try:
                    obj = fn(user_id=user_id, guild_id=guild_id, start_date=start_date, days=7)
                except TypeError:
                    obj = fn(guild_id, user_id, start_date, 7)
            return _file_from_img_obj(obj)
        except Exception:
            pass

    # 2) 週版にフォールバック（引数名の揺れも吸収）
    if hasattr(_charts, "make_timeline_week"):
        fn = getattr(_charts, "make_timeline_week")
        for kwargs in [
            {"start_date": start_date, "days": 7, "user_id": user_id, "guild_id": guild_id},
            {"start": start_date, "days": 7, "user_id": user_id, "guild_id": guild_id},
            {"from_date": start_date, "days": 7, "user_id": user_id, "guild_id": guild_id},
            {"user_id": user_id, "guild_id": guild_id, "start_date": start_date, "days": 7},
        ]:
            try:
                obj = fn(**kwargs)
                f = _file_from_img_obj(obj)
                if f:
                    return f
            except TypeError:
                continue
            except Exception:
                break
        # 引数が受け取れない実装なら従来のカレンダー週を表示
        try:
            obj = fn(user_id=user_id, guild_id=guild_id)
            return _file_from_img_obj(obj)
        except Exception:
            pass

    return None


# ===== Slash Command =====
def setup(tree: app_commands.CommandTree, client: discord.Client):

    @tree.command(name="log", description="作業ログを見る（『今週』＝直近7日・画像添付）")
    @app_commands.describe(period="期間を選択（今日 / 今週=直近7日）")
    @app_commands.choices(
        period=[
            app_commands.Choice(name="今日", value="today"),
            app_commands.Choice(name="今週（直近7日）", value="week"),
        ]
    )
    async def log_cmd(inter: discord.Interaction, period: app_commands.Choice[str]):
        await inter.response.defer(ephemeral=False)

        guild = inter.guild
        user = inter.user
        if guild is None:
            return await inter.followup.send("⚠️ ギルド内で実行してください。", ephemeral=True)

        today = _now().date()

        if period.value == "today":
            # 今日
            start_dt = _jst_midnight(today)
            end_dt = start_dt + dt.timedelta(days=1)
            start_ts, end_ts = _unix(start_dt), _unix(end_dt)

            totals = _sum_type_durations(guild.id, user.id, start_ts, end_ts)

            embed = discord.Embed(
                title=f"🗓️ 今日のログ（{today:%Y-%m-%d}（{_weekday_jp(today)}））",
                color=0x00B5AD,
            )
            for t in WORK_TYPES:
                embed.add_field(name=t, value=_fmt_duration(totals.get(t, 0)), inline=True)
            total_sum = sum(totals.values())
            embed.add_field(name="合計", value=_fmt_duration(total_sum), inline=True)

            # 進捗（空なら表示しない）
            progress = _load_progress(guild.id, user.id, start_ts)
            if progress:
                embed.add_field(name="進捗メモ", value=progress[:1024], inline=False)

            return await inter.followup.send(embed=embed)

        # 今週＝直近7日（今日を含む過去6日〜今日）
        start_day = today - dt.timedelta(days=6)
        start_dt = _jst_midnight(start_day)
        end_dt = _jst_midnight(today) + dt.timedelta(days=1)  # 翌日0時
        start_ts, end_ts = _unix(start_dt), _unix(end_dt)

        totals = _sum_type_durations(guild.id, user.id, start_ts, end_ts)

        embed = discord.Embed(
            title=f"📘 今週のログ（{start_day:%Y-%m-%d}〜{today:%Y-%m-%d}）",
            color=0x3BA55D,
        )
        for t in WORK_TYPES:
            embed.add_field(name=t, value=_fmt_duration(totals.get(t, 0)), inline=True)
        total_sum = sum(totals.values())
        embed.add_field(name="合計", value=_fmt_duration(total_sum), inline=True)

        # 各日の進捗メモ：未登録日は非表示
        for i in range(7):
            d = start_day + dt.timedelta(days=i)
            d0 = _jst_midnight(d)
            content = _load_progress(guild.id, user.id, _unix(d0))
            if not content:
                continue
            label = f"{d:%m/%d}（{_weekday_jp(d)}）の進捗"
            embed.add_field(name=label, value=content[:1024], inline=False)

        # チャート画像：charts が範囲指定対応なら直近7日を生成、なければ従来週版にフォールバック
        chart_file = _try_build_last7_chart(guild.id, user.id, start_day)
        if chart_file:
            await inter.followup.send(embed=embed, file=chart_file)
        else:
            await inter.followup.send(embed=embed)