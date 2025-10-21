# bot/commands/logs.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import time
import math
import datetime as dt
from zoneinfo import ZoneInfo
from typing import Optional, Dict

import discord
from discord import app_commands

from ..db import get_db
from zoneinfo import ZoneInfo
try:
    from ..config import TZ
except Exception:
    TZ = "Asia/Tokyo"
JST = ZoneInfo(TZ)
JST = ZoneInfo("Asia/Tokyo")

# 既存のタイプ名称（DBに保存されている work_type を想定）
WORK_TYPES = ["研究", "勉強", "資料作成", "その他"]

# ========== ユーティリティ ==========
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

def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    """区間 [a_start,a_end) と [b_start,b_end) の重なり秒"""
    s = max(a_start, b_start)
    e = min(a_end, b_end)
    return max(0, e - s)

def _sum_type_durations(guild_id: int, user_id: int, r_start: int, r_end: int) -> Dict[str, int]:
    """sessions から期間内のタイプ別稼働秒を概算集計（休憩はセッション長に按分）"""
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
        # 休憩を稼働に対して按分して除外
        brk_share = int(round(brk * (ov / total_len)))
        work_sec = max(0, ov - brk_share)
        totals[wtype if wtype in totals else "その他"] += work_sec
    return totals

def _load_progress(guild_id: int, user_id: int, day0_ts: int) -> str:
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
    texts = [r[0] for r in rows]
    return "\n".join(texts)

def _try_build_week_chart(guild_id: int, user_id: int) -> Optional[discord.File]:
    """
    charts.make_timeline_week の返り値が:
      - str（ファイルパス）
      - bytes / bytearray（PNGなど）
      - PIL.Image（saveできるオブジェクト）
    のいずれでも受け付け、discord.File を返す。
    失敗した場合は None。
    """
    try:
        from ..charts import make_timeline_week
    except Exception:
        return None

    try:
        # シグネチャの差異に寛容に対応
        try:
            img_obj = make_timeline_week(user_id=user_id, guild_id=guild_id)
        except TypeError:
            try:
                img_obj = make_timeline_week(guild_id, user_id)
            except TypeError:
                img_obj = make_timeline_week(user_id)
    except Exception:
        return None

    # 返り値の型で分岐
    try:
        # パス文字列
        if isinstance(img_obj, str):
            return discord.File(img_obj, filename="week_timeline.png")
        # バイナリ
        if isinstance(img_obj, (bytes, bytearray)):
            bio = io.BytesIO(img_obj)
            bio.seek(0)
            return discord.File(bio, filename="week_timeline.png")
        # PIL.Image 風
        if hasattr(img_obj, "save"):
            bio = io.BytesIO()
            img_obj.save(bio, format="PNG")
            bio.seek(0)
            return discord.File(bio, filename="week_timeline.png")
    except Exception:
        return None

    return None

def _weekday_jp(d: dt.date) -> str:
    return "月火水木金土日"[d.weekday() % 7]

# ========== コマンド登録 ==========
def setup(tree: app_commands.CommandTree, client: discord.Client):

    @tree.command(name="log", description="作業ログを見る（画像は『今週』選択時に自動添付）")
    @app_commands.describe(
        period="期間を選択（今日 / 今週）"
    )
    @app_commands.choices(
        period=[
            app_commands.Choice(name="今日", value="today"),
            app_commands.Choice(name="今週", value="week"),
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
            # 本日0時〜翌0時（JST）
            start_dt = _jst_midnight(today)
            end_dt = start_dt + dt.timedelta(days=1)
            start_ts, end_ts = _unix(start_dt), _unix(end_dt)

            totals = _sum_type_durations(guild.id, user.id, start_ts, end_ts)
            progress = _load_progress(guild.id, user.id, start_ts)

            embed = discord.Embed(
                title=f"🗓️ 今日のログ（{today:%Y-%m-%d}（{_weekday_jp(today)}））",
                color=0x00B5AD,
            )
            for t in WORK_TYPES:
                embed.add_field(name=t, value=_fmt_duration(totals.get(t, 0)), inline=True)
            total_sum = sum(totals.values())
            embed.add_field(name="合計", value=_fmt_duration(total_sum), inline=True)

            if progress:
                embed.add_field(name="進捗メモ", value=progress[:1024], inline=False)
            else:
                embed.add_field(name="進捗メモ", value="（未登録）", inline=False)

            return await inter.followup.send(embed=embed)

        else:
            # 週（月曜始まり）: 月曜0時〜翌週月曜0時（JST）
            # 今日が何曜日でも、その週の月曜に揃える
            weekday = today.weekday()  # Mon=0 ... Sun=6
            monday = today - dt.timedelta(days=weekday)
            start_dt = _jst_midnight(monday)
            end_dt = start_dt + dt.timedelta(days=7)
            start_ts, end_ts = _unix(start_dt), _unix(end_dt)

            totals = _sum_type_durations(guild.id, user.id, start_ts, end_ts)

            embed = discord.Embed(
                title=f"📘 今週のログ（{monday:%Y-%m-%d}〜{(monday+dt.timedelta(days=6)):%Y-%m-%d}）",
                color=0x3BA55D,
            )
            for t in WORK_TYPES:
                embed.add_field(name=t, value=_fmt_duration(totals.get(t, 0)), inline=True)
            total_sum = sum(totals.values())
            embed.add_field(name="合計", value=_fmt_duration(total_sum), inline=True)

            # 各日の進捗メモ
            for i in range(7):
                d = monday + dt.timedelta(days=i)
                d0 = _jst_midnight(d)
                content = _load_progress(guild.id, user.id, _unix(d0))
                label = f"{d:%m/%d}（{_weekday_jp(d)}）の進捗"
                embed.add_field(name=label, value=(content[:1024] if content else "（未登録）"), inline=False)

            # ★ 週タイムライン画像（タイプ別色分け・休憩グレー）を添付
            chart_file = _try_build_week_chart(guild.id, user.id)
            if chart_file:
                await inter.followup.send(embed=embed, file=chart_file)
            else:
                # 画像生成に失敗してもテキストだけは返す
                await inter.followup.send(embed=embed)