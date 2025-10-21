# bot/commands/onboarding.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Optional, Dict

import discord
from discord import app_commands

from ..config import GRADE_ROLES, WELCOME_CHANNEL_NAME
try:
    from ..config import REGISTERED_ROLE_NAME  # 例: "Registered" / 使わないなら None
except Exception:
    REGISTERED_ROLE_NAME = None  # type: ignore[assignment]

# --- 内部メモリ（再起動で消えてOK） ---
_PENDING_GRADE: Dict[int, str] = {}  # user_id -> grade(str)


# --- ユーティリティ ---
def _find_role(guild: discord.Guild, name: str) -> Optional[discord.Role]:
    return discord.utils.get(guild.roles, name=name)

async def _ensure_role(guild: discord.Guild, name: str) -> discord.Role:
    role = _find_role(guild, name)
    if role:
        return role
    return await guild.create_role(
        name=name, mentionable=True, reason="onboarding: auto-create grade role"
    )

async def _ensure_registered_role(guild: discord.Guild) -> Optional[discord.Role]:
    if not REGISTERED_ROLE_NAME:
        return None
    role = discord.utils.get(guild.roles, name=REGISTERED_ROLE_NAME)
    if role:
        return role
    return await guild.create_role(
        name=REGISTERED_ROLE_NAME,
        mentionable=False,
        reason="onboarding: auto-create registered role",
    )

def _make_channel_name(s: str) -> str:
    """
    入力名をほぼそのままチャンネル名に使う（日本語可）。
    Discordで問題になりがちな記号だけ除去し、空白はハイフン化。
    """
    s = (s or "").strip()
    # 空白系はハイフンに
    s = re.sub(r"\s+", "-", s)
    # 明らかにマズい記号を除去（# はUIで付くので不要）
    s = re.sub(r"[#@:`*/\\<>|\"'?%]", "", s)
    # 長すぎると拒否されることがあるので安全側でトリム
    if len(s) > 90:
        s = s[:90]
    return s or "user"

def _cat_overwrites_for_role(
    guild: discord.Guild, visible_role: discord.Role
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    return {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        visible_role: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        ),
    }

async def _ensure_category(
    guild: discord.Guild, name: str, visible_role: Optional[discord.Role] = None
) -> discord.CategoryChannel:
    cat = discord.utils.get(guild.categories, name=name)
    if cat:
        if visible_role and not cat.overwrites.get(visible_role):
            ow = dict(cat.overwrites)
            ow.update(_cat_overwrites_for_role(guild, visible_role))
            ow[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
            await cat.edit(overwrites=ow, reason="onboarding: fix category overwrites")
        return cat
    overwrites = _cat_overwrites_for_role(guild, visible_role) if visible_role else None
    return await guild.create_category(
        name=name, overwrites=overwrites, reason="onboarding: auto-create category"
    )

async def _ensure_welcome_channel(guild: discord.Guild) -> discord.TextChannel:
    ch = discord.utils.get(guild.text_channels, name=WELCOME_CHANNEL_NAME)
    if ch:
        # 念のため全員見える/書けるを確保（公開案内用。DM優先なので多投しない）
        ow = dict(ch.overwrites)
        ow[guild.default_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        )
        await ch.edit(overwrites=ow, reason="onboarding: ensure welcome open")
        return ch
    ch = await guild.create_text_channel(
        WELCOME_CHANNEL_NAME, reason="onboarding: auto-create welcome"
    )
    await ch.edit(
        overwrites={
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )
        },
        reason="onboarding: ensure welcome open",
    )
    return ch


# --- UI（学年セレクト + 名前モーダル） ---
class GradeSelect(discord.ui.Select):
    def __init__(self):
        opts = [discord.SelectOption(label=g, value=g) for g in GRADE_ROLES]
        super().__init__(
            placeholder="学年を選択してください",
            min_values=1, max_values=1, options=opts, custom_id="grade_select"
        )

    async def callback(self, interaction: discord.Interaction):
        grade = self.values[0]
        _PENDING_GRADE[interaction.user.id] = grade
        await interaction.response.send_message(
            f"✅ 学年「**{grade}**」を選択しました。次に **名前** を入力してください。",
            ephemeral=True,
        )

