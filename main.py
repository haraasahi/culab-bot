# main.py
# -*- coding: utf-8 -*-
"""
エントリポイント：
- Discord Client / CommandTree を初期化
- DB初期化（未終了セッションのクローズ含む）
- コマンド群を登録
- スケジューラ起動（休憩ゆる通知・カレンダー1日前リマインド）
- 「作業終了」直後のユーザーメッセージを“本日の進捗”として保存
"""

import discord
from discord import app_commands

from bot.config import TOKEN, DEV_GUILD_ID
from bot.db import init_db, close_open_sessions_at_startup
from bot.commands import setup_all
from bot.progress import consume_waiting, save_progress
from bot.utils import now_utc

# スケジューラ import（新名 start_schedulers を優先、旧名に後方互換）
try:
    from bot.scheduler import start_schedulers  # 新しい実装名
except ImportError:  # 旧実装名の互換
    try:
        from bot.scheduler import setup_schedulers as start_schedulers  # type: ignore
    except Exception:
        start_schedulers = None  # type: ignore


# ------- Discord Client -------
intents = discord.Intents.default()
# 進捗の自動保存に必要
intents.message_content = True
# オンボーディング等でメンバー情報が必要な場合は True（開発者ポータル側でも有効化必須）
intents.members = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


@client.event
async def on_ready():
    """起動時：スラッシュコマンド同期 & スケジューラ起動"""
    try:
        if DEV_GUILD_ID:
            guild = discord.Object(id=int(DEV_GUILD_ID))
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)  # ギルド同期（即時反映）
            print(f"Synced commands to DEV guild {DEV_GUILD_ID}")
        else:
            await tree.sync()  # グローバル同期（数分かかる場合あり）
            print("Synced global commands")
    except Exception as e:
        print("Command sync error:", e)

    # スケジューラ（休憩ゆる通知・カレンダー1日前リマインド 等）
    try:
        if callable(start_schedulers):
            start_schedulers(client)  # type: ignore[misc]
            print("✅ schedulers started")
        else:
            print("⚠️ scheduler not started: start_schedulers is not available")
    except Exception as e:
        print("Scheduler start error:", e)

    print(f"✅ Logged in as {client.user} (ID: {client.user.id})")


@client.event
async def on_message(message: discord.Message):
    """
    「作業終了」直後に同じチャンネルへユーザーが送ったメッセージを
    “本日の進捗”として保存するフック。
    """
    # Bot自身やDMは対象外
    if message.author.bot or not message.guild:
        return

    gid = str(message.guild.id)
    cid = str(message.channel.id)
    uid = str(message.author.id)

    # 対象ユーザーが「進捗待ち」状態か？

    if consume_waiting(gid, cid, uid):
        content = (message.content or "").strip()
        if not content:
            return  # 画像だけ等はスキップ
        save_progress(gid, uid, content, now_utc())
        try:
            await message.reply("📝 進捗を保存しました。/log で確認できます。", mention_author=False)
        except Exception:
            pass

        # 返信できない権限でも処理自体は完了させる
        try:
            await message.reply("📝 進捗を保存しました。/log で確認できます。", mention_author=False)
        except Exception:
            pass


def main():
    # DB初期化 & コマンド登録
    init_db()
    # Bot再起動時に未終了セッションを強制クローズ
    close_open_sessions_at_startup(now_utc())

    # スラッシュコマンド登録
    setup_all(tree, client)

    if not TOKEN:
        raise RuntimeError("環境変数 DISCORD_TOKEN が設定されていません。")

    # ログイン
    client.run(TOKEN)


if __name__ == "__main__":
    main()