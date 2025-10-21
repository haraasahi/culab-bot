# bot/commands/onboarding.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Optional, Dict

import discord
from discord import app_commands

from ..config import GRADE_ROLES, WELCOME_CHANNEL_NAME
try:
    from ..config import REGISTERED_ROLE_NAME  # 任意: "Registered" など
except Exception:
    REGISTERED_ROLE_NAME = None  # type: ignore

# 閲覧のみ付与するロール名（存在すれば適用）
try:
    from ..config import CULAB_VIEW_ROLE_NAME
except Exception:
    CULAB_VIEW_ROLE_NAME = "culab"  # type: ignore

# ---- 内部メモリ（Bot再起動で消えてOK） ----
_PENDING_GRADE: Dict[int, str] = {}  # user_id -> grade

# ---- 制約（英数字のみ）----
# 2〜32文字の半角英数字のみ許可（チャンネル名/ニックネームを一致させるため）
ALNUM_NAME_RE = re.compile(r"^[A-Za-z0-9]{2,32}$")


# ---- ユーティリティ ----
def _find_role(guild: discord.Guild, name: str) -> Optional[discord.Role]:
    return discord.utils.get(guild.roles, name=name)

def _find_role_ci(guild: discord.Guild, name: str) -> Optional[discord.Role]:
    lname = (name or "").lower()
    for r in guild.roles:
        if r.name.lower() == lname:
            return r
    return None

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
    role = _find_role(guild, REGISTERED_ROLE_NAME)
    if role:
        return role
    return await guild.create_role(
        name=REGISTERED_ROLE_NAME,
        mentionable=False,
        reason="onboarding: auto-create registered role",
    )

def _get_culab_view_role(guild: discord.Guild) -> Optional[discord.Role]:
    return _find_role_ci(guild, CULAB_VIEW_ROLE_NAME)

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
    culab = _get_culab_view_role(guild)

    if cat:
        need_edit = False
        ow = dict(cat.overwrites)

        if visible_role and not ow.get(visible_role):
            ow.update(_cat_overwrites_for_role(guild, visible_role))
            need_edit = True

        if culab and not ow.get(culab):
            ow[culab] = discord.PermissionOverwrite(
                view_channel=True, read_message_history=True, send_messages=False
            )
            need_edit = True

        if need_edit:
            await cat.edit(overwrites=ow, reason="onboarding: fix category overwrites")
        return cat

    base_ow = (
        _cat_overwrites_for_role(guild, visible_role)
        if visible_role
        else {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    )
    if culab:
        base_ow[culab] = discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=False
        )

    return await guild.create_category(
        name=name, overwrites=base_ow, reason="onboarding: auto-create category"
    )

async def _ensure_welcome_channel(guild: discord.Guild) -> discord.TextChannel:
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
    """ギルド内なら ephemeral、DMなら通常送信。"""
    if inter.response.is_done():
        if inter.guild is None:
            await inter.followup.send(content)
        else:
            await inter.followup.send(content, ephemeral=True)
        return
    if inter.guild is None:
        await inter.response.send_message(content)
    else:
        await inter.response.send_message(content, ephemeral=True)


# ---- UI（学年セレクト + 名前モーダル）----
class GradeSelect(discord.ui.Select):
    def __init__(self):
        opts = [discord.SelectOption(label=g, value=g) for g in GRADE_ROLES]
        super().__init__(
            placeholder="学年を選択してください",
            min_values=1, max_values=1, options=opts, custom_id="grade_select",
        )

    async def callback(self, interaction: discord.Interaction):
        grade = self.values[0]
        _PENDING_GRADE[interaction.user.id] = grade
        await _reply_only_to_user(
            interaction,
            "✅ 学年を選択しました。続いて **名前** を英数字のみ（例: asahi2）で入力してください。",
        )

