# bot/scheduler.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Iterable

import discord
from discord.ext import tasks

from .db import connect
from .utils import now_utc  # エポック秒（UTC）を返すユーティリティ想定

JST = ZoneInfo("Asia/Tokyo")

# 学年キー（DB保存値）→ サーバのカテゴリ名（大文字小文字は問わない）
GRADE_KEYS = ["B3", "B4", "M", "D", "researcher"]

_client: Optional[discord.Client] = None


# ========= DBマイグレーション（安全な後方互換ALTER） =========
def _ensure_columns():
    """不足カラムを安全に追加する（存在すれば無視）。"""
    with connect() as con:
        cur = con.cursor()
        # sessions.start_channel_id（/start_workを実行したチャンネルIDを保存）
        try:
            cur.execute("ALTER TABLE sessions ADD COLUMN start_channel_id TEXT;")
        except Exception:
            pass
        # calendar_events.remind1d_sent（1日前リマインド済みフラグ）
        try:
            cur.execute("ALTER TABLE calendar_events ADD COLUMN remind1d_sent INTEGER NOT NULL DEFAULT 0;")
        except Exception:
            pass
        con.commit()


# ========= 学年カテゴリ内「連絡」チャンネル取得/作成 =========
async def _get_or_create_notice_channel(guild: discord.Guild, grade_key: str) -> Optional[discord.TextChannel]:
    """カテゴリ <grade_key> 内の『連絡』テキストチャンネルを取得。無ければ作成（権限なければ None）。"""
    category = discord.utils.find(lambda c: c.name.lower() == grade_key.lower(), guild.categories)
    if category is None:
        return None

    chan = discord.utils.find(
        lambda ch: isinstance(ch, discord.TextChannel) and ch.name == "連絡",
        category.text_channels
    )
    if chan:
        return chan

    # 作成を試みる
    try:
        chan = await guild.create_text_channel(
            name="連絡",
            category=category,
            reason="自動作成: カレンダー1日前リマインド用"
        )
        return chan
    except Exception:
        return None


# ========= 1日前リマインド用 Embed =========
def _compose_calendar_reminder_embed(ev: dict, grade_for_view: str) -> discord.Embed:
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
    em.set_footer(text="※イベント開始のちょうど24時間前に自動送信されています。")
    return em


# ========= タスク: 休憩超過ゆる通知（/start_workのチャンネルへ） =========
@tasks.loop(minutes=5)
async def break_alert_monitor():
    """
    休憩開始から2時間経過 & 未通知 の on_break セッションに、
    『/start_work を実行したチャンネル』へゆる通知を投下。
    """
    if _client is None or not _client.is_ready():
        return

    now = now_utc()  # epoch seconds (UTC)
    with connect() as con:
        rows = con.execute(
            """
            SELECT id, user_id, guild_id, break_started_ts, start_channel_id
            FROM sessions
            WHERE status='on_break' AND break_started_ts IS NOT NULL AND break_alert_sent=0
            """
        ).fetchall()

        for sid, uid, gid, bst, ch_id in rows:
            if bst is None:
                continue
            if (now - int(bst)) < 7200:  # 2時間未満
                continue

            # 送信先チャンネル（保存されていない場合はスキップ）
            if not ch_id:
                continue

            guild = _client.get_guild(int(gid))
            if guild is None:
                continue
            channel = guild.get_channel(int(ch_id))
            if not isinstance(channel, discord.TextChannel):
                continue

            # メッセージ送信（発行者メンション付き）
            try:
                await channel.send(f"💤 <@{uid}> 休憩開始から**2時間**が経過しました。そろそろ作業に戻りませんか？")
                con.execute("UPDATE sessions SET break_alert_sent=1 WHERE id=?", (sid,))
            except Exception:
                # 送れなくても次回以降また試すとスパムになるため、失敗時もフラグを立てるなら下の行を有効化
                # con.execute("UPDATE sessions SET break_alert_sent=1 WHERE id=?", (sid,))
                pass


@break_alert_monitor.before_loop
async def _before_break_alert_monitor():
    if _client is None:
        return
    await _client.wait_until_ready()
    await asyncio.sleep(2)


# ========= タスク: カレンダー1日前リマインド =========
@tasks.loop(minutes=1)
async def calendar_reminder_loop():
    """
    calendar_events から『開始ちょうど24時間前（JST）』を検出し、
    学年カテゴリの『連絡』テキストチャンネルへリマインドを投下。
    """
    if _client is None or not _client.is_ready():
        return

    now = datetime.now(JST)

    # 直近範囲の未リマインドだけに絞って走査コストを下げる
    min_date = (now.date() - timedelta(days=2)).strftime("%Y-%m-%d")
    max_date = (now.date() + timedelta(days=62)).strftime("%Y-%m-%d")

    with connect() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, guild_id, grade, title, date, start_time, end_time, location_type, location_detail,
                   COALESCE(remind1d_sent, 0)
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
            remind_at = start_dt - timedelta(days=1)

            # 「ちょうど1日前」判定：now が remind_at 以降 かつ イベント開始前
            if not (now >= remind_at and now < start_dt):
                continue

            guild = _client.get_guild(int(guild_id))
            if guild is None:
                continue

            # 送信先学年の決定（ALL は全学年）
            target_grades: Iterable[str] = GRADE_KEYS if grade == "ALL" else [grade]

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
                    em = _compose_calendar_reminder_embed(
                        ev_payload,
                        grade_for_view=(gkey if grade != "ALL" else f"{gkey}（全学年）")
                    )
                    await chan.send(embed=em)
                    sent_any = True
                except Exception:
                    continue

            if sent_any:
                to_mark_done.append(ev_id)

        if to_mark_done:
            cur.executemany("UPDATE calendar_events SET remind1d_sent = 1 WHERE id = ?", [(i,) for i in to_mark_done])
            con.commit()


@calendar_reminder_loop.before_loop
async def _before_calendar_reminder_loop():
    if _client is None:
        return
    await _client.wait_until_ready()
    await asyncio.sleep(3)


# ========= エントリポイント =========
def start_schedulers(client: discord.Client):
    global _client
    _client = client
    _ensure_columns()  # 必要カラムが無ければ追加
    if not break_alert_monitor.is_running():
        break_alert_monitor.start()
    if not calendar_reminder_loop.is_running():
        calendar_reminder_loop.start()