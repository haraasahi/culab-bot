# bot/views.py
import discord
from typing import Callable

class WorkButtons(discord.ui.View):
    """
    - 押下できるのは発行ユーザーのみ（interaction_checkで判定）
    - 各ボタンは main 側から渡されたコールバックを呼ぶ
    """
    def __init__(
        self,
        owner_user_id: str,
        on_start_break: Callable[[discord.Interaction], None],
        on_end_break: Callable[[discord.Interaction], None],
        on_end_work: Callable[[discord.Interaction], None],
    ):
        super().__init__(timeout=None)
        self.owner_user_id = owner_user_id
        self._on_start_break = on_start_break
        self._on_end_break = on_end_break
        self._on_end_work = on_end_work

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return str(interaction.user.id) == self.owner_user_id

    @discord.ui.button(label="休憩開始", style=discord.ButtonStyle.secondary)
    async def start_break_btn(self, interaction: discord.Interaction, _):
        await self._on_start_break(interaction)

    @discord.ui.button(label="休憩終了", style=discord.ButtonStyle.secondary)
    async def end_break_btn(self, interaction: discord.Interaction, _):
        await self._on_end_break(interaction)

    @discord.ui.button(label="作業終了", style=discord.ButtonStyle.danger)
    async def end_work_btn(self, interaction: discord.Interaction, _):
        await self._on_end_work(interaction)
