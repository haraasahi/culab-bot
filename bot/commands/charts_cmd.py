# bot/commands/charts_cmd.py
import discord
from discord import app_commands
from ..utils import now_utc
from ..charts import make_timeline_week

def setup(tree: app_commands.CommandTree, client: discord.Client):
    @tree.command(
        name="chart",
        description="今週の作業タイムライン（タイプ別色分け・休憩グレー）"
    )
    async def chart_timeline_week_cmd(inter: discord.Interaction):
        uid, gid = str(inter.user.id), str(inter.guild_id)
        buf = make_timeline_week(uid, gid, now_utc())
        file = discord.File(buf, filename="timeline_week.png")
        embed = discord.Embed(title="🗓️ 今週の作業タイムライン")
        embed.set_image(url="attachment://timeline_week.png")
        await inter.response.send_message(embed=embed, file=file)
