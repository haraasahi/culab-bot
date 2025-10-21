# bot/commands/calendar_cmds.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo
from typing import Optional, Iterable

import discord
from discord import app_commands

from ..db import get_db  # 既存の SQLite 接続ユーティリティ
# タイムゾーン（既存に合わせてJST）
JST = ZoneInfo("Asia/Tokyo")

# --- 学年グループ定義 ---
# 役職ロール名 → 学年キー（DB保存用）。M1/M2は"M"に統合
ROLE_TO_GRADE = {
    "b3": "B3",
    "b4": "B4",
    "m1": "M",
    "m2": "M",
    "d": "D",
    "doctor": "D",
    "phd": "D",
    "researcher": "researcher",
}

# スラッシュコマンドの選択肢
# ※ "ALL" を追加（全学年向け）
GRADE_CHOICES = ["B3", "B4", "M", "D", "researcher", "ALL"]


# ---------- DB 初期化 ----------
def _ensure_tables():
    con = get_db()
    cur = con.cursor()
    cur.execute(
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
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cal_guild_grade_date ON calendar_events(guild_id, grade, date);")
    con.commit()


# ---------- 役立ちユーティリティ ----------
def _now_tz() -> dt.datetime:
    return dt.datetime.now(JST)

def _parse_date(date_str: str) -> dt.date:
    return dt.datetime.strptime(date_str, "%Y-%m-%d").date()

def _parse_time(hhmm: str) -> dt.time:
    return dt.datetime.strptime(hhmm, "%H:%M").time()

def _user_grade(member: discord.Member) -> Optional[str]:
    """メンバーのロールから学年キーを推定（M1/M2→'M'）。該当なしなら None。"""
    names = [r.name.lower() for r in member.roles]
    for n in names:
        if n in ROLE_TO_GRADE:
            return ROLE_TO_GRADE[n]
    return None

def _can_write_grade(member: discord.Member, target_grade: str) -> bool:
    """その学年のカレンダーに登録できるか。
       - 'ALL' はサーバ管理権限（manage_guild or administrator）のみ登録可
       - それ以外は「自分がその学年ロール」or「管理権限」
    """
    if member.guild_permissions.manage_guild or member.guild_permissions.administrator:
        return True
    if target_grade == "ALL":
        return False
    my_grade = _user_grade(member)
    return my_grade == target_grade

def _fmt_time(t: dt.time) -> str:
    return t.strftime("%H:%M")

def _fmt_date(d: dt.date) -> str:
    w = "月火水木金土日"[d.weekday() % 7]
    return f"{d:%Y-%m-%d}（{w}）"

def _grade_label(g: str) -> str:
    if g == "M":
        return "M（M1/M2）"
    if g == "ALL":
        return "ALL（全学年）"
    return g


# ---------- 埋め込み生成 ----------
def _embed_event_list(
    grade: str,
    date_to_rows: list[tuple[dt.date, list[tuple[int, dict]]]],
    title_suffix: str = "",
) -> discord.Embed:
    title = f"📅 学年カレンダー {_grade_label(grade)} {title_suffix}".strip()
    embed = discord.Embed(title=title, color=0x3BA55D, timestamp=_now_tz())
    for day, rows in date_to_rows:
        if not rows:
            continue
        lines = []
        for _id, ev in rows:
            tag = "【全学年】" if ev.get("grade") == "ALL" and grade != "ALL" else ""
            lines.append(
                f"{tag}**{_fmt_time(ev['start'])}–{_fmt_time(ev['end'])}** "
                f"{ev['title']} 〔{'オンライン' if ev['loc_type']=='online' else 'オフライン'}"
                f"{'・'+ev['loc_detail'] if ev['loc_detail'] else ''}〕"
            )
        embed.add_field(name=_fmt_date(day), value="\n".join(lines), inline=False)
    if not embed.fields:
        embed.description = "予定は見つかりませんでした。"
    # 補足
    if grade != "ALL":
        embed.set_footer(text="注: 「【全学年】」は全学年向けに登録された予定です。")
    return embed


# ---------- コマンド ----------
def setup(tree: app_commands.CommandTree, client: discord.Client):
    _ensure_tables()

    # /calendar_registration
    @tree.command(
        name="calendar_registration",
        description="学年カレンダーに予定を登録（MはM1/M2統合・ALLで全学年向け）。"
    )
    @app_commands.describe(
        date="日付（YYYY-MM-DD）",
        start="開始時刻（HH:MM）",
        end="終了時刻（HH:MM）",
        title="予定のタイトル",
        place="場所の種別（オンライン / オフライン）",
        detail="場所の補足（Zoom/教室名など任意）",
        grade="対象学年（未指定なら自分の学年）※ALL=全学年向け",
    )
    @app_commands.choices(
        place=[
            app_commands.Choice(name="オンライン", value="online"),
            app_commands.Choice(name="オフライン", value="offline"),
        ],
        grade=[app_commands.Choice(name=_grade_label(g), value=g) for g in GRADE_CHOICES],
    )
    async def calendar_registration(
        inter: discord.Interaction,
        date: str,
        start: str,
        end: str,
        title: str,
        place: app_commands.Choice[str],
        detail: Optional[str] = None,
        grade: Optional[app_commands.Choice[str]] = None,
    ):
        await inter.response.defer(ephemeral=False)

        # 対象学年（未指定なら自分の学年）
        target_grade = grade.value if grade else _user_grade(inter.user)  # type: ignore
        if target_grade is None:
            return await inter.followup.send(
                "⚠️ あなたの学年ロールが見つかりませんでした。B3/B4/M1/M2/D/Researcher のいずれかのロールを付与してください。",
                ephemeral=True,
            )
        if not _can_write_grade(inter.user, target_grade):  # type: ignore
            if target_grade == "ALL":
                return await inter.followup.send("⛔ 全学年向け（ALL）の登録は管理者のみ可能です。", ephemeral=True)
            return await inter.followup.send(
                f"⛔ この学年（{_grade_label(target_grade)}）のカレンダーに登録する権限がありません。",
                ephemeral=True,
            )

        # 入力チェック
        try:
            d = _parse_date(date)
            t_start = _parse_time(start)
            t_end = _parse_time(end)
        except ValueError:
            return await inter.followup.send("⚠️ 日付/時刻の形式が不正です。`YYYY-MM-DD` / `HH:MM` で指定してください。", ephemeral=True)

        if dt.datetime.combine(d, t_end) <= dt.datetime.combine(d, t_start):
            return await inter.followup.send("⚠️ 終了時刻は開始時刻より後にしてください。", ephemeral=True)

        loc_type = place.value
        loc_detail = (detail or "").strip() or None

        # 追加
        con = get_db()
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO calendar_events
                (guild_id, grade, title, date, start_time, end_time, location_type, location_detail, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                inter.guild_id,
                target_grade,
                title.strip(),
                d.strftime("%Y-%m-%d"),
                t_start.strftime("%H:%M"),
                t_end.strftime("%H:%M"),
                loc_type,
                loc_detail,
                inter.user.id,  # type: ignore
                _now_tz().isoformat(timespec="seconds"),
            ),
        )
        con.commit()
        ev_id = cur.lastrowid

        # 通知（公開）
        embed = discord.Embed(
            title=f"📝 予定を登録しました（{_grade_label(target_grade)}）",
            color=0x5865F2,
        )
        embed.add_field(name="日付", value=_fmt_date(d), inline=True)
        embed.add_field(name="時刻", value=f"{_fmt_time(t_start)}–{_fmt_time(t_end)}", inline=True)
        embed.add_field(name="タイトル", value=title[:256], inline=False)
        embed.add_field(
            name="場所",
            value=("オンライン" if loc_type == "online" else "オフライン") + (f"｜{loc_detail}" if loc_detail else ""),
            inline=False,
        )
        embed.set_footer(text=f"ID: {ev_id}")
        await inter.followup.send(embed=embed)

    # /calendar
    @tree.command(
        name="calendar",
        description="自分の学年（または指定学年）のカレンダーを表示（同時に全学年向けも含む）。"
    )
    @app_commands.describe(
        days="表示する日数（既定：14日）",
        from_date="起点日（YYYY-MM-DD。未指定なら今日）",
        grade="対象学年（指定しない場合は自分の学年。ALL=全学年の予定のみ）",
    )
    @app_commands.choices(
        grade=[app_commands.Choice(name=_grade_label(g), value=g) for g in GRADE_CHOICES],
    )
    async def calendar(
        inter: discord.Interaction,
        days: Optional[int] = 14,
        from_date: Optional[str] = None,
        grade: Optional[app_commands.Choice[str]] = None,
    ):
        # 対象学年（未指定→自分の学年）
        target_grade = grade.value if grade else _user_grade(inter.user)  # type: ignore
        if target_grade is None:
            return await inter.response.send_message(
                "⚠️ あなたの学年ロールが見つかりませんでした。B3/B4/M1/M2/D/Researcher のいずれかのロールを付与してください。",
                ephemeral=True,
            )

        # 期間
        try:
            base = _parse_date(from_date) if from_date else _now_tz().date()
        except ValueError:
            return await inter.response.send_message("⚠️ from_date は `YYYY-MM-DD` で指定してください。", ephemeral=True)

        days = int(days or 14)
        if days <= 0 or days > 60:
            return await inter.response.send_message("⚠️ days は 1〜60 の範囲で指定してください。", ephemeral=True)

        end_date = base + dt.timedelta(days=days)

        # 取得
        con = get_db()
        cur = con.cursor()
        if target_grade == "ALL":
            # 全学年向けのみ
            cur.execute(
                """
                SELECT id, grade, title, date, start_time, end_time, location_type, location_detail
                FROM calendar_events
                WHERE guild_id = ? AND grade = 'ALL' AND date >= ? AND date < ?
                ORDER BY date ASC, start_time ASC
                """,
                (inter.guild_id, base.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
            )
        else:
            # 指定学年 + 全学年向けを含む
            cur.execute(
                """
                SELECT id, grade, title, date, start_time, end_time, location_type, location_detail
                FROM calendar_events
                WHERE guild_id = ? AND grade IN (?, 'ALL') AND date >= ? AND date < ?
                ORDER BY date ASC, start_time ASC
                """,
                (inter.guild_id, target_grade, base.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
            )
        rows = cur.fetchall()

        # 整形
        by_day: dict[dt.date, list[tuple[int, dict]]] = {}
        for (ev_id, g, title, s_date, s_start, s_end, loc_type, loc_detail) in rows:
            d = _parse_date(s_date)
            st = _parse_time(s_start)
            en = _parse_time(s_end)
            by_day.setdefault(d, []).append(
                (ev_id, {
                    "grade": g,
                    "title": title,
                    "start": st,
                    "end": en,
                    "loc_type": loc_type,
                    "loc_detail": loc_detail
                })
            )

        ordered = sorted(by_day.items(), key=lambda kv: kv[0])
        scope = "（全学年のみ）" if target_grade == "ALL" else f"（{_fmt_date(base)} から {days}日・全学年含む）"
        embed = _embed_event_list(target_grade, ordered, title_suffix=scope)
        await inter.response.send_message(embed=embed)