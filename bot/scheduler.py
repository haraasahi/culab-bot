# bot/scheduler.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Iterable

import discord
from discord.ext import tasks

from .db import get_db

JST = ZoneInfo("Asia/Tokyo")

# 学年キー（DB保存値）→ カテゴリ名（サーバ内の想定）
GRADE_KEYS = ["B3", "B4", "M", "D", "researcher"]  # 'ALL' は全学年配信

_client: Optional[discord.Client] = None


def start_schedulers(client: discord.Client):
    """main.py の on_ready などから呼んでください。"""
    global _client
    _client = client
    if not calendar_reminder_loop.is_running():
        calendar_reminder_loop.start()


async def _get_or_create_notice_channel(guild: discord.Guild, grade_key: str) -> Optional[discord.TextChannel]:
    """カテゴリ <grade_key> 内の『連絡』チャンネルを取得。無ければ作成（権限があれば）。"""
    # カテゴリ名は大文字小文字を無視
    category = discord.utils.find(lambda c: c.name.lower() == grade_key.lower(), guild.categories)
    if category is None:
        return None

    chan = discord.utils.find(lambda ch: isinstance(ch, discord.TextChannel) and ch.name == "連絡",
                              category.text_channels)
    if chan is not None:
        return chan

    # 作成を試みる（権限が無い場合はスキップ）
    try:
        chan = await guild.create_text_channel(name="連絡", category=category, reason="自動作成: カレンダー1日前リマインド")
        return chan
    except Exception:
        return None


def _compose_reminder_embed(ev: dict, grade_for_view: str) -> discord.Embed:
    """リマインド用の埋め込みを作る。"""
    start_dt: datetime = ev["start_dt"]
    end_dt: datetime = ev["end_dt"]
    title = ev["title"]
    loc_type = ev["loc_type"]
    loc_detail = ev["loc_detail"] or ""

    where = ("オンライン" if loc_type == "online" else "オフライン") + (f"｜{loc_detail}" if loc_detail else "")

    em = discord.Embed(
        title="⏰ 明日の予定リマインド",
        description=f"**{title}**",
        color=0xF59E0B,
        timestamp=datetime.now(JST),
    )
    em.add_field(name="対象", value=grade_for_view, inline=True)
    em.add_field(name="日時", value=f"{start_dt:%Y-%m-%d（%a）} {start_dt:%H:%M}–{end_dt:%H:%M}", inline=True)
    em.add_field(name="場所", value=where, inline=False)
    em.set_footer(text="※これはイベント開始のちょうど24時間前に自動送信されています。")
    return em


@tasks.loop(minutes=1)
async def calendar_reminder_loop():
    """calendar_events から『1日前』のイベントを検出し、学年ごとの「連絡」チャンネルに通知。"""
    if _client is None or not _client.is_ready():
        return

    now = datetime.now(JST)

    # 直近約2ヶ月の未リマインドのみ対象にして走査コストを抑える
    min_date = (now.date() - timedelta(days=2)).strftime("%Y-%m-%d")
    max_date = (now.date() + timedelta(days=62)).strftime("%Y-%m-%d")

    con = get_db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, guild_id, grade, title, date, start_time, end_time, location_type, location_detail, COALESCE(remind1d_sent, 0)
        FROM calendar_events
        WHERE date >= ? AND date <= ? AND COALESCE(remind1d_sent, 0) = 0
        ORDER BY date ASC, start_time ASC
        """,
        (min_date, max_date),
    )
    rows = cur.fetchall()

    to_mark_done: list[int] = []

    for (ev_id, guild_id, grade, title, d_str, s_str, e_str, loc_type, loc_detail, sent_flag) in rows:
        # 開始/終了のJST日時
        try:
            d = datetime.strptime(d_str, "%Y-%m-%d").date()
            t_start = datetime.strptime(s_str, "%H:%M").time()
            t_end = datetime.strptime(e_str, "%H:%M").time()
        except Exception:
            continue

        start_dt = datetime(d.year, d.month, d.day, t_start.hour, t_start.minute, tzinfo=JST)
        end_dt = datetime(d.year, d.month, d.day, t_end.hour, t_end.minute, tzinfo=JST)

        # ちょうど1日前の同時刻
        remind_at = start_dt - timedelta(days=1)

        # 送信タイミング判定：now が remind_at 以降 かつ イベント開始前
        if not (now >= remind_at and now < start_dt):
            continue

        # 対象ギルドを取得
        guild = _client.get_guild(int(guild_id))
        if guild is None:
            continue

        # 送信先チャンネルを集める
        target_grades: Iterable[str]
        if grade == "ALL":
            target_grades = GRADE_KEYS
        else:
            target_grades = [grade]

        ev_payload = {
            "title": title,
            "start_dt": start_dt,
            "end_dt": end_dt,
            "loc_type": loc_type,
            "loc_detail": loc_detail,
        }

        sent_any = False
        for gkey in target_grades:
            chan = await _get_or_create_notice_channel(guild, gkey)
            if chan is None:
                continue
            try:
                em = _compose_reminder_embed(ev_payload, grade_for_view=(gkey if grade != "ALL" else f"{gkey}（全学年）"))
                await chan.send(embed=em)
                sent_any = True
            except Exception:
                # 送信に失敗しても他学年には続行
                continue

        if sent_any:
            to_mark_done.append(ev_id)

    # まとめて既送信にする
    if to_mark_done:
        cur.executemany("UPDATE calendar_events SET remind1d_sent = 1 WHERE id = ?", [(i,) for i in to_mark_done])
        con.commit()


@calendar_reminder_loop.before_loop
async def _before_calendar_reminder_loop():
    # Botのログイン完了まで待つ
    if _client is None:
        return
    await _client.wait_until_ready()
    # ギルドキャッシュが温まるまで少し待機
    await asyncio.sleep(3)