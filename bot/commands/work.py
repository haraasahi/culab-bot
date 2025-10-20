# bot/commands/work.py
import discord
from discord import app_commands
from typing import Callable
from ..config import WORK_TYPES
from ..db import connect
from ..utils import now_utc, fmt_duration
from ..stats import get_active, sum_week
from ..views import WorkButtons
from ..progress import arm_progress_capture 

# ===== ハンドラ本体 =====
async def handle_start_break(inter: discord.Interaction):
    uid, gid = str(inter.user.id), str(inter.guild_id)
    with connect() as con:
        act = get_active(con, uid, gid)
        if not act:
            return await inter.response.send_message("作業セッションがありません。/start_work から始めてください。", ephemeral=True)
        sid, _, _, start_ts, _, break_sec, _, status, _, _ = act
        if status == "on_break":
            return await inter.response.send_message("すでに休憩中です。/end_break で終了してください。", ephemeral=True)

        worked = max(0, (now_utc() - start_ts) - (break_sec or 0))
        now = now_utc()

        # セッションを休憩中にし、未クローズの休憩区間が無いことを保証してから新規レコード
        con.execute("UPDATE sessions SET status='on_break', break_started_ts=?, break_alert_sent=0 WHERE id=?",
                    (now, sid))
        # 念のため開きっぱなしの休憩行があれば閉じる
        con.execute("UPDATE session_breaks SET end_ts=? WHERE session_id=? AND end_ts IS NULL", (now, sid))
        # 新しい休憩区間を開始
        con.execute("INSERT INTO session_breaks(session_id, start_ts) VALUES(?,?)", (sid, now))

    await inter.response.send_message(f"⏸️ {inter.user.mention} さんが **休憩開始**。これまでの作業：{fmt_duration(worked)}")

async def handle_end_break(inter: discord.Interaction):
    uid, gid = str(inter.user.id), str(inter.guild_id)
    with connect() as con:
        act = get_active(con, uid, gid)
        if not act:
            return await inter.response.send_message("作業セッションがありません。/start_work から始めてください。", ephemeral=True)
        sid, _, _, _, _, break_sec, break_started, status, _, _ = act
        if status != "on_break" or break_started is None:
            return await inter.response.send_message("いまは休憩中ではありません。", ephemeral=True)

        now = now_utc()
        add = max(0, now - break_started)
        con.execute("""
            UPDATE sessions
            SET status='working', break_started_ts=NULL, break_seconds=break_seconds+?, break_alert_sent=0
            WHERE id=?
        """, (add, sid))
        # 直近の未終了休憩をクローズ
        con.execute("UPDATE session_breaks SET end_ts=? WHERE session_id=? AND end_ts IS NULL",
                    (now, sid))

    await inter.response.send_message(f"▶️ {inter.user.mention} さんが休憩終了。今回の休憩：{fmt_duration(add)}")

async def handle_end_work(inter: discord.Interaction):
    uid, gid = str(inter.user.id), str(inter.guild_id)
    with connect() as con:
        act = get_active(con, uid, gid)
        if not act:
            return await inter.response.send_message("作業セッションがありません。/start_work から始めてください。", ephemeral=True)
        sid, _, _, start_ts, _, break_sec, break_started, status, work_type, _ = act
        now = now_utc()
        if status == "on_break" and break_started is not None:
            break_sec += max(0, now - break_started)
            con.execute("UPDATE session_breaks SET end_ts=? WHERE session_id=? AND end_ts IS NULL", (now, sid))

        work = max(0, (now - start_ts) - break_sec)
        con.execute("""
            UPDATE sessions
            SET end_ts=?, break_seconds=?, break_started_ts=NULL, status='closed'
            WHERE id=?
        """, (now, break_sec, sid))
        week_total = sum_week(con, uid, gid, now)

    # ★ このチャンネルで当人の“次メッセージ”を進捗保存として受け付ける
    arm_progress_capture(gid, str(inter.channel_id), uid)

    await inter.response.send_message(
        f"✅ {inter.user.mention} さんが{work_type}を終了しました。\n"
        f"・今回：**{fmt_duration(work)}**\n"
        f"・今週累計：**{fmt_duration(week_total)}**\n"
        f"📝 **続けて進捗をメッセージで教えてください**（このチャンネルに書くと**本日の進捗**として保存します）"
    )

# ===== Slashコマンド登録 =====
def setup(tree: app_commands.CommandTree, client: discord.Client):
    @tree.command(name="start_work", description="作業を開始します（タイプ選択）")
    @app_commands.choices(task_type=[app_commands.Choice(name=t, value=t) for t in WORK_TYPES])
    async def start_work(inter: discord.Interaction, task_type: app_commands.Choice[str]):
        uid, gid = str(inter.user.id), str(inter.guild_id)
        ttype = task_type.value if task_type else "その他"
        now = now_utc()

        auto_closed_msg = ""
        with connect() as con:
            act = get_active(con, uid, gid)
            if act:
                # ここで“未終了セッション”を自動クローズ
                sid, _, _, start_ts, _, break_sec, break_started, status, prev_type, _ = act
                adj_break = break_sec or 0
                if status == "on_break" and break_started is not None:
                    adj_break += max(0, now - break_started)
                    # 開いている休憩レコードも閉じる
                    con.execute(
                        "UPDATE session_breaks SET end_ts=? WHERE session_id=? AND end_ts IS NULL",
                        (now, sid),
                    )

                prev_work = max(0, (now - start_ts) - adj_break)
                con.execute("""
                    UPDATE sessions
                    SET end_ts=?, break_seconds=?, break_started_ts=NULL, status='closed'
                    WHERE id=?
                """, (now, adj_break, sid))

                auto_closed_msg = f"ℹ️ 前回の未終了セッション（{prev_type}）を自動終了しました：**{fmt_duration(prev_work)}**\n"

            # 新しいセッションを開始
            con.execute("""
                INSERT INTO sessions(user_id, guild_id, start_ts, status, work_type)
                VALUES(?,?,?,?,?)
            """, (uid, gid, now, 'working', ttype))

        # クイックボタンを付与
        view = WorkButtons(
            owner_user_id=uid,
            on_start_break=handle_start_break,
            on_end_break=handle_end_break,
            on_end_work=handle_end_work,
        )

        await inter.response.send_message(
            auto_closed_msg +
            f"📣 {inter.user.mention} さんが{ttype}の **作業開始** しました。集中モード突入！",
            view=view
        )

