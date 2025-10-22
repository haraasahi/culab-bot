# bot/commands/onboarding.py
# -*- coding: utf-8 -*-
"""
オンボーディング（#welcomeで完結・日本語チャンネル名対応）：

- 新規参加時：#welcome に案内メッセージ（UI付）を公開投稿（DMは使わない）
- 学年（B3/B4/M1/M2/D）を選択 → 名前を入力（日本語OK）
- 学年ロール付与、学年カテゴリ作成（@everyone非表示／学年ロール可視）
- 個人チャンネル名は“入力名をほぼそのまま”（空白→-、危険記号のみ除去）
  ※ APIが弾いた場合のみローマ字スラグに自動フォールバック
- culab ロールは「閲覧のみ」（read可・send不可）

main.py 側の注意:
- intents.members = True
- intents.message_content は不要（FalseでOK）
"""

from __future__ import annotations

import re
from typing import Optional, Dict

import discord
from discord import app_commands

from ..config import GRADE_ROLES, WELCOME_CHANNEL_NAME
try:
    from ..config import REGISTERED_ROLE_NAME  # 例: "Registered"（任意）
except Exception:
    REGISTERED_ROLE_NAME = None  # type: ignore

# culab ロール名（閲覧のみ）。config.py に CULAB_VIEW_ROLE_NAME があればそれを使用
try:
    from ..config import CULAB_VIEW_ROLE_NAME
except Exception:
    CULAB_VIEW_ROLE_NAME = "culab"  # type: ignore

# ローマ字フォールバック用（未インストールでも動くように弱い代替を用意）
try:
    from unidecode import unidecode  # pip install Unidecode（任意）
except Exception:
    def unidecode(s: str) -> str:
        return re.sub(r"[^\x00-\x7F]+", "", s)  # 簡易ASCII化

# 一時保持（Bot再起動で消えてOK）
_PENDING_GRADE: Dict[int, str] = {}  # user_id -> "B3" など


# -------------------------
#  ユーティリティ
# -------------------------
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

def _make_channel_name_jp(display_name: str) -> str:
    """
    入力名をそのままチャンネル名に使う：日本語OK
    - 空白系 → ハイフン
    - 危険/不正の可能性が高い記号を除去（#はUIで付くので不要）
    """
    s = (display_name or "").strip()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[#@:`*/\\<>|\"'?%]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        s = "user"
    return s[:95]  # 100未満に抑える

def _make_channel_name_ascii(display_name: str, fallback_suffix: str = "") -> str:
    """
    APIが日本語名を拒否した場合のフォールバック（ASCII）。
    """
    s = unidecode(display_name).lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9\-_]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        s = "user"
    if fallback_suffix:
        s = f"{s}-{fallback_suffix}"
    return s[:95]

def _cat_overwrites_for_role(
    guild: discord.Guild, visible_role: discord.Role, culab: Optional[discord.Role]
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    """
    カテゴリ権限：
      - @everyone 非表示
      - 学年ロール：閲覧・送信可
      - culab（存在すれば）：閲覧のみ
    """
    base = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        visible_role: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        ),
    }
    if culab:
        base[culab] = discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=False
        )
    return base

async def _ensure_category(
    guild: discord.Guild, name: str, visible_role: Optional[discord.Role] = None
) -> discord.CategoryChannel:
    culab = _get_culab_view_role(guild)
    cat = discord.utils.get(guild.categories, name=name)
    if cat:
        # 権限が不足していたら補正
        need_edit = False
        ow = dict(cat.overwrites)
        if visible_role and not ow.get(visible_role):
            ow.update(_cat_overwrites_for_role(guild, visible_role, culab))
            need_edit = True
        if culab and not ow.get(culab):
            ow[culab] = discord.PermissionOverwrite(
                view_channel=True, read_message_history=True, send_messages=False
            )
            need_edit = True
        if need_edit:
            await cat.edit(overwrites=ow, reason="onboarding: fix category overwrites")
        return cat

    overwrites = _cat_overwrites_for_role(guild, visible_role, culab) if visible_role else {
        guild.default_role: discord.PermissionOverwrite(view_channel=False)
    }
    return await guild.create_category(
        name=name, overwrites=overwrites, reason="onboarding: auto-create category"
    )

