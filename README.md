# 「culabの秘書」

culab のdiscordサーバー用bot **「culabの秘書」** のgithubです。

***
## 日本語説明

✅ **基本コマンド**<br>
**・**  ` /start_work` *タイプ選択必須（研究 / 勉強 / 資料作成 / その他）*
> 実行すると「開始」メッセージと一緒にクイック操作ボタンが出ます（発行者のみ操作可）。<br>
> ボタン：休憩開始 / 休憩終了 / 作業終了

**・** `/log` *period を選択*
> ・ `今日` … タイプ別の合計時間＋今日の進捗メモを表示<br>
> ・ `今週` … タイプ別の合計時間＋一週間分の進捗メモ（「何月何日」ごと）を表示<br>
> 
> 今週のタイムライン画像を生成（タイプ別に色分け、休憩はグレー）。<br>
> 例：月〜日の各行に、1日の時間帯ごとの作業・休憩を帯で表示します。

**・** `/report <内容> [date:YYYY-MM-DD] `
> その日の進捗メモを追記保存（同日に複数回OK。上書きではなく追加）

**・** `/log_manual <date> <start> <end> <type> `
> 例：`/log_manual 2025-10-20 09:00 11:15 勉強`<br>
> 休憩なしの手動ログを追加（形式チェック・重複時間の警告あり）

**・** `/calender` (*days, from_date, grade* は自由選択)
> 予定されたカレンダーが見れます<br>
> *days* は表示する日数です。未指定の場合は一ヶ月後までの予定が表示されます<br>
> *from_date* は表示の起点日です。未指定の場合は今日以降の予定が表示されます。<br>
> *garde* は対象学年です。ALL は全学年共通の予定です。未指定の場合は自分の学年の予定が表示されます。<br>
>
> 管理パネルから予定の削除、編集、新規登録を行えます。
<br>

✍️ **進捗メモの残し方**<br>
**・** 作業終了メッセージの案内に続けて、そのまま文章を送ると今日の進捗として自動保存されます。<br>
　（「作業終了」後 約10分間、同チャンネルの次メッセージを進捗として受け付けます）<br>
**・** もしくは `/report` でもいつでも保存OK。<br>
**・** 同日に複数回書いた場合は、すべて連結して保存します。<br>
<br>

🔔 **自動機能**<br>
・**超過休憩アラート**：休憩開始から2時間経過で本人にのみDMで通知。<br>
・**予定リマインド**：学年毎に予定の１日前に`連絡` チャンネルで予定をリマインドします。<br>
<br>

⚠️**不具合やほしい機能あればあさひまでお知らせください**
<br>
<br>
<br>

***
## English Explanation


✅ **Basic Commands**
**・**  ` /start_work` *Choose a type (研究 / 勉強 / 資料作成 / その他) — required*
> When you run it, a “Started” message appears with quick-action buttons (only the issuer can use them).<br>
> Buttons: Start Break / End Break / End Work<br>

**・** `/log` *choose a period*
> ・  `Today` … Shows total time by type + today’s progress note<br>
> ・　 `This Week` … Shows total time by type + one week of progress notes (grouped by date)<br>
> 
> Generates a weekly timeline image (color-coded by type; breaks in gray).<br>
> Example: rows for Mon–Sun with bands showing work/break periods across the day.<br>

**・** `/report <text> [date:YYYY-MM-DD] `
> Appends a progress note for that day (multiple notes per day are OK; they’re appended, not overwritten)

**・** `/log_manual <date> <start> <end> <type> `
> Example: `/log_manual 2025-10-20 09:00 11:15 勉強`<br>
> Adds a manual block without breaks (with format validation and overlap warnings)<br>

**・** `/calender` (*days, from_date, grade* optional)
> View scheduled calendar items  <br>
> *days* is the number of days to display. If omitted, it shows up to one month ahead  <br>
> *from_date* is the start date of the view. If omitted, events from today onward are shown  <br>
> *grade* is the target grade. *ALL* means events common to all grades. If no grade is specified, your own grade's schedule will be displayed.<br>
>
> You can delete, edit, or add new schedules from the admin panel<br>
<br>

✍️ **How to Leave Progress Notes**<br>
**・** Right after the “End Work” message, just send your text; it is automatically saved as today’s progress.  <br>
　　(For about 10 minutes after “End Work,” your next message in the same channel is treated as progress.)<br>
**・** You can also save at any time with `/report`.  <br>
**・** If you write multiple notes on the same day, they are concatenated (not overwritten).<br>
<br>

🔔 **Automation**<br>
・**Break Overrun Alert**: If a break lasts 2 hours, a DM reminder is sent to the user only.  <br>
・**Event Reminder**: One day before an event, a reminder is posted in the `連絡` channel for each relevant grade.<br>
<br>

⚠️ **If you find bugs or want features, please ping あさひ.**
