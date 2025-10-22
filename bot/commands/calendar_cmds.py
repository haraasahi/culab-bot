# bot/commands/calendar_cmds.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import calendar
from zoneinfo import ZoneInfo
from typing import Optional, Dict, List, Tuple

import discord
from discord import app_commands

try:
    from ..db import get_db
except ImportError:
    from ..db import get_conn as get_db

# タイムゾーン
JST = ZoneInfo("Asia/Tokyo")

# --- 学年グループ定義 ---
# 役職ロール名 → 学年キー（DB保存用）。M1/M2は"M"に統合
ROLE_TO_GRADE = {
    "b3": "B3",
    "b4": "B4",
    "m1": "M",
    "m2": "M",
    "d": "D",
    "doctor": "D",
    "phd": "D",
    "researcher": "researcher",
}
GRADE_CHOICES = ["B3", "B4", "M", "D", "researcher", "ALL"]

# ---------- DB 初期化 ----------
def _ensure_tables():
    con = get_db()
    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            grade TEXT NOT NULL,
            title TEXT NOT NULL,
            date TEXT NOT NULL,        -- 'YYYY-MM-DD'
            start_time TEXT NOT NULL,  -- 'HH:MM'
            end_time TEXT NOT NULL,    -- 'HH:MM'
            location_type TEXT NOT NULL,   -- 'online' or 'offline'
            location_detail TEXT,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cal_guild_grade_date ON calendar_events(guild_id, grade, date);")
    # 1日前リマインド済みフラグ（存在しなければ追加）
    try:
        cur.execute("ALTER TABLE calendar_events ADD COLUMN remind1d_sent INTEGER NOT NULL DEFAULT 0;")
    except Exception:
        pass
    con.commit()


# ---------- ユーティリティ ----------
def _now_tz() -> dt.datetime:
    return dt.datetime.now(JST)

def _parse_date(date_str: str) -> dt.date:
    return dt.datetime.strptime(date_str, "%Y-%m-%d").date()

def _parse_time(hhmm: str) -> dt.time:
    return dt.datetime.strptime(hhmm, "%H:%M").time()

def _fmt_time(t: dt.time) -> str:
    return t.strftime("%H:%M")

def _fmt_date(d: dt.date) -> str:
    w = "月火水木金土日"[d.weekday() % 7]
    return f"{d:%Y-%m-%d}（{w}）"

def _grade_label(g: str) -> str:
    if g == "M":
        return "M（M1/M2）"
    if g == "ALL":
        return "ALL（全学年）"
    return g

def _add_one_month(d: dt.date) -> dt.date:
    y = d.year + (1 if d.month == 12 else 0)
    m = 1 if d.month == 12 else d.month + 1
    last_day = calendar.monthrange(y, m)[1]
    return dt.date(y, m, min(d.day, last_day))

def _user_grade(member: discord.Member) -> Optional[str]:
    names = [r.name.lower() for r in member.roles]
    for n in names:
        if n in ROLE_TO_GRADE:
            return ROLE_TO_GRADE[n]
    return None

def _can_write_grade(member: discord.Member, target_grade: str) -> bool:
    """新規登録の権限判定。ALLは管理権限のみ。"""
    if member.guild_permissions.manage_guild or member.guild_permissions.administrator:
        return True
    if target_grade == "ALL":
        return False
    my_grade = _user_grade(member)
    return my_grade == target_grade

def _can_manage_event(member: discord.Member, ev_grade: str) -> bool:
    """編集/削除の許可。'ALL' は管理権限のみ、それ以外は学年一致 or 管理権限。"""
    if member.guild_permissions.manage_guild or member.guild_permissions.administrator:
        return True
    if ev_grade == "ALL":
        return False
    return _user_grade(member) == ev_grade

