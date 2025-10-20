# bot/commands/report.py
import discord
from discord import app_commands
from datetime import datetime
from ..config import JST
from ..progress import save_progress

def setup(tree: app_commands.CommandTree, client: discord.Client):
    @tree.command(name="report", description="今日（または指定日）の進捗を保存します（追記型）")
    @app_commands.describe(
        text="保存したい進捗（複数行OK）",
        date="任意：YYYY-MM-DD（省略すると今日）"
    )
    async def report_cmd(inter: discord.Interaction, text: str, date: str | None = None):
        uid, gid = str(inter.user.id), str(inter.guild_id)
        when_ts = None
        if date:
            try:
                when_ts = int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=JST).timestamp())
            except Exception:
                return await inter.response.send_message("日付の形式は YYYY-MM-DD で入力してください。", ephemeral=True)
        save_progress(gid, uid, text, when_ts)
        await inter.response.send_message("📝 進捗を保存しました。（同じ日に複数回実行すると**追記**されます）")
