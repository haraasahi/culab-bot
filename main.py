# main.py
# -*- coding: utf-8 -*-
"""
エントリポイント：
- Discord Client / CommandTree を初期化
- DB初期化
- コマンド群を登録
- スケジューラ起動
- 「作業終了」直後のユーザーメッセージを“本日の進捗”として保存
"""

import discord
from discord import app_commands

from bot.config import TOKEN, DEV_GUILD_ID
from bot.db import init_db, close_open_sessions_at_startup
from bot.utils import now_utc
from bot.commands import setup_all
from bot.scheduler import setup_schedulers
from bot.progress import is_waiting, save_progress
from bot.utils import now_utc

# ------- Discord Client -------
intents = discord.Intents.default()
intents.message_content = True
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

    # 休憩アラート/週次レポートなどの自動処理を起動
    setup_schedulers(client)
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
    if is_waiting(gid, cid, uid):
        content = (message.content or "").strip()
        if not content:
            return  # 画像だけ等はスキップ
        save_progress(gid, uid, content, now_utc())
        # 返信できない権限でも処理自体は完了させる
        try:
            await message.reply("📝 進捗を保存しました。/log で確認できます。", mention_author=False)
        except Exception:
            pass


def main():
    # DB初期化 & コマンド登録
    init_db()
    close_open_sessions_at_startup(now_utc())
    setup_all(tree, client)

    if not TOKEN:
        raise RuntimeError("環境変数 DISCORD_TOKEN が設定されていません。")

    client.run(TOKEN)


if __name__ == "__main__":
    main()
