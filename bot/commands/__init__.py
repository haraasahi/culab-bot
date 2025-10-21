# bot/commands/__init__.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import discord
from discord import app_commands

# ここに読み込むコマンドセットを列挙
from .work import setup as setup_work
from .logs import setup as setup_logs
from .manual import setup as setup_manual
from .report import setup as setup_report
from .onboarding import setup as setup_onboarding
from .calendar_cmds import setup as setup_calendar

def setup_all(tree: app_commands.CommandTree, client: discord.Client):
    # 順番は任意。存在するものだけ呼べばOK
    setup_work(tree, client)
    setup_logs(tree, client)        # ← /log の中で週チャートを添付する実装に変更
    setup_manual(tree, client)
    setup_report(tree, client)
    setup_onboarding(tree, client)
    setup_calendar(tree, client)