def _normalize_grade_input(s: str | None, member: discord.Member) -> Optional[str]:
    """テキスト入力から学年キーを正規化。空欄→自分の学年。"""
    if not s:
        return _user_grade(member)
    raw = s.strip().lower()
    # 代表表記へ丸め
    if raw in ("b3",): return "B3"
    if raw in ("b4",): return "B4"
    if raw in ("m", "m1", "m2"): return "M"
    if raw in ("d", "doctor", "phd"): return "D"
    if raw in ("researcher", "res", "r"): return "researcher"
    if raw in ("all", "＊", "全", "全学年"): return "ALL"
    return None


# ---------- 埋め込み生成 ----------
def _embed_event_list(
    grade: str,
    date_to_rows: List[Tuple[dt.date, List[Tuple[int, dict]]]],
    title_suffix: str = "",
) -> discord.Embed:
    title = f"📅 学年カレンダー {_grade_label(grade)} {title_suffix}".strip()
    embed = discord.Embed(title=title, color=0x3BA55D, timestamp=_now_tz())
    for day, rows in date_to_rows:
        if not rows:
            continue
        lines = []
        # 行に [#ID] を含めて、ID指定の手動操作もしやすくする
        for _id, ev in rows:
            tag = "【全学年】" if ev.get("grade") == "ALL" and grade != "ALL" else ""
            lines.append(
                f"[#{_id}] {tag}**{_fmt_time(ev['start'])}–{_fmt_time(ev['end'])}** "
                f"{ev['title']} 〔{'オンライン' if ev['loc_type']=='online' else 'オフライン'}"
                f"{'・'+ev['loc_detail'] if ev['loc_detail'] else ''}〕"
            )
        embed.add_field(name=_fmt_date(day), value="\n".join(lines), inline=False)
    if not embed.fields:
        embed.description = "予定は見つかりませんでした。"
    if grade != "ALL":
        embed.set_footer(text="注: 「【全学年】」は全学年向けに登録された予定です。")
    return embed


# ---------- 管理用ビュー ----------
class _EventSelect(discord.ui.Select):
    """予定を1件選ぶセレクト（選択時は無言ACK＋選択状態を保持）"""
    def __init__(self, options: List[discord.SelectOption]):
        super().__init__(
            placeholder="編集/削除する予定を選んでください（最大25件）",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, inter: discord.Interaction):
        # 選択状態を保持するため、選んだ value を default=True に更新
        chosen = self.values[0] if self.values else None
        if chosen:
            for opt in self.options:
                opt.default = (opt.value == chosen)
        # 無言でACK（見た目は変えずに応答）
        try:
            await inter.response.edit_message(view=self.view)
        except discord.InteractionResponded:
            pass
        except Exception:
            try:
                if not inter.response.is_done():
                    await inter.response.defer()
            except Exception:
                pass


