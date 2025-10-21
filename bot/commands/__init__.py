# bot/commands/__init__.py
from .work import setup as setup_work
from .manual import setup as setup_manual
from .logs import setup as setup_logs
from .charts_cmd import setup as setup_charts
from .report import setup as setup_report
from .calendar_cmds import setup as setup_calendar
from .onboarding import setup as setup_onboarding


def setup_all(tree, client):
    setup_work(tree, client)
    setup_manual(tree, client)
    setup_logs(tree, client)
    setup_charts(tree, client)
    setup_report(tree, client)
    setup_calendar(tree, client)
    setup_onboarding(tree, client)