async def _ensure_welcome_channel(guild: discord.Guild) -> discord.TextChannel:
    """
    #welcome を取得/作成。公開（全員見える/書ける）にする。
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

async def _reply_ephemeral(inter: discord.Interaction, content: str):
    """
    #welcome内で操作しても、返信は基本ephemeral（本人にのみ表示）。
    """
    if inter.response.is_done():
        await inter.followup.send(content, ephemeral=True)
    else:
        await inter.response.send_message(content, ephemeral=True)


# -------------------------
#  UI（学年セレクト + 名前モーダル）
# -------------------------
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
        await _reply_ephemeral(
            interaction,
            f"✅ 学年「**{grade}**」を選択しました。次に **名前** を入力してください。",
        )

class NameModal(discord.ui.Modal, title="名前の入力（日本語OK）"):
    # ※ ラベルは45文字以下
    name = discord.ui.TextInput(
        label="あなたの名前（日本語OK）",
        placeholder="名前または通称を入力（後で変更不可）",
        required=True,
        max_length=32,
    )

    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.user
        guild = interaction.guild
        if guild is None:
            return await _reply_ephemeral(interaction, "ギルド内で実行してください。")

        grade = _PENDING_GRADE.get(user.id)
        if grade is None:
            return await _reply_ephemeral(interaction, "先に学年を選択してください。")

        # 1) ロール付与
        grade_role = await _ensure_role(guild, grade)
        registered_role = await _ensure_registered_role(guild)
        roles_to_add = [grade_role] + ([registered_role] if registered_role else [])
        try:
            await user.add_roles(*roles_to_add, reason="onboarding: grade/registered")
        except discord.Forbidden:
            return await _reply_ephemeral(
                interaction, "ロール付与に失敗。Botに『ロールの管理』権限を付与してください。"
            )

        # 2) ニックネームを入力名に変更（失敗しても続行）
        display_name = str(self.name).strip()
        try:
            await user.edit(nick=display_name)
        except discord.Forbidden:
            pass

        # 3) 学年カテゴリ（@everyone 非表示 / 学年のみ可視 + culabは閲覧のみ）
        category = await _ensure_category(guild, grade_role.name, visible_role=grade_role)

        # 4) 個人チャンネル名：まずは日本語名で試す
        base = _make_channel_name_jp(display_name)
        final_name = base
        idx = 2
        while discord.utils.get(category.text_channels, name=final_name) is not None:
            final_name = f"{base}-{idx}"
            idx += 1

        # チャンネル権限（カテゴリをベースに、culab閲覧のみを明示）
        ch_ow = dict(category.overwrites)
        culab = _get_culab_view_role(guild)
        if culab:
            ch_ow[culab] = discord.PermissionOverwrite(
                view_channel=True, read_message_history=True, send_messages=False
            )

        # 5) 作成（日本語名がAPIで弾かれたらASCIIにフォールバック）
        try:
            channel = await guild.create_text_channel(
                name=final_name,
                category=category,
                topic=f"Owner: {display_name}（{user.mention}） / 学年: {grade_role.name}",
                overwrites=ch_ow,
                reason="onboarding: create personal channel",
            )
        except discord.HTTPException as e:
            # 400 Invalid Form Body 等で失敗した場合のみASCIIにフォールバック
            ascii_base = _make_channel_name_ascii(display_name)
            fallback = ascii_base
            n = 2
            while discord.utils.get(category.text_channels, name=fallback) is not None:
                fallback = f"{ascii_base}-{n}"
                n += 1
            channel = await guild.create_text_channel(
                name=fallback,
                category=category,
                topic=f"Owner: {display_name}（{user.mention}） / 学年: {grade_role.name}",
                overwrites=ch_ow,
                reason=f"onboarding: fallback from JP name ({final_name}) due to {e}",
            )
            final_name = fallback  # 実際のチャンネル名に同期

        # 6) #welcome に公開アナウンス（UI操作レスはephemeral）
        welcome = await _ensure_welcome_channel(guild)
        await welcome.send(
            f"🎉 {user.mention} さん 登録完了！ 学年 **{grade_role.name}** を付与し、"
            f"カテゴリ **{category.name}** に **#{channel.name}** を作成しました。"
            + (f"\n共通ロール **{registered_role.name}** も付与しました。" if registered_role else "")
        )

        _PENDING_GRADE.pop(user.id, None)
        await _reply_ephemeral(interaction, "登録完了！ほかのチャンネルが見えるようになりました。")


class OnboardView(discord.ui.View):
    """永続ビュー（再起動後も動作）"""
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(GradeSelect())

    @discord.ui.button(label="名前を入力", style=discord.ButtonStyle.primary, custom_id="name_button")
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(NameModal())


# -------------------------
#  セットアップ（イベントのみ登録）
# -------------------------
def setup(tree: app_commands.CommandTree, client: discord.Client):
    # 永続ビュー登録（再起動後もUIが有効）
    try:
        client.add_view(OnboardView())
    except Exception:
        pass

    # 新規参加時：#welcome に公開案内（DMは使わない）
    @client.event
    async def on_member_join(member: discord.Member):
        guild = member.guild
        ch = await _ensure_welcome_channel(guild)
        view = OnboardView()
        await ch.send(
            f"👋 {member.mention} さん、ようこそ！\n"
            "以下の手順で登録してください：\n"
            "1) 下のメニューで **学年** を選択\n"
            "2) **名前を入力** ボタンであなたの名前を送信（日本語OK）\n"
            "→ Bot が **学年ロール付与** と **学年カテゴリ内に個人チャンネル作成** を行います。",
            view=view,
        )