class _ManagePanel(discord.ui.View):
    """
    押したユーザーにだけ見える管理パネル（セレクト＋編集/削除＋新規登録）。
    リストが空でも「➕ 新規登録」は使える。
    """
    def __init__(self, items: List[Tuple[int, dict]], *, timeout: int = 600):
        super().__init__(timeout=timeout)
        self._has_select = False

        options: List[discord.SelectOption] = []
        for ev_id, ev in items[:25]:  # Select 限界
            label = f"{ev['date']} {ev['start'].strftime('%H:%M')}-{ev['end'].strftime('%H:%M')}"
            desc  = f"#{ev_id} [{ev['grade']}] {ev['title']}"
            options.append(discord.SelectOption(label=label[:100], value=str(ev_id), description=desc[:100]))

        if options:
            self.add_item(_EventSelect(options))
            self._has_select = True

    def _selected_id(self) -> int | None:
        for child in self.children:
            if isinstance(child, _EventSelect) and child.values:
                try:
                    return int(child.values[0])
                except Exception:
                    return None
        return None

    # --- 削除 ---
    @discord.ui.button(label="🗑️ 削除", style=discord.ButtonStyle.danger, row=1)
    async def delete_btn(self, inter: discord.Interaction, _: discord.ui.Button):
        if not self._has_select:
            return await inter.response.send_message("削除対象がありません。まずは予定を作成してください。", ephemeral=True)

        ev_id = self._selected_id()
        if ev_id is None:
            return await inter.response.send_message("削除する予定をセレクトから選んでください。", ephemeral=True)

        con = get_db()
        cur = con.cursor()
        row = cur.execute(
            "SELECT grade, title, date, start_time, end_time FROM calendar_events WHERE id=? AND guild_id=?",
            (ev_id, inter.guild_id),
        ).fetchone()
        if not row:
            return await inter.response.send_message("指定の予定が見つかりません。", ephemeral=True)

        ev_grade, title, _, _, _ = row
        if not _can_manage_event(inter.user, ev_grade):  # type: ignore
            return await inter.response.send_message("⛔ この予定を削除する権限がありません。", ephemeral=True)

        cur.execute("DELETE FROM calendar_events WHERE id=? AND guild_id=?", (ev_id, inter.guild_id))
        con.commit()
        return await inter.response.send_message(f"✅ 予定 [#{ev_id}]「{title}」を削除しました。", ephemeral=True)

    # --- 編集（モーダル） ---
    @discord.ui.button(label="✏️ 編集", style=discord.ButtonStyle.primary, row=1)
    async def edit_btn(self, inter: discord.Interaction, _: discord.ui.Button):
        if not self._has_select:
            return await inter.response.send_message("編集対象がありません。まずは予定を作成してください。", ephemeral=True)

        ev_id = self._selected_id()
        if ev_id is None:
            return await inter.response.send_message("編集する予定をセレクトから選んでください。", ephemeral=True)

        con = get_db()
        cur = con.cursor()
        row = cur.execute(
            "SELECT grade, title, date, start_time, end_time, location_type, location_detail FROM calendar_events WHERE id=? AND guild_id=?",
            (ev_id, inter.guild_id),
        ).fetchone()
        if not row:
            return await inter.response.send_message("指定の予定が見つかりません。", ephemeral=True)

        ev_grade, title, date_s, st_s, en_s, loc_type, loc_detail = row
        if not _can_manage_event(inter.user, ev_grade):  # type: ignore
            return await inter.response.send_message("⛔ この予定を編集する権限がありません。", ephemeral=True)

        class EditModal(discord.ui.Modal, title="予定を編集"):
            t_title = discord.ui.TextInput(label="タイトル", default=title[:100], max_length=256)
            t_date  = discord.ui.TextInput(label="日付 (YYYY-MM-DD)", default=date_s, max_length=10)
            t_start = discord.ui.TextInput(label="開始 (HH:MM)", default=st_s, max_length=5)
            t_end   = discord.ui.TextInput(label="終了 (HH:MM)", default=en_s, max_length=5)
            t_place = discord.ui.TextInput(
                label="場所（online/offline + 任意の詳細）",
                default=(f"{loc_type} {loc_detail}".strip() if loc_detail else loc_type),
                required=False,
                max_length=200
            )

            async def on_submit(self, m_inter: discord.Interaction):
                try:
                    new_date = _parse_date(self.t_date.value)
                    new_st = _parse_time(self.t_start.value)
                    new_en = _parse_time(self.t_end.value)
                except Exception:
                    return await m_inter.response.send_message("⚠️ 日付/時刻の形式が不正です。", ephemeral=True)

                if dt.datetime.combine(new_date, new_en) <= dt.datetime.combine(new_date, new_st):
                    return await m_inter.response.send_message("⚠️ 終了は開始より後にしてください。", ephemeral=True)

                new_title = (self.t_title.value or title).strip()
                loc_in = (self.t_place.value or "").strip()
                new_loc_type = loc_type
                new_loc_detail = loc_detail

                if loc_in:
                    parts = loc_in.split(None, 1)
                    head = parts[0].lower()
                    if head in ("online", "offline"):
                        new_loc_type = head
                        new_loc_detail = parts[1].strip() if len(parts) > 1 else None
                    else:
                        new_loc_detail = loc_in

                con2 = get_db()
                con2.execute(
                    """
                    UPDATE calendar_events
                    SET title=?, date=?, start_time=?, end_time=?, location_type=?, location_detail=?
                    WHERE id=? AND guild_id=?
                    """,
                    (
                        new_title,
                        new_date.strftime("%Y-%m-%d"),
                        new_st.strftime("%H:%M"),
                        new_en.strftime("%H:%M"),
                        new_loc_type,
                        new_loc_detail,
                        ev_id,
                        m_inter.guild_id,
                    ),
                )
                con2.commit()
                await m_inter.response.send_message(
                    f"✅ 予定 [#{ev_id}] を更新しました。`/calendar` を再実行すると反映が見られます。",
                    ephemeral=True
                )

        return await inter.response.send_modal(EditModal())

    # --- 新規登録（モーダル） ---
    @discord.ui.button(label="➕ 新規登録", style=discord.ButtonStyle.success, row=2)
    async def create_btn(self, inter: discord.Interaction, _: discord.ui.Button):
        class CreateModal(discord.ui.Modal, title="予定を新規登録"):
            g_grade = discord.ui.TextInput(
                label="学年（B3/B4/M/D/researcher/ALL）※空欄は自分の学年",
                required=False,
                max_length=20
            )
            t_title = discord.ui.TextInput(label="タイトル", max_length=256)
            t_date  = discord.ui.TextInput(label="日付 (YYYY-MM-DD)", max_length=10)
            t_start = discord.ui.TextInput(label="開始 (HH:MM)", max_length=5)
            t_end   = discord.ui.TextInput(label="終了 (HH:MM)", max_length=5)
            t_place = discord.ui.TextInput(
                label="場所（online/offline + 任意の詳細） 例: online Zoom / offline 3F-教室",
                required=False,
                max_length=200
            )

            async def on_submit(self, m_inter: discord.Interaction):
                # 学年の正規化＋権限確認（.value を使う）
                target_grade = _normalize_grade_input(self.g_grade.value, m_inter.user)  # type: ignore
                if target_grade is None:
                    return await m_inter.response.send_message(
                        "⚠️ 学年は B3/B4/M/D/researcher/ALL から指定してください（空欄可）。",
                        ephemeral=True
                    )
                if not _can_write_grade(m_inter.user, target_grade):  # type: ignore
                    if target_grade == "ALL":
                        return await m_inter.response.send_message("⛔ 全学年（ALL）の登録は管理者のみ可能です。", ephemeral=True)
                    return await m_inter.response.send_message(
                        f"⛔ { _grade_label(target_grade) } の予定を登録する権限がありません。",
                        ephemeral=True
                    )

                # 入力チェック（.value を使う）
                try:
                    d = _parse_date(self.t_date.value)
                    t_start = _parse_time(self.t_start.value)
                    t_end   = _parse_time(self.t_end.value)
                except Exception:
                    return await m_inter.response.send_message("⚠️ 日付/時刻の形式が不正です。", ephemeral=True)
                if dt.datetime.combine(d, t_end) <= dt.datetime.combine(d, t_start):
                    return await m_inter.response.send_message("⚠️ 終了時刻は開始より後にしてください。", ephemeral=True)

                title = (self.t_title.value or "").strip()
                if not title:
                    return await m_inter.response.send_message("⚠️ タイトルを入力してください。", ephemeral=True)

                loc_in = (self.t_place.value or "").strip()
                loc_type = "offline"
                loc_detail = None
                if loc_in:
                    parts = loc_in.split(None, 1)
                    head = parts[0].lower()
                    if head in ("online", "offline"):
                        loc_type = head
                        loc_detail = parts[1].strip() if len(parts) > 1 else None
                    else:
                        loc_detail = loc_in

                con2 = get_db()
                cur2 = con2.cursor()
                cur2.execute(
                    """
                    INSERT INTO calendar_events
                        (guild_id, grade, title, date, start_time, end_time, location_type, location_detail, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        m_inter.guild_id,
                        target_grade,
                        title,
                        d.strftime("%Y-%m-%d"),
                        t_start.strftime("%H:%M"),
                        t_end.strftime("%H:%M"),
                        loc_type,
                        loc_detail,
                        m_inter.user.id,  # type: ignore
                        _now_tz().isoformat(timespec="seconds"),
                    ),
                )
                con2.commit()
                ev_id = cur2.lastrowid

                embed = discord.Embed(
                    title=f"📝 予定を登録しました（{_grade_label(target_grade)}）",
                    color=0x57F287,
                )
                embed.add_field(name="日付", value=_fmt_date(d), inline=True)
                embed.add_field(name="時刻", value=f"{_fmt_time(t_start)}–{_fmt_time(t_end)}", inline=True)
                embed.add_field(name="タイトル", value=title[:256], inline=False)
                place_txt = ("オンライン" if loc_type == "online" else "オフライン") + (f"｜{loc_detail}" if loc_detail else "")
                embed.add_field(name="場所", value=place_txt, inline=False)
                embed.set_footer(text=f"ID: {ev_id}")
                await m_inter.response.send_message(embed=embed, ephemeral=True)

        # モーダル表示（ACK）
        try:
            return await inter.response.send_modal(CreateModal())
        except discord.InteractionResponded:
            pass
        except Exception:
            if not inter.response.is_done():
                await inter.response.defer(ephemeral=True)


# 「管理パネルを開く」ボタン付きビュー
class _OpenManageButton(discord.ui.View):
    """/calendar のメッセージに付ける「管理パネルを開く」ボタン。押した人にだけ管理UIを出す。"""
    def __init__(self, base: dt.date, end_date: dt.date, target_grade_for_view: str):
        super().__init__(timeout=600)
        self.base = base
        self.end = end_date
        self.view_grade = target_grade_for_view  # 画面に表示している学年（ALLなら全学年のみ）

    @discord.ui.button(label="🛠️ 管理パネルを開く", style=discord.ButtonStyle.secondary)
    async def open_panel(self, inter: discord.Interaction, button: discord.ui.Button):
        # 押したユーザーの権限で、期間内＆表示学年の予定から“管理可能なもの”だけ抽出
        con = get_db()
        cur = con.cursor()
        if self.view_grade == "ALL":
            cur.execute(
                """
                SELECT id, grade, title, date, start_time, end_time, location_type, location_detail
                FROM calendar_events
                WHERE guild_id = ? AND grade = 'ALL' AND date >= ? AND date < ?
                ORDER BY date ASC, start_time ASC
                """,
                (inter.guild_id, self.base.strftime("%Y-%m-%d"), self.end.strftime("%Y-%m-%d")),
            )
        else:
            cur.execute(
                """
                SELECT id, grade, title, date, start_time, end_time, location_type, location_detail
                FROM calendar_events
                WHERE guild_id = ? AND grade IN (?, 'ALL') AND date >= ? AND date < ?
                ORDER BY date ASC, start_time ASC
                """,
                (inter.guild_id, self.view_grade, self.base.strftime("%Y-%m-%d"), self.end.strftime("%Y-%m-%d")),
            )
        rows = cur.fetchall()

        manageable: List[Tuple[int, dict]] = []
        for (ev_id, g, title, s_date, s_start, s_end, loc_type, loc_detail) in rows:
            if not _can_manage_event(inter.user, g):  # type: ignore
                continue
            try:
                _ = _parse_date(s_date); st = _parse_time(s_start); en = _parse_time(s_end)
            except Exception:
                continue
            manageable.append(
                (ev_id, {
                    "grade": g, "title": title, "date": s_date,
                    "start": st, "end": en, "loc_type": loc_type, "loc_detail": loc_detail
                })
            )

        panel = _ManagePanel(manageable)
        msg = "管理したい予定を選ぶか『➕ 新規登録』を押してください。"
        if not manageable:
            msg = "管理できる予定はありません。『➕ 新規登録』から作成できます。"
        await inter.response.send_message(msg, view=panel, ephemeral=True)


# ---------- コマンド ----------
def setup(tree: app_commands.CommandTree, client: discord.Client):
    _ensure_tables()

    # /calendar_registration（既存）
    @tree.command(
        name="calendar_registration",
        description="学年カレンダーに予定を登録（MはM1/M2統合・ALLで全学年向け）。"
    )
    @app_commands.describe(
        date="日付（YYYY-MM-DD）",
        start="開始時刻（HH:MM）",
        end="終了時刻（HH:MM）",
        title="予定のタイトル",
        place="場所の種別（オンライン / オフライン）",
        detail="場所の補足（Zoom/教室名など任意）",
        grade="対象学年（未指定なら自分の学年）※ALL=全学年向け",
    )
    @app_commands.choices(
        place=[
            app_commands.Choice(name="オンライン", value="online"),
            app_commands.Choice(name="オフライン", value="offline"),
        ],
        grade=[app_commands.Choice(name=_grade_label(g), value=g) for g in GRADE_CHOICES],
    )
    async def calendar_registration(
        inter: discord.Interaction,
        date: str,
        start: str,
        end: str,
        title: str,
        place: app_commands.Choice[str],
        detail: Optional[str] = None,
        grade: Optional[app_commands.Choice[str]] = None,
    ):
        await inter.response.defer(ephemeral=False)

        target_grade = grade.value if grade else _user_grade(inter.user)  # type: ignore
        if target_grade is None:
            return await inter.followup.send(
                "⚠️ あなたの学年ロールが見つかりませんでした。B3/B4/M1/M2/D/Researcher のいずれかのロールを付与してください。",
                ephemeral=True,
            )
        if not _can_write_grade(inter.user, target_grade):  # type: ignore
            if target_grade == "ALL":
                return await inter.followup.send("⛔ 全学年向け（ALL）の登録は管理者のみ可能です。", ephemeral=True)
            return await inter.followup.send(
                f"⛔ この学年（{_grade_label(target_grade)}）のカレンダーに登録する権限がありません。",
                ephemeral=True,
            )

        try:
            d = _parse_date(date)
            t_start = _parse_time(start)
            t_end = _parse_time(end)
        except ValueError:
            return await inter.followup.send("⚠️ 日付/時刻の形式が不正です。`YYYY-MM-DD` / `HH:MM` で指定してください。", ephemeral=True)
        if dt.datetime.combine(d, t_end) <= dt.datetime.combine(d, t_start):
            return await inter.followup.send("⚠️ 終了時刻は開始時刻より後にしてください。", ephemeral=True)

        loc_type = place.value
        loc_detail = (detail or "").strip() or None

        con = get_db()
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO calendar_events
                (guild_id, grade, title, date, start_time, end_time, location_type, location_detail, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                inter.guild_id,
                target_grade,
                title.strip(),
                d.strftime("%Y-%m-%d"),
                t_start.strftime("%H:%M"),
                t_end.strftime("%H:%M"),
                loc_type,
                loc_detail,
                inter.user.id,  # type: ignore
                _now_tz().isoformat(timespec="seconds"),
            ),
        )
        con.commit()
        ev_id = cur.lastrowid

        embed = discord.Embed(
            title=f"📝 予定を登録しました（{_grade_label(target_grade)}）",
            color=0x5865F2,
        )
        embed.add_field(name="日付", value=_fmt_date(d), inline=True)
        embed.add_field(name="時刻", value=f"{_fmt_time(t_start)}–{_fmt_time(t_end)}", inline=True)
        embed.add_field(name="タイトル", value=title[:256], inline=False)
        embed.add_field(
            name="場所",
            value=("オンライン" if loc_type == "online" else "オフライン") + (f"｜{loc_detail}" if loc_detail else ""),
            inline=False,
        )
        embed.set_footer(text=f"ID: {ev_id}")
        await inter.followup.send(embed=embed)

    # /calendar（管理ボタン付き）
    @tree.command(
        name="calendar",
        description="自分の学年（または指定学年）のカレンダーを表示（同時に全学年向けも含む）。"
    )
    @app_commands.describe(
        days="表示する日数（未指定なら『1ヶ月後まで』）",
        from_date="起点日（YYYY-MM-DD。未指定なら今日）",
        grade="対象学年（指定しない場合は自分の学年。ALL=全学年の予定のみ）",
    )
    @app_commands.choices(
        grade=[app_commands.Choice(name=_grade_label(g), value=g) for g in GRADE_CHOICES],
    )
    async def calendar(
        inter: discord.Interaction,
        days: Optional[int] = None,
        from_date: Optional[str] = None,
        grade: Optional[app_commands.Choice[str]] = None,
    ):
        # 対象学年
        target_grade = grade.value if grade else _user_grade(inter.user)  # type: ignore
        if target_grade is None:
            return await inter.response.send_message(
                "⚠️ あなたの学年ロールが見つかりませんでした。B3/B4/M1/M2/D/Researcher のいずれかのロールを付与してください。",
                ephemeral=True,
            )

        # 期間
        try:
            base = _parse_date(from_date) if from_date else _now_tz().date()
        except ValueError:
            return await inter.response.send_message("⚠️ from_date は `YYYY-MM-DD` で指定してください。", ephemeral=True)

        if days is None:
            end_date = _add_one_month(base)
        else:
            try:
                days = int(days)
            except Exception:
                return await inter.response.send_message("⚠️ days は整数で指定してください。", ephemeral=True)
            if days <= 0 or days > 62:
                return await inter.response.send_message("⚠️ days は 1〜62 の範囲で指定してください。", ephemeral=True)
            end_date = base + dt.timedelta(days=days)

        # 取得
        con = get_db()
        cur = con.cursor()
        if target_grade == "ALL":
            cur.execute(
                """
                SELECT id, grade, title, date, start_time, end_time, location_type, location_detail
                FROM calendar_events
                WHERE guild_id = ? AND grade = 'ALL' AND date >= ? AND date < ?
                ORDER BY date ASC, start_time ASC
                """,
                (inter.guild_id, base.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
            )
        else:
            cur.execute(
                """
                SELECT id, grade, title, date, start_time, end_time, location_type, location_detail
                FROM calendar_events
                WHERE guild_id = ? AND grade IN (?, 'ALL') AND date >= ? AND date < ?
                ORDER BY date ASC, start_time ASC
                """,
                (inter.guild_id, target_grade, base.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
            )
        rows = cur.fetchall()

        # 整形
        by_day: Dict[dt.date, List[Tuple[int, dict]]] = {}
        for (ev_id, g, title, s_date, s_start, s_end, loc_type, loc_detail) in rows:
            d = _parse_date(s_date)
            st = _parse_time(s_start)
            en = _parse_time(s_end)
            by_day.setdefault(d, []).append(
                (ev_id, {
                    "grade": g,
                    "title": title,
                    "start": st,
                    "end": en,
                    "loc_type": loc_type,
                    "loc_detail": loc_detail
                })
            )

        ordered = sorted(by_day.items(), key=lambda kv: kv[0])
        disp_end = end_date - dt.timedelta(days=1)
        if grade and grade.value == "ALL":
            scope = f"（{_fmt_date(base)} 〜 {_fmt_date(disp_end)}｜全学年のみ）"
        else:
            scope = f"（{_fmt_date(base)} 〜 {_fmt_date(disp_end)}｜全学年含む）"

        embed = _embed_event_list(target_grade if target_grade else "ALL", ordered, title_suffix=scope)

        # 管理パネルボタン（押した人にだけephemeral管理UI）
        view = _OpenManageButton(base, end_date, target_grade if target_grade else "ALL")
        await inter.response.send_message(embed=embed, view=view)