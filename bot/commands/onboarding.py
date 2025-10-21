# bot/commands/onboarding.py
# -*- coding: utf-8 -*-
"""
ウェルカム導線（学年選択→名前入力→ロール付与→学年カテゴリ内に個人チャンネル作成）。
要件：
- チャンネル名は日本語OK（空白はハイフン、危険記号は除去）
- 参加時はDMで個別案内（DM不可の場合のみ #welcome に最小限の案内）
- 学年ロール（B3/B4/M1/M2/D）を付与。任意で共通ロール Registered も付与可能
- 学年ロール名と同一のカテゴリを @everyone 非表示／当該学年のみ可視で用意
- /welcome_post で案内貼り直し、/lockdown_categories でカテゴリ一括整備
注意：
- main.py 側で intents.members=True / intents.message_content=True
- Developer Portal で Server Members Intent / Message Content Intent を ON
"""

from __future__ import annotations

import re
from typing import Optional, Dict

import discord
from discord import app_commands

from ..config import GRADE_ROLES, WELCOME_CHANNEL_NAME
try:
    # 任意：共通ロール（登録済み）を使いたい場合に設定（例: "Registered"）
    from ..config import REGISTERED_ROLE_NAME  # type: ignore
except Exception:
    REGISTERED_ROLE_NAME = None  # type: ignore


# -------------------------
# 内部メモリ（Bot再起動で消えてOK）
# -------------------------
_PENDING_GRADE: Dict[int, str] = {}  # user_id -> grade(str)


# -------------------------
# ユーティリティ
# -------------------------
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
    """共通ロール（登録済みフラグ）を使う場合のみ作成/取得。"""
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


