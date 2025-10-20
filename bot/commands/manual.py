# bot/commands/manual.py
import discord
from discord import app_commands
from datetime import datetime
from ..config import JST
from ..db import connect
from ..utils import fmt_duration

def setup(tree: app_commands.CommandTree, client: discord.Client):
    @tree.command(name="log_manual", description="手動で作業ログを追加します（休憩なし）")
    async def log_manual(
        inter: discord.Interaction,
        date: str,     # 2025-10-20
        start: str,    # 09:00
        end: str,      # 11:15
        task_type: str # 研究/勉強/資料作成/その他 など
    ):
        uid, gid = str(inter.user.id), str(inter.guild_id)
        try:
            s_dt = datetime.strptime(f"{date} {start}", "%Y-%m-%d %H:%M").replace(tzinfo=JST)
            e_dt = datetime.strptime(f"{date} {end}",   "%Y-%m-%d %H:%M").replace(tzinfo=JST)
            if e_dt <= s_dt:
                return await inter.response.send_message("終了時刻は開始より後にしてください。", ephemeral=True)
        except Exception:
            return await inter.response.send_message(
                "形式が正しくありません。例: /log_manual 2025-10-20 09:00 11:15 勉強", ephemeral=True
            )

        st, et = int(s_dt.timestamp()), int(e_dt.timestamp())
        with connect() as con:
            rows = con.execute("""
                SELECT start_ts, end_ts FROM sessions
                WHERE user_id=? AND guild_id=? AND status='closed'
            """, (uid, gid)).fetchall()
            for s, e in rows:
                if (st < e and et > s):
                    return await inter.response.send_message("⚠️ 既存の記録と重複しています。", ephemeral=True)
            con.execute("""
                INSERT INTO sessions(user_id,guild_id,start_ts,end_ts,status,work_type)
                VALUES(?,?,?,?,?,?)
            """, (uid, gid, st, et, 'closed', task_type))
        await inter.response.send_message(
            f"✏️ 手動ログ追加：{task_type} {start}〜{end}（{fmt_duration(et - st)}）"
        )