class NameModal(discord.ui.Modal, title="名前の入力"):
    name = discord.ui.TextInput(
        label="あなたの名前（例：あさひ2）",
        placeholder="氏名を入力",
        required=True,
        max_length=32
    )

    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.user
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("ギルド内で実行してください。", ephemeral=True)

        grade = _PENDING_GRADE.get(user.id)
        if grade is None:
            return await interaction.response.send_message("先に学年を選択してください。", ephemeral=True)

        # 1) ロール付与
        grade_role = await _ensure_role(guild, grade)
        registered_role = await _ensure_registered_role(guild)
        roles_to_add = [grade_role] + ([registered_role] if registered_role else [])
        try:
            await user.add_roles(*roles_to_add, reason="onboarding: grade/registered")
        except discord.Forbidden:
            return await interaction.response.send_message(
                "ロール付与に失敗しました。Botに『ロールの管理』権限を付与してください。",
                ephemeral=True,
            )

        # 2) ニックネームを入力名に変更
        display_name = str(self.name).strip()
        try:
            await user.edit(nick=display_name)
        except discord.Forbidden:
            pass

        # 3) 学年カテゴリ（@everyone 非表示/当該学年のみ可視）
        category = await _ensure_category(guild, grade_role.name, visible_role=grade_role)

        # 4) 個人チャンネルを「入力名」をそのままベースに作成（日本語OK）
        base = _make_channel_name(display_name)  # 例：あさひ2 → あさひ2
        safe = base or "user"
        i = 2
        while discord.utils.get(category.text_channels, name=safe) is not None:
            safe = f"{base}-{i}"
            i += 1

        channel = await guild.create_text_channel(
            name=safe,
            category=category,
            topic=f"Owner: {display_name} / 学年: {grade_role.name}",
            reason="onboarding: create personal channel",
        )

        # 5) 公開アナウンスは控えめに。基本はDMで完結（下の on_member_join でもDM優先）
        welcome = await _ensure_welcome_channel(guild)
        await welcome.send(
            f"🎉 {user.mention} さん 登録完了！ 学年 **{grade_role.name}** を付与し、"
            f"カテゴリ **{category.name}** に **#{channel.name}** を作成しました。"
            + (f"\n共通ロール **{registered_role.name}** も付与しました。" if registered_role else "")
        )

        _PENDING_GRADE.pop(user.id, None)
        await interaction.response.send_message(
            "登録完了！ほかのチャンネルが見えるようになりました。", ephemeral=True
        )

class OnboardView(discord.ui.View):
    """永続ビュー（再起動後も動作）"""
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(GradeSelect())

    @discord.ui.button(label="名前を入力", style=discord.ButtonStyle.primary, custom_id="name_button")
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(NameModal())


# --- コマンド登録 & リスナー ---
def setup(tree: app_commands.CommandTree, client: discord.Client):
    # 永続ビュー登録
    try:
        client.add_view(OnboardView())
    except Exception:
        pass

    @tree.command(name="welcome_post", description="#welcome にウェルカム案内を投稿（管理）")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_post(inter: discord.Interaction):
        guild = inter.guild
        if guild is None:
            return await inter.response.send_message("ギルド内で実行してください。", ephemeral=True)
        ch = await _ensure_welcome_channel(guild)
        view = OnboardView()
        await ch.send(
            "ようこそ！\n"
            "1) 下のメニューで **学年** を選択\n"
            "2) **名前を入力** ボタンであなたの名前を送信\n"
            "→ Bot が **学年ロール付与** と **学年カテゴリ内に個人チャンネル作成** を行います。",
            view=view,
        )
        await inter.response.send_message(f"✅ {ch.mention} に案内を投稿しました。", ephemeral=True)

    # 新規参加時：まず DM に個別案内（＝welcomeに公開で流さない）
    @client.event
    async def on_member_join(member: discord.Member):
        guild = member.guild
        view = OnboardView()
        # DM で個別送信（最優先）
        try:
            await member.send(
                "👋 サーバーへようこそ！\n"
                "下のUIで **学年** を選び、**名前** を送信してください。\n"
                "→ ロール付与と個人チャンネル作成を自動で行います。",
                view=view,
            )
            return
        except discord.Forbidden:
            pass  # DM拒否の場合のみフォールバック

        # フォールバック：welcome へ最小限の案内（公開）
        ch = await _ensure_welcome_channel(guild)
        await ch.send(
            f"{member.mention} さん、ようこそ！\n"
            "DMが受け取れない設定のため、こちらから登録してください：\n"
            "1) 下のメニューで **学年** を選択\n"
            "2) **名前を入力** ボタンであなたの名前を送信",
            view=view,
        )

    # 既存カテゴリの一括整備（任意）
    @tree.command(
        name="lockdown_categories",
        description="カテゴリ権限を一括設定（@everyone非表示、学年 or Registered を可視）",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def lockdown_categories(inter: discord.Interaction):
        guild = inter.guild
        if guild is None:
            return await inter.response.send_message("ギルド内で実行してください。", ephemeral=True)
        reg_role = await _ensure_registered_role(guild)
        changed = 0

        welcome = await _ensure_welcome_channel(guild)  # 公開案内用
        ow = dict(welcome.overwrites)
        ow[guild.default_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        )
        await welcome.edit(overwrites=ow, reason="onboarding: ensure welcome open")

        for cat in guild.categories:
            new_ow = dict(cat.overwrites)
            new_ow[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
            grade_role = discord.utils.get(guild.roles, name=cat.name)
            if grade_role and grade_role.name in GRADE_ROLES:
                new_ow[grade_role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )
            elif reg_role:
                new_ow[reg_role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )
            await cat.edit(overwrites=new_ow, reason="onboarding: lockdown categories")
            changed += 1

        await inter.response.send_message(
            f"🔐 セット完了：{changed}カテゴリを更新。#welcome は公開のままです。",
            ephemeral=True,
        )