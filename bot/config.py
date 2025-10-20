# bot/config.py
from dotenv import load_dotenv
import os
from zoneinfo import ZoneInfo

load_dotenv()

JST = ZoneInfo("Asia/Tokyo")

TOKEN = os.getenv("DISCORD_TOKEN")
DEV_GUILD_ID = os.getenv("DEV_GUILD_ID")  # 任意（ギルド同期を速く）
DB_PATH = os.getenv("DB_PATH", "worklog.db")

# サーバー内の“週次レポ投稿チャンネル”対応表（ユーザーID→チャンネル名）
USER_REPORT_CHANNELS = {
    "41064": "あさひ",
    "723": "なつみ",
}

WORK_TYPES = ["研究", "勉強", "資料作成", "その他"]