def _make_channel_name(display_name: str) -> str:
    """
    入力名を基本そのままチャンネル名に使用（Unicode可）。
    - 空白系はハイフンに
    - 危険/禁止記号は除去（# はUIで付くので不要）
    - 連続ハイフンを1つに圧縮
    """
    s = (display_name or "").strip()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[#@:`*/\\<>|\"'?%]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        s = "user"
    # 安全側で長さ制限（Discordは100文字上限）
    return s[:95]


def _cat_overwrites_for_role(
    guild: discord.Guild, visible_role: discord.Role
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    """カテゴリ：@everyone 非表示 / 指定ロールのみ可視"""
    return {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        visible_role: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        ),
    }


async def _ensure_category(
    guild: discord.Guild, name: str, visible_role: Optional[discord.Role] = None
) -> discord.CategoryChannel:
    """
    カテゴリを取得/作成。visible_role が指定されていれば、そのロールのみ可視にする。
    既存カテゴリでも不足していれば権限を補正。
    """
    cat = discord.utils.get(guild.categories, name=name)
    if cat:
        if visible_role and not cat.overwrites.get(visible_role):
            ow = dict(cat.overwrites)
            ow.update(_cat_overwrites_for_role(guild, visible_role))
            ow[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
            await cat.edit(overwrites=ow, reason="onboarding: fix category overwrites")
        return cat

    overwrites = (
        _cat_overwrites_for_role(guild, visible_role) if visible_role else None
    )
    return await guild.create_category(
        name=name, overwrites=overwrites, reason="onboarding: auto-create category"
    )


async def _ensure_welcome_channel(guild: discord.Guild) -> discord.TextChannel:
    """
    #welcome を取得/作成。@everyone が見えて書ける（DM不可時の案内に使用）。
    """
    ch = discord.utils.get(guild.text_channels, name=WELCOME_CHANNEL_NAME)
    if ch:
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


async def _reply_only_to_user(inter: discord.Interaction, content: str):
    """
    応答は「本人にだけ」見えるように：ギルド内なら ephemeral、DMなら通常送信。
    """
    if inter.response.is_done():
        # 既に初回応答済みなら followup を使う
        if inter.guild is None:
            await inter.followup.send(content)
        else:
            await inter.followup.send(content, ephemeral=True)
        return

    if inter.guild is None:
        await inter.response.send_message(content)
    else:
        await inter.response.send_message(content, ephemeral=True)


# -------------------------
# UI（学年セレクト + 名前モーダル）
# -------------------------
class GradeSelect(discord.ui.Select):
    def __init__(self):
        opts = [discord.SelectOption(label=g, value=g) for g in GRADE_ROLES]
        super().__init__(
            placeholder="学年を選択してください",
            min_values=1,
            max_values=1,
            options=opts,
            custom_id="grade_select",
        )

    async def callback(self, interaction: discord.Interaction):
        grade = self.values[0]
        _PENDING_GRADE[interaction.user.id] = grade
        await _reply_only_to_user(
            interaction,
            f"✅ 学年「**{grade}**」を選択しました。次に **名前** を入力してください。",
        )


class NameModal(discord.ui.Modal, title="名前の入力"):
    name = discord.ui.TextInput(
        label="あなたの名前",
        placeholder="氏名を入力",
        required=True,
        max_length=32,
    )

    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.user
        guild = interaction.guild
        if guild is None:
            return await _reply_only_to_user(interaction, "ギルド内で実行してください。")

        grade = _PENDING_GRADE.get(user.id)
        if grade is None:
            return await _reply_only_to_user(interaction, "先に学年を選択してください。")

        # 1) ロール付与（学年 + 任意の登録済みロール）
        grade_role = await _ensure_role(guild, grade)
        registered_role = await _ensure_registered_role(guild)
        roles_to_add = [grade_role] + ([registered_role] if registered_role else [])
        try:
            await user.add_roles(*roles_to_add, reason="onboarding: grade/registered")
        except discord.Forbidden:
            return await _reply_only_to_user(
                interaction,
                "ロール付与に失敗しました。Botに『ロールの管理』権限を付与してください。",
            )

        # 2) ニックネームを入力名（日本語OK）に変更（失敗しても続行）
        display_name = str(self.name).strip()
        try:
            await user.edit(nick=display_name)
        except discord.Forbidden:
            pass

        # 3) 学年カテゴリ（@everyone 非表示 / 学年のみ可視）
        category = await _ensure_category(
            guild, grade_role.name, visible_role=grade_role
        )

        # 4) 個人チャンネルを「入力名」を元に作成（Unicode可）
        base = _make_channel_name(display_name)
        name = base
        i = 2
        while discord.utils.get(category.text_channels, name=name) is not None:
            name = f"{base}-{i}"
            i += 1

        channel = await guild.create_text_channel(
            name=name,
            category=category,
            topic=f"Owner: {display_name}（{user.mention}） / 学年: {grade_role.name}",
            reason="onboarding: create personal channel",
        )

        # 5) 公開アナウンスは最小限（#welcome）。操作応答は本人のみ（ephemeral/DM）
        welcome = await _ensure_welcome_channel(guild)
        await welcome.send(
            f"🎉 {user.mention} さん 登録完了！ 学年 **{grade_role.name}** を付与し、"
            f"カテゴリ **{category.name}** に **#{channel.name}** を作成しました。"
            + (
                f"\n共通ロール **{registered_role.name}** も付与しました。"
                if registered_role
                else ""
            )
        )

        _PENDING_GRADE.pop(user.id, None)
        await _reply_only_to_user(
            interaction, "登録完了！ほかのチャンネルが見えるようになりました。"
        )


class OnboardView(discord.ui.View):
    """永続ビュー。Bot再起動後も動かすため setup() で client.add_view() する。"""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(GradeSelect())

    @discord.ui.button(
        label="名前を入力", style=discord.ButtonStyle.primary, custom_id="name_button"
    )
    async def open_modal(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_modal(NameModal())


# -------------------------
# コマンド登録 & リスナー
# -------------------------
def setup(tree: app_commands.CommandTree, client: discord.Client):
    # 永続ビューを登録（再起動後もSelect/Buttonが機能）
    try:
        client.add_view(OnboardView())
    except Exception:
        pass

    @tree.command(
        name="welcome_post", description="#welcome にウェルカム案内を投稿（管理者）"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_post(inter: discord.Interaction):
        guild = inter.guild
        if guild is None:
            return await _reply_only_to_user(inter, "ギルド内で実行してください。")
        ch = await _ensure_welcome_channel(guild)
        view = OnboardView()
        await ch.send(
            "ようこそ！\n"
            "1) 下のメニューで **学年** を選択\n"
            "2) **名前を入力** ボタンであなたの名前を送信\n"
            "→ Bot が **学年ロール付与** と **学年カテゴリ内に個人チャンネル作成** を行います。",
            view=view,
        )
        await _reply_only_to_user(inter, f"✅ {ch.mention} に案内を投稿しました。")

    # 新規参加時：DMで個別案内（DM不可時のみ #welcome にフォールバック）
    @client.event
    async def on_member_join(member: discord.Member):
        guild = member.guild
        view = OnboardView()
        try:
            await member.send(
                "👋 サーバーへようこそ！\n"
                "下のUIで **学年** を選び、**名前** を送信してください。\n"
                "→ ロール付与と個人チャンネル作成を自動で行います。",
                view=view,
            )
            return
        except discord.Forbidden:
            pass  # DM拒否時のみフォールバック

        ch = await _ensure_welcome_channel(guild)
        await ch.send(
            f"{member.mention} さん、ようこそ！\n"
            "DMが受け取れない設定のため、こちらから登録してください：\n"
            "1) 下のメニューで **学年** を選択\n"
            "2) **名前を入力** ボタンであなたの名前を送信",
            view=view,
        )

    # 既存カテゴリを一括整備：@everyone 非表示、学年カテゴリは学年ロール可視、
    # それ以外は（設定があれば）Registered を可視にする
    @tree.command(
        name="lockdown_categories",
        description="カテゴリ権限を一括設定（@everyone非表示、学年またはRegistered可視）",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def lockdown_categories(inter: discord.Interaction):
        guild = inter.guild
        if guild is None:
            return await _reply_only_to_user(inter, "ギルド内で実行してください。")
        reg_role = await _ensure_registered_role(guild)
        changed = 0

        # #welcome は全員見える/書けるに固定
        welcome = await _ensure_welcome_channel(guild)
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

        await _reply_only_to_user(
            inter, f"🔐 セット完了：{changed} 件のカテゴリを更新。#welcome は公開のままです。"
        )