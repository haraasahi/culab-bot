# bot/commands/logs.py
import discord
from discord import app_commands
from datetime import datetime, timedelta
from ..db import connect
from ..config import WORK_TYPES, JST
from ..utils import now_utc, fmt_duration, week_start_ts, today_start_ts
from ..stats import sum_week_by_type, sum_day_by_type

MAX_LEN = 1800  # Discord 2000字制限対策（本文＋ヘッダ余裕）

def _clip(text: str) -> str:
    return text if len(text) <= MAX_LEN else text[:MAX_LEN] + "\n…（長文のため途中まで表示）"

def setup(tree: app_commands.CommandTree, client: discord.Client):
    @tree.command(
        name="log",
        description="タイプ別作業時間＋進捗を表示（今日/今週）"
    )
    @app_commands.describe(period="集計対象を選択してください")
    @app_commands.choices(
        period=[
            app_commands.Choice(name="今日", value="day"),
            app_commands.Choice(name="今週", value="week"),
        ]
    )
    async def log_cmd(inter: discord.Interaction, period: app_commands.Choice[str]):
        uid, gid = str(inter.user.id), str(inter.guild_id)
        now = now_utc()

        if period.value == "day":
            with connect() as con:
                by_type = sum_day_by_type(con, uid, gid, now)
                day_ts = today_start_ts(now)
                prog = con.execute("""
                    SELECT content, created_ts FROM daily_progress
                    WHERE user_id=? AND guild_id=? AND day_start_ts=?
                    ORDER BY created_ts
                """, (uid, gid, day_ts)).fetchall()
            total = sum(by_type.values())
            lines = [f"📝 {inter.user.mention} さんの **今日のタイプ別ログ**"]
            for tp in WORK_TYPES:
                lines.append(f"・{tp}：**{fmt_duration(by_type.get(tp, 0))}**")
            lines.append("――――――")
            lines.append(f"・合計：**{fmt_duration(total)}**")
            # 進捗
            lines.append("\n🧾 **今日の進捗**")
            if prog:
                for c, ts in prog:
                    hh = datetime.fromtimestamp(ts, JST).strftime("%H:%M")
                    lines.append(f"- {hh} … {c}")
            else:
                lines.append("- （まだ保存されていません）")
            return await inter.response.send_message(_clip("\n".join(lines)))

        else:  # week
            with connect() as con:
                by_type = sum_week_by_type(con, uid, gid, now)
                ws = week_start_ts(now)
                we = ws + 7 * 86400
                prog = con.execute("""
                    SELECT day_start_ts, content, created_ts FROM daily_progress
                    WHERE user_id=? AND guild_id=? AND day_start_ts BETWEEN ? AND ?
                    ORDER BY day_start_ts, created_ts
                """, (uid, gid, ws, we)).fetchall()
            total = sum(by_type.values())
            lines = [f"📝 {inter.user.mention} さんの **今週のタイプ別ログ**"]
            for tp in WORK_TYPES:
                lines.append(f"・{tp}：**{fmt_duration(by_type.get(tp, 0))}**")
            lines.append("――――――")
            lines.append(f"・合計：**{fmt_duration(total)}**")

            # 進捗（曜日ごとにまとめて表示）
            lines.append("\n🧾 **今週の進捗**")
            if prog:
                cur_day = None
                for dts, c, ts in prog:
                    if cur_day != dts:
                        cur_day = dts
                        label = datetime.fromtimestamp(dts, JST).strftime("%m/%d(%a)")
                        lines.append(f"■ {label}")
                    hh = datetime.fromtimestamp(ts, JST).strftime("%H:%M")
                    lines.append(f"  - {hh} … {c}")
            else:
                lines.append("（まだ保存されていません）")

            await inter.response.send_message(_clip("\n".join(lines)))
