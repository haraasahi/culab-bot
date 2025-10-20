# bot/commands/calendar_cmds.py
import discord
from discord import app_commands
from datetime import datetime
from ..db import connect
from ..config import JST
from ..utils import now_utc

ONLINE_CHOICES = [
    app_commands.Choice(name="オンライン", value="online"),
    app_commands.Choice(name="オフライン", value="offline"),
]


def _parse_dt(date_str: str, time_str: str) -> int:
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=JST)
    return int(dt.timestamp())


def _fmt_hhmm(ts: int) -> str:
    return datetime.fromtimestamp(ts, JST).strftime("%H:%M")


def _fmt_md(ts: int) -> str:
    return datetime.fromtimestamp(ts, JST).strftime("%m/%d(%a)")


def setup(tree: app_commands.CommandTree, client: discord.Client):
    @tree.command(name="calendar_registration", description="予定を登録します")
    @app_commands.describe(
        date="日付 (YYYY-MM-DD)",
        start="開始時刻 (HH:MM)",
        end="終了時刻 (HH:MM)",
        title="予定の内容",
        mode="オンライン/オフライン",
        place="任意: 場所またはURL"
    )
    @app_commands.choices(mode=ONLINE_CHOICES)
    async def calendar_registration(
        inter: discord.Interaction,
        date: str,
        start: str,
        end: str,
        title: str,
        mode: app_commands.Choice[str],
        place: str | None = None,
    ):
        uid, gid = str(inter.user.id), str(inter.guild_id)
        try:
            st = _parse_dt(date, start)
            et = _parse_dt(date, end)
        except Exception:
            return await inter.response.send_message(
                "⛔ 日付または時間の形式が正しくありません。例: 2025-10-21 / 09:00 / 10:30", ephemeral=True
            )
        if et <= st:
            return await inter.response.send_message("⛔ 終了は開始より後にしてください。", ephemeral=True)

        is_online = 1 if mode.value == "online" else 0
        with connect() as con:
            con.execute(
                """
                INSERT INTO events(user_id, guild_id, title, start_ts, end_ts, is_online, place)
                VALUES(?,?,?,?,?,?,?)
                """,
                (uid, gid, title.strip(), st, et, is_online, (place or None)),
            )

        label = "オンライン" if is_online else "オフライン"
        where = f" / {place}" if place else ""
        await inter.response.send_message(
            f"🗓️ 予定を登録しました：\n"
            f"・{date} {start}–{end}（{label}{where}）\n"
            f"・件名：{title}"
        )

    @tree.command(name="calendar", description="登録した予定を表示します（今後7日分）")
    async def calendar_list(inter: discord.Interaction):
        uid, gid = str(inter.user.id), str(inter.guild_id)
        now = now_utc()
        horizon = now + 7 * 86400
        with connect() as con:
            rows = con.execute(
                """
                SELECT title, start_ts, end_ts, is_online, place
                FROM events
                WHERE user_id=? AND guild_id=? AND end_ts>=? AND start_ts<=?
                ORDER BY start_ts ASC
                LIMIT 50
                """,
                (uid, gid, now, horizon),
            ).fetchall()

        if not rows:
            return await inter.response.send_message("🗓️ 今後7日間の予定はありません。/calendar_registration で登録できます。")

        # 日付ごとにまとめて整形
        lines = [f"🗓️ {inter.user.mention} さんの今後7日間の予定"]
        cur_day = None
        for title, st, et, is_online, place in rows:
            day = _fmt_md(st)
            if day != cur_day:
                cur_day = day
                lines.append(f"\n■ {day}")
            label = "オンライン" if is_online else "オフライン"
            where = f" / {place}" if place else ""
            lines.append(f"  - {_fmt_hhmm(st)}–{_fmt_hhmm(et)}（{label}{where}）{title}")

        await inter.response.send_message("\n".join(lines))