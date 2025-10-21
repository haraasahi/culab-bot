# bot/commands/onboarding.py
# -*- coding: utf-8 -*-
"""
ウェルカム導線：
- #welcome に学年セレクト + 名前入力ボタンを表示
- 学年選択 → 名前入力 → 学年ロール付与（必要なら Registered も）
- 学年ロール名と同じカテゴリを用意（@everyone 非表示 / 学年ロールのみ可視）
- そのカテゴリ内に本人用テキストチャンネルを作成
- 新規参加者には自動で案内を投下（on_member_join）
- /welcome_post で手動再掲、/lockdown_categories で一括権限整備

※ main.py 側で Intents に `intents.members = True` を設定し、
  Developer Portal の「Server Members Intent」「Message Content Intent」を ON にしてください。
"""

from __future__ import annotations

import re
from typing import Optional, Dict

import discord
from discord import app_commands

from ..config import GRADE_ROLES, WELCOME_CHANNEL_NAME
# 任意：共通ロール名（登録済みフラグ）。config に無ければ None として扱う
try:
    from ..config import REGISTERED_ROLE_NAME  # 例: "Registered"
except Exception:
    REGISTERED_ROLE_NAME = None  # type: ignore[assignment]

# -------------------------
# 内部メモリ保持（Bot再起動で消えてOK）
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
    """共通ロール（登録済み）を使いたい場合のみ作成/取得。REGISTERED_ROLE_NAME が None なら未使用。"""
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


def _slugify_channel_name(s: str) -> str:
    """テキストチャンネル名に安全なスラグへ（日本語名はトピックに格納）。"""
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "-", s)          # 空白→ハイフン
    s = re.sub(r"[^a-z0-9\-_]", "", s)  # 許可外を除去
    return s or "user"


def _cat_overwrites_for_role(
    guild: discord.Guild, visible_role: discord.Role
) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
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
    既存カテゴリでも不足していれば上書きを補正する。
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
    #welcome を取得/作成。@everyone が見えて書けるようにしておく（案内/操作のため）。
    """
    ch = discord.utils.get(guild.text_channels, name=WELCOME_CHANNEL_NAME)
    if ch:
        # 念のため権限を補正
        ow = dict(ch.overwrites)
        ow[guild.default_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        )
        await ch.edit(overwrites=ow, reason="onboarding: ensure welcome open")
        return ch

    ch = await guild.create_text_channel(
        WELCOME_CHANNEL_NAME, reason="onboarding: auto-create welcome"
    )
    ow = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        )
    }
    await ch.edit(overwrites=ow, reason="onboarding: ensure welcome open")
    return ch


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
        await interaction.response.send_message(
            f"✅ 学年「**{grade}**」を選択しました。次に **名前** を入力してください。",
            ephemeral=True,
        )


class NameModal(discord.ui.Modal, title="名前の入力"):
    name = discord.ui.TextInput(
        label="あなたの名前（例：山田太郎）", placeholder="氏名を入力", required=True, max_length=32
    )

    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.user
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "ギルド内で実行してください。", ephemeral=True
            )

        grade = _PENDING_GRADE.get(user.id)
        if grade is None:
            return await interaction.response.send_message(
                "先に学年を選択してください。", ephemeral=True
            )

        # 1) ロール確保 & 付与
        grade_role = await _ensure_role(guild, grade)
        registered_role = await _ensure_registered_role(guild)  # 使わない設定なら None

        roles_to_add = [grade_role] + ([registered_role] if registered_role else [])
        try:
            await user.add_roles(*roles_to_add, reason="onboarding: grade/registered")
        except discord.Forbidden:
            return await interaction.response.send_message(
                "ロール付与に失敗しました。Botに『ロールの管理』権限を付与してください。",
                ephemeral=True,
            )

        # 2) ニックネーム変更（失敗しても続行）
        try:
            await user.edit(nick=str(self.name))
        except discord.Forbidden:
            pass

        # 3) 学年カテゴリ（@everyone 非表示 / 学年のみ可視）
        category = await _ensure_category(guild, grade_role.name, visible_role=grade_role)

        # 4) 個人チャンネル作成（カテゴリ継承＝同学年は閲覧可。本人だけにしたい場合は個別overwritesを足す）
        safe = _slugify_channel_name(str(self.name))
        base, i = safe, 2
        while discord.utils.get(category.text_channels, name=safe) is not None:
            safe = f"{base}-{i}"
            i += 1

        channel = await guild.create_text_channel(
            name=safe,
            category=category,
            topic=f"Owner: {user.display_name} / 学年: {grade_role.name}",
            reason="onboarding: create personal channel",
        )

        # 5) ウェルカムに結果をアナウンス（公開）
        welcome = await _ensure_welcome_channel(guild)
        await welcome.send(
            f"🎉 {user.mention} さん、サーバーへようこそ！\n"
            f"学年ロール **{grade_role.name}** を付与しました。"
            f" カテゴリ **{category.name}** に **#{channel.name}** を作成しました。"
            + (
                f"\n共通ロール **{registered_role.name}** も付与しました。"
                if registered_role
                else ""
            )
        )

        # 6) 後始末
        _PENDING_GRADE.pop(user.id, None)
        await interaction.response.send_message(
            "登録完了！ほかのチャンネルが見えるようになりました。", ephemeral=True
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
        # 起動順の都合で失敗しても致命ではない（/welcome_post で都度添付できる）
        pass

    @tree.command(
        name="welcome_post", description="#welcome にウェルカム案内を投稿（管理者）"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_post(inter: discord.Interaction):
        guild = inter.guild
        if guild is None:
            return await inter.response.send_message(
                "ギルド内で実行してください。", ephemeral=True
            )
        ch = await _ensure_welcome_channel(guild)
        view = OnboardView()
        await ch.send(
            "ようこそ！\n"
            "1) 下のメニューで **学年** を選択\n"
            "2) **名前を入力** ボタンであなたの名前を送信\n"
            "→ Bot が **学年ロール付与** と **学年カテゴリ内に個人チャンネル作成** を行います。",
            view=view,
        )
        await inter.response.send_message(
            f"✅ {ch.mention} にウェルカム案内を投稿しました。", ephemeral=True
        )

    # 既存カテゴリを一括で整備：@everyone 非表示、学年カテゴリは学年ロール可視、
    # それ以外は（設定があれば）Registered を可視にする
    @tree.command(
        name="lockdown_categories",
        description="カテゴリ権限を一括設定（@everyone非表示、学年またはRegistered可視）",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def lockdown_categories(inter: discord.Interaction):
        guild = inter.guild
        if guild is None:
            return await inter.response.send_message(
                "ギルド内で実行してください。", ephemeral=True
            )
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
            if cat == welcome.category:
                # welcome がカテゴリ直下にある場合のケアは任意
                pass

            new_ow = dict(cat.overwrites)
            # まず @everyone を非表示に
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
            f"🔐 セット完了：{changed} 件のカテゴリを更新。#welcome は全員が見える設定にしました。",
            ephemeral=True,
        )

    # 新規参加時に自動で案内を投稿
    @client.event
    async def on_member_join(member: discord.Member):
        guild = member.guild
        ch = await _ensure_welcome_channel(guild)
        view = OnboardView()
        await ch.send(
            f"👋 {member.mention} さん、ようこそ！\n"
            "以下の手順で登録してください：\n"
            "1) 下のメニューで **学年** を選択\n"
            "2) **名前を入力** ボタンであなたの名前を送信\n"
            "→ Bot が **学年ロール付与** と **学年カテゴリ内に個人チャンネル作成** を行います。",
            view=view,
        )