class NameModal(discord.ui.Modal, title="名前の入力（英数字のみ）"):
    name = discord.ui.TextInput(
        label="あなたの名前（例：asahi2）",
        placeholder="半角英数字のみ・2〜32文字",
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

        # --- 1) 名前バリデーション（英数字のみ・2〜32）---
        raw = str(self.name).strip()
        if not ALNUM_NAME_RE.fullmatch(raw):
            return await _reply_only_to_user(
                interaction,
                "⚠️ **英数字のみ（2〜32文字）**で入力してください。例：`asahi2`\n"
                "もう一度 **「名前を入力」** ボタンからやり直してください。",
            )
        base = raw.lower()  # チャンネル/ニックネーム統一のため小文字化

        # --- 2) ロール付与（学年 + 任意の登録済みロール）---
        grade_role = await _ensure_role(guild, grade)
        registered_role = await _ensure_registered_role(guild)
        roles_to_add = [grade_role] + ([registered_role] if registered_role else [])
        try:
            await user.add_roles(*roles_to_add, reason="onboarding: grade/registered")
        except discord.Forbidden:
            return await _reply_only_to_user(
                interaction, "ロール付与に失敗。Botに『ロールの管理』権限を付与してください。"
            )

        # --- 3) 学年カテゴリ（@everyone 非表示 / 学年のみ可視 + culabは閲覧のみ）---
        category = await _ensure_category(guild, grade_role.name, visible_role=grade_role)

        # --- 4) 衝突回避（既に同名チャンネルがある場合は連番付与）---
        final_name = base
        i = 2
        while discord.utils.get(category.text_channels, name=final_name) is not None:
            final_name = f"{base}-{i}"
            i += 1

        # ニックネームもチャンネル名と完全一致にする（衝突で連番が付いたらニックネームにも反映）
        try:
            await user.edit(nick=final_name)
        except discord.Forbidden:
            # ニックネーム変更できなくても続行（チャンネルは作成）
            pass

        # --- 5) 個人チャンネル作成（culab は閲覧のみ）---
        ch_ow = dict(category.overwrites)
        culab = _get_culab_view_role(guild)
        if culab:
            ch_ow[culab] = discord.PermissionOverwrite(
                view_channel=True, read_message_history=True, send_messages=False
            )

        channel = await guild.create_text_channel(
            name=final_name,
            category=category,
            topic=f"Owner: {final_name}（{user.mention}） / 学年: {grade_role.name}",
            overwrites=ch_ow,
            reason="onboarding: create personal channel",
        )

        # --- 6) 最小限の公開アナウンス（welcome）。操作応答は本人のみ ---
        welcome = await _ensure_welcome_channel(guild)
        await welcome.send(
            f"🎉 {user.mention} さん 登録完了！ 学年 **{grade_role.name}** を付与し、"
            f"カテゴリ **{category.name}** に **#{channel.name}** を作成しました。"
            + (f"\n共通ロール **{registered_role.name}** も付与しました。" if registered_role else "")
        )

        _PENDING_GRADE.pop(user.id, None)
        if final_name != base:
            await _reply_only_to_user(
                interaction,
                f"登録完了！同名チャンネルがあったため **{final_name}** に調整しました。"
                "（ニックネームとチャンネル名は一致しています）",
            )
        else:
            await _reply_only_to_user(
                interaction, "登録完了！ほかのチャンネルが見えるようになりました。"
            )


class OnboardView(discord.ui.View):
    """永続ビュー（再起動後も動作）"""
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(GradeSelect())

    @discord.ui.button(label="名前を入力", style=discord.ButtonStyle.primary, custom_id="name_button")
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(NameModal())


# ---- コマンド登録 & リスナー ----
def setup(tree: app_commands.CommandTree, client: discord.Client):
    try:
        client.add_view(OnboardView())
    except Exception:
        pass

    @tree.command(name="welcome_post", description="#welcome にウェルカム案内を投稿（管理者）")
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
            "2) **名前を入力** ボタンから、**半角英数字のみ（例: asahi2）** で送信\n"
            "→ Bot が **学年ロール付与** と **学年カテゴリ内に個人チャンネル作成** を行います。",
            view=view,
        )
        await _reply_only_to_user(inter, f"✅ {ch.mention} に案内を投稿しました。")

    @client.event
    async def on_member_join(member: discord.Member):
        guild = member.guild
        view = OnboardView()
        try:
            await member.send(
                "👋 サーバーへようこそ！\n"
                "1) セレクトで **学年** を選択\n"
                "2) **名前を入力** ボタンから、**半角英数字のみ（例: asahi2）** を送信\n"
                "→ ロール付与と個人チャンネル作成を自動で行います。",
                view=view,
            )
            return
        except discord.Forbidden:
            pass

        ch = await _ensure_welcome_channel(guild)
        await ch.send(
            f"{member.mention} さん、ようこそ！\n"
            "DMが受け取れない設定のため、こちらから登録してください：\n"
            "1) セレクトで **学年** を選択\n"
            "2) **名前を入力** ボタンから、**半角英数字のみ** を送信",
            view=view,
        )

    @tree.command(
        name="lockdown_categories",
        description="カテゴリ権限を一括設定（@everyone非表示、学年/Registered可視 + culab閲覧のみ）",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def lockdown_categories(inter: discord.Interaction):
        guild = inter.guild
        if guild is None:
            return await _reply_only_to_user(inter, "ギルド内で実行してください。")
        reg_role = await _ensure_registered_role(guild)
        culab = _get_culab_view_role(guild)
        changed = 0

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

            if culab:
                new_ow[culab] = discord.PermissionOverwrite(
                    view_channel=True, read_message_history=True, send_messages=False
                )

            await cat.edit(overwrites=new_ow, reason="onboarding: lockdown categories")
            changed += 1

        await _reply_only_to_user(
            inter, f"🔐 セット完了：{changed} 件のカテゴリを更新（culab は閲覧のみ）。#welcome は公開のままです。"
        )