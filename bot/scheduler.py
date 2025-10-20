# bot/scheduler.py
import discord
from discord.ext import tasks
from .db import connect
from .utils import now_utc, fmt_duration, week_start_ts
from .config import USER_REPORT_CHANNELS, WORK_TYPES

def setup_schedulers(client: discord.Client):

    @tasks.loop(minutes=5)
    async def break_alert_monitor():
        """
        休憩開始から2時間経過していて、まだ alert していない on_break セッションに
        ユーザーDMで “ゆる通知” を送る（本人だけに見える）。
        """
        now = now_utc()
        with connect() as con:
            rows = con.execute("""
                SELECT id, user_id, guild_id, break_started_ts
                FROM sessions
                WHERE status='on_break' AND break_started_ts IS NOT NULL AND break_alert_sent=0
            """).fetchall()
            for sid, uid, gid, bst in rows:
                if bst is None: continue
                if (now - bst) >= 7200:
                    try:
                        user = await client.fetch_user(int(uid))
                        if user is not None:
                            await user.send("💤 休憩開始から2時間が経過しました。そろそろ作業に戻りませんか？")
                    except Exception:
                        pass
                    con.execute("UPDATE sessions SET break_alert_sent=1 WHERE id=?", (sid,))

    @tasks.loop(hours=1)
    async def weekly_report():
        """
        毎週木曜 9:00 JST に、ユーザー別に指定チャンネルへ週次レポートを投稿。
        """
        from datetime import datetime
        from .config import JST
        now = datetime.now(JST)
        if not (now.weekday() == 4 and now.hour == 9):  # 金曜=4
            return

        # 各対象ユーザーごとに、在籍ギルドを横断して該当チャンネルへ投稿
        for uid, channel_name in USER_REPORT_CHANNELS.items():
            for guild in client.guilds:
                ch = discord.utils.get(guild.text_channels, name=channel_name)
                if not ch:
                    continue

                with connect() as con:
                    rows = con.execute("""
                        SELECT work_type, start_ts, end_ts, break_seconds
                        FROM sessions
                        WHERE user_id=? AND guild_id=? AND status='closed' AND end_ts>=?
                    """, (uid, str(guild.id), week_start_ts(now.timestamp()))).fetchall()

                data = {t: 0 for t in WORK_TYPES}
                for t, s, e, b in rows:
                    data[t] += max(0, (e - s) - b)

                msg = [f"🗓️ <@{uid}> さんの今週レポート"]
                for t in WORK_TYPES:
                    msg.append(f"・{t}: **{fmt_duration(data[t])}**")
                msg.append(f"―――――\n合計: **{fmt_duration(sum(data.values()))}**")
                try:
                    await ch.send("\n".join(msg))
                except Exception:
                    pass

    # 起動
    break_alert_monitor.start()
    weekly_report.start()
