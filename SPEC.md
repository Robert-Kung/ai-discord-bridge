# ai-discord-bridge — 規格文件 (SPEC)

> 版本：v3 · 最後更新：2026-06-02
> 狀態：MVP 運作中，核心功能已在 Discord 實測通過
> v3 變更（plan-eng-review 已通過，B review 回饋已回灌 §7）：session/summary 改 per-(bot, cwd)、新增 per-project notes 層、flush-before-compaction

---

## 1. 這是什麼

把 Discord 變成「使用者 + 兩個對等 Claude 帳號 (A / B)」的三方協作聊天室。
後端 `claude -p --resume`，**雙認證模式**（見 §9）：預設走各帳號的**訂閱額度**
（Agent SDK credits，非原始 API 計費）；亦可 `USE_API_KEY=true` 改走 **Developer
Platform API key** 計費（ToS 更乾淨；優先序尚待實證）。

兩個帳號是 dual-account 設定的延伸：A 在 `~/.claude/`、B 在 `~/.claude-b/`，
共用 `~/.claude-shared/`（CLAUDE.md / skills / memory）。

### 設計目標
- 在手機 / 任何 Discord client 跟兩個 AI 協作、博弈思想、輸出方案
- 兩帳號額度分流（A=slot 1、B=slot 2），不互相吃 quota
- 對話、記憶、授權都可控

---

## 2. 系統分層（本專案在第 3 層）

```
┌─ 第 1 層：雙帳號隔離 ────────────────────────────────┐
│  ~/.claude (A) · ~/.claude-b (B) · ~/.claude-shared (共用)  │
│  cswap 監控 5h/7d 用量；symlink 共用 CLAUDE.md/skills/memory │
├─ 第 2 層：CLI 互通（sibling）───────────────────────┤
│  sibling "msg"        A↔B 同步派工                          │
│  sibling bg "msg"     非同步派工 + job 管理                  │
│  方向自動偵測（看 CLAUDE_CONFIG_DIR）                        │
├─ 第 3 層：Discord 聊天室（本專案）──────────────────┤
│  Docker 容器跑兩個 discord.Client，被 @ 觸發 claude -p       │
└──────────────────────────────────────────────────────┘
```

---

## 3. 架構與資料流

```
   [你的手機 / 桌面 Discord client]
              │  在 #ai-chat 打字 / @-mention
              ▼
   [Discord Gateway (WebSocket)]
              │
              ▼
┌──────────────── Docker: ai-discord-bridge ────────────────┐
│  bot.py (單一 Python 進程)                                  │
│    ├─ discord.Client "A"  ← DISCORD_BOT_A_TOKEN            │
│    └─ discord.Client "B"  ← DISCORD_BOT_B_TOKEN            │
│         on_message → 路由 → call_claude()                  │
│              │ asyncio.create_subprocess_exec              │
│              ▼                                             │
│    claude -p --output-format json                          │
│      --permission-mode <mode>                              │
│      [--resume <sid>] [--append-system-prompt-file latest] │
│      (CLAUDE_CONFIG_DIR=/home/user/.claude{,-b}, cwd=~)    │
└──────────────────────────┬─────────────────────────────────┘
                           │ bind mount（同路徑）
                           ▼
   ~/.claude     ~/.claude-b     ~/.claude-shared
   (A 憑證+設定)  (B 憑證+設定)    (共用；memory 子目錄 ro)
```

### 一則訊息的生命週期
1. 兩個 client 同時收到 `on_message`
2. **Bot-A 額外職責**：寫入 channel buffer、累計訊息數、達門檻觸發背景 flush、解析 `!` 指令
3. 判斷是否被 mention（user mention 或 bot 的 role mention 都算）
4. 過 turn budget 閘門（`turn_lock` 保護，A/B 共用計數，防互答失控）
5. 決定 permission mode：`!once` override > channel 預設
6. `bypass` → plan-then-execute 雙階段；其餘 → 直接 call
7. 回覆切塊（每塊 <1900 字）送回 Discord

---

## 4. 檔案結構

### 4.1 Repo（`~/ai-discord-bridge/`）
```
ai-discord-bridge/
├── bot.py                       主程式（~1130 行）
├── Dockerfile                   python:3.12-slim + Node20 + claude-code CLI + discord.py 2.4.0
├── docker-compose.yml           真實 compose（bind mount + 私人專案路徑；gitignore）
├── docker-compose.example.yml   公開樣板（佔位路徑 + 隔離說明）
├── .env                         機密設定（gitignore，不進版控）
├── .env.example                 設定樣板
├── .gitignore                   排除 .env / docker-compose.yml / RELEASE_PLAN.md / __pycache__
├── README.md                    公開入口：Discord 設定步驟 + 啟動 + 用法（英文）
├── SPEC.md                      ← 本文件（設計規格，中文）
├── SECURITY.md / SECURITY.zh.md 威脅模型（英 / 中）
├── LICENSE                      MIT
├── RELEASE_PLAN.md              內部公開化計畫（gitignore，不 ship）
└── scripts/
    ├── archive-old-jsonl.sh     cron 週清 >30 天 session jsonl
    └── refresh-cswap-usage.py   host cron：寫兩帳號 5h/7d 用量 JSON 給 !state 讀
```

### 4.2 執行期資料（在 `~/.claude-shared/` 底下，A/B/host 共用）
```
~/.claude-shared/
├── discord-state/                       Discord bridge 狀態
│   ├── <bot>__<cwd-slug>.json           per-(bot, cwd) session id  {"session_id","cwd"}
│   │                                      例：A__home-user.json、B__home-user-myproject.json
│   └── channel_<channel_id>.json        per-channel 設定 {"mode": "plan", "cwd": "..."}
│
├── discord-summaries/<channel_id>/<cwd-slug>/   記憶中期層（v3：加 cwd 一段）
│   ├── latest.md                        該 (channel, cwd) 最新摘要（呼叫時 prepend）
│   └── <YYYYMMDD-HHMMSS>.md             歷史摘要快照
│
├── discord-project-notes/<cwd-slug>/    記憶專案層（v3 新增；**可寫**，不在 ro 的 memory/ 下）
│   ├── notes.md                         per-cwd 專案筆記（架構決策/進行中/關鍵路徑/Open Q）
│   └── <YYYYMMDD-HHMMSS>.md             merge-on-write 前的舊版快照（留最近 3 份）
│
├── memory/                              記憶長期層（容器內 read-only 掛載）
│   ├── MEMORY.md                        索引
│   ├── user_profile.md / infrastructure.md   共用
│   └── agent_a.md / agent_b.md          各自 profile
│
├── sibling-session-a-to-b.id            sibling CLI 的 session（與 Discord 獨立）
├── sibling-session-b-to-a.id
├── sibling-chat.md                      sibling 對話 log
└── jobs/                                sibling bg 任務

~/.claude/projects/<-home-user-cwd-slug>/<sid>.jsonl     A 的 session 原始紀錄（Claude 內建按 cwd 分目錄）
~/.claude-b/projects/<-home-user-cwd-slug>/<sid>.jsonl   B 的 session 原始紀錄
~/.claude-archive/                                       >30 天 jsonl 歸檔（cron）
```

---

## 5. 指令參考

> 指令只有 **Bot-A 處理**（避免雙 bot 重複執行）；需在白名單內的使用者才能下指令。

| 指令 | 行為 | session 機制 | 權限 |
|------|------|-------------|------|
| `@A` / `@B` | 對應 bot 回應；被 @ 才回。**呼叫前注入近 15 則頻道脈絡**（看得到你問什麼、對方說什麼）。bot 可在回覆中 `@對方` 徵詢第二意見 → 觸發 mention 鏈接話（受 `MAX_BOT_TURNS` 限制）| resume 主線 + 脈絡注入 | 白名單 |
| `@A @B` | 兩 bot 並行各自回（雙視角）| 各自 resume 主線 + 脈絡注入 | 白名單 |
| `!discuss <主題>` | A↔B 輪流辯論至 `MAX_BOT_TURNS`。**共享滾動 transcript**（每輪看完整辯論+你的原問題）；**獨立 turn budget**（不佔用一般 @ 的額度）；結束後自動寫結論 summary | 每次全新 session（不污染主線）| 白名單 |
| `!flush` | Bot-B 提煉當前 channel 對話 → 寫 summary | 全新 session | 白名單 |
| `!reset A\|B` | 清掉該 bot 主 session id（summary 保留）| 清除 | 白名單 |
| `!cd <專案名\|路徑>` | 切 channel 工作目錄到白名單 git 專案；不帶參數顯示當前 cwd + 可用專案 | per-channel state | 白名單 |
| `!mode plan\|edit\|bypass` | 設 channel 預設權限模式 | — | bypass 需白名單 |
| `!once <mode>` | 單一訊息用此模式（訊息末尾加）| — | bypass 需白名單 |
| `!yolo` | bypass 跳過 plan-then-execute（單訊息）| — | 白名單 |
| `!state` | 顯示 channel 模式 / buffer / summary 狀態 | — | 白名單 |
| `!help` | 指令參考 | — | 白名單 |

### 自動行為
- **自動 flush**：channel 累計 `AUTO_FLUSH_THRESHOLD`（預設 20）則訊息 → 背景跑 flush
- **summary prepend**：每次 call 用 `--append-system-prompt-file latest.md` 注入最新摘要
- **啟動公告**：容器重啟後 Bot-A 自動在 #ai-chat 貼功能清單

---

## 6. 權限模式（授權執行）

> 限制：`claude -p` 是一次性 subprocess，**授權必須在呼叫前決定**，不能跑到一半彈窗。

| 模式 | CLI 旗標 | 能做什麼 |
|------|---------|---------|
| `plan` | `--permission-mode plan` | 只讀規劃，不能改任何東西（最安全，預設）|
| `edit` | `acceptEdits` | 自動接受檔案編輯；bash 等仍需 bypass |
| `bypass` | `bypassPermissions` | 全自動接受所有工具（高風險）|

### Plan-then-execute（bypass 的防呆層）
```
你 @A（channel 為 bypass，或 !once bypass）
  → Bot 先用 plan 模式跑一次，貼出「我打算做 X/Y/Z」+ ✅/❌ reaction
  → 你按 ✅ → Bot 用 bypass 真執行
  → 按 ❌ 或 5 分鐘（PLAN_REACTION_TIMEOUT）無反應 → 取消
  → 加 !yolo 可跳過 plan 階段直接執行
```
reaction 等待用 `pending_actions` dict + Future，**不持有 bot_lock**，所以等待期間 bot 仍可服務其他訊息。

### 預設策略
- `#ai-chat`（聊天）→ `plan`
- bypass 從不預設，只能明確 `!mode bypass` 或 `!once bypass`，且限白名單

---

## 7. 記憶管理（四層模型）

啟發自 OpenClaw 的 memory flush/dreaming、Claude Code 的 compaction 與 `MEMORY.md`、Cline Memory Bank 的 hot-file。
（研究來源見 §12；三方收斂的 pattern：index 常駐+detail 按需、flush-before-compaction、靜態 context 從 disk 重注入、按 cwd 切記憶。）

| 層 | 位置 | key | 機制 | 生命週期 |
|----|------|-----|------|---------|
| **短期** | `<sid>.jsonl` | (bot, cwd) | `--resume` 重載整段；Claude 內建 auto-compact（~95%）| 持續累積到 reset |
| **中期** | `discord-summaries/<ch>/<cwd-slug>/latest.md` | (channel, cwd) | flush 提煉決策/任務/檔案/角色；呼叫時 prepend | 手動/自動 flush 更新 |
| **專案** | `discord-project-notes/<cwd-slug>/notes.md` | cwd | 慢變化架構文件；merge-on-write；呼叫時 prepend | 隨專案演進 |
| **長期** | `~/.claude-shared/memory/` | 全域 | 共用 profile，A/B/host 都讀得到 | 跨 session 永久 |

### 為什麼這樣切 key（v3 設計核心）
- **短期/中期/專案 都按 cwd 切**：session 本來就是 Claude 按 cwd 分目錄存的，summary 與 project notes 若不跟著切，`!cd my-project` 後會 prepend 到一份**混了前一專案**的脈絡 → 誤導。
- **中期 = (channel, cwd)，專案 = 只 cwd**：summary 是某對話線的濃縮（channel 私有）；project notes 是專案級知識（多個 channel 在同一專案應**共享**那一份，不分裂）。
- **專案層為何不放 `memory/` 下**：`memory/` 在容器內是 **read-only 掛載**（限制 #4，防 A/B/host 併發寫競態）。bot 要能寫，故 project notes 放在 rw 的 `.claude-shared/discord-project-notes/`，不被 ro 的 `memory/` 遮蔽，**不需改 docker-compose**。A/B 在 host 仍可直接讀。

### flush-before-compaction（吸收 OpenClaw 殺手鐧）
中期/專案層的更新**不只**靠 `!flush` 與訊息門檻被動觸發，還在以下時機主動落盤：
- `do_flush()` 跑完 → 同一份 transcript 順手更新對應 cwd 的 project notes（一個 flush 週期只跑一次）。
- `!cd` **切離**當前專案前 → 先對舊 cwd 跑一次 project-notes 更新（「離開前把正在做的事記下來」）。
- 不在每次 bot call 後寫 project notes —— 它是慢變化文件，非逐則對話。

### flush 一次呼叫雙段輸出（B plan-eng-review d 點）
summary 與 project notes **不分兩次 `claude -p`**（否則 Bot-B quota 翻倍）。`do_flush()` 用單次呼叫產出兩段，以分隔線切分後各自寫檔：
```
=== CHANNEL_SUMMARY ===   （決策/任務/檔案/角色，500 字內 → 中期層）
=== PROJECT_NOTES ===     （merge 現有 notes + 本次對話，400 字內 → 專案層）
```
- notes 更新的 `call_claude("B", ...)` 用 **`cwd=DEFAULT_CWD`**（不持 `cwd_locks[專案]`），否則會卡在 Bot-B 該專案長任務的鎖後面（B review b 點）。純文字任務不需在專案目錄跑，寫檔路徑不受影響。
- `cmd_cd` 觸發背景更新時，**先** `format_buffer_transcript()` 與讀 `old_cwd`，**再** `save_channel_state()` 改 cwd，最後 `create_task(...)` 帶 snapshot 進去（B review a 點，固定順序避免脈絡錯亂）。
- DEFAULT_CWD（`/home/user`，非專案）不寫 project notes。

### 注入合併（單檔限制的處理）
`--append-system-prompt-file` 只吃**一個**檔案。呼叫前把「中期 summary + 專案 notes」合併成一個 temp 檔再傳：
- temp 檔放 `/tmp/_sysprompt_<ch>_<bot>.md`，帶 **bot 名**避免 A/B 同 channel 互相覆蓋；放 `/tmp` 保持 STATE_DIR 乾淨、容器重啟自清（B review c 點）。
- 兩段以 `---` 分隔；任一缺則只放有的；都缺則不加旗標。

### GC
merge-on-write（每次重寫前先讀舊檔合併，不 raw append）+ 寫前把舊版 rename 成 timestamped、留最近 3 份。
- notes 防漂移：`len(existing) > 3000` 時在 prompt 加強制壓縮提示，要求積極刪過時資訊（B review e 點）。字元數估算即可，不需 token 計算。
- 3 份 snapshot 是人工回溯用，非 GC 機制。

### `--resume` 的本質（重要觀念）
- 每次 `claude -p` 進程都是**一次性**的（跑完即死）
- 但啟動時會把整個 `<sid>.jsonl` **重新讀進來當 context**，所以「假裝」連續
- 比喻：每次失憶但帶日記的人 —— 進程是人（一次性），jsonl 是日記（持久）
- 代價：jsonl 越大，每次重讀 token 越多 → summary 層就是為了濃縮舊日記

---

## 8. A↔B 協作的兩條通道

| 通道 | 入口 | 用途 |
|------|------|------|
| **sibling CLI** | terminal / 各自 session 內 | A、B 程式化互相派工（同步/非同步 job）|
| **Discord** | #ai-chat | 使用者主導的三方對話、辯論、派任務 |

兩條通道的 session **完全獨立**（不同 session id 檔），不互相干擾。

---

## 9. 設定（環境變數）

| 變數 | 必填 | 預設 | 說明 |
|------|------|------|------|
| `DISCORD_BOT_A_TOKEN` | ✅ | — | Bot-A 權杖 |
| `DISCORD_BOT_B_TOKEN` | ✅ | — | Bot-B 權杖 |
| `DISCORD_CHANNEL_ID` | ✅ | — | 監聽的頻道 ID（純數字，非連結）|
| `ALLOWED_USER_IDS` | ✅ | — | 可下指令/驅動的 user id（逗號分隔）；**留空 = fail-closed 拒絕啟動** |
| `USE_API_KEY` | | false | 認證模式開關：false=訂閱 OAuth；true=用下列 API key |
| `ANTHROPIC_API_KEY_A` / `_B` | △ | — | per-bot API key；`USE_API_KEY=true` 時必填，否則拒絕啟動 |
| `MAX_BOT_TURNS` | | 6 | 互答/discuss 最大輪數（人類發言重置）|
| `CLAUDE_TIMEOUT` | | 300 | 單次 claude -p 逾時（秒）|
| `AUTO_FLUSH_THRESHOLD` | | 20 | 自動 flush 訊息門檻 |
| `FLUSH_TOKEN_THRESHOLD` | | 400000 | token 門檻：寫 summary 存檔、保留對話線（0=關）|
| `RESET_TOKEN_THRESHOLD` | | 700000 | token 門檻：濃縮 session + 重置（0=關）|
| `HARD_RESET_TOKEN_THRESHOLD` | | 900000 | 濃縮失敗也強制重置的硬上限 |
| `PLAN_REACTION_TIMEOUT` | | 300 | plan-then-execute 等 react 秒數 |

### 認證模式（雙模式）
- **訂閱模式（預設）**：`USE_API_KEY` 未設/false。各 bot 用其 `CLAUDE_CONFIG_DIR`（`~/.claude{,-b}`）內的 OAuth 憑證；subprocess env **無條件 strip 掉整個 auth/計費路由家族**（`ANTHROPIC_API_KEY`、`_A`、`_B`、`ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_BASE_URL`、`CLAUDE_CODE_USE_BEDROCK/VERTEX`），所以任何 host stray 值都無法靜默改路由或計費（B review 的 deny-list 擴充）。
- **API key 模式**：`USE_API_KEY=true` + per-bot `ANTHROPIC_API_KEY_{A,B}`。`call_claude` 在上述 strip 之後**只**把該 bot 的 key 以 canonical `ANTHROPIC_API_KEY` re-inject → 改走 Developer Platform 計費（另一隻 bot 的 key 不會出現在這隻的 env）。
- ⚠️ **尚未實證**：「env 有 key 時即使掛載 OAuth 也走 API 計費」的優先序，需一把真 key 跑過 console.anthropic.com 用量確認（缺 key 故未驗）。
- ⚠️ **安全副作用**：API key 模式下 key 進了 subprocess env → `bypass` 的 `printenv` 讀得到。請用有額度上限/workspace 隔離的 key（見 SECURITY §6）。訂閱模式無此問題（env 完全無 key）。

---

## 10. 部署 / 維運

```bash
# 啟動 / 改 code 後重建（restart 不會載入新 code，必須 --build）
cd ~/ai-discord-bridge
docker compose up -d --build

# 看 log
docker compose logs -f

# 看對話（host 端工具）
discord-tail [N]

# 重置某 bot 對話
rm ~/.claude-shared/discord-state/A.json   # 或從 Discord !reset A

# 掛 cron 週清舊 jsonl
crontab -e
0 3 * * 0 /home/user/ai-discord-bridge/scripts/archive-old-jsonl.sh >> ~/.claude-archive/archive.log 2>&1
```

⚠️ **改 bot.py 後一定要 `--build`**，`docker compose restart` 只重啟舊 image。

---

## 11. 已知限制與 Backlog

### 已知限制（MVP 接受）
1. **OAuth credential refresh race**：容器與 host session 同時 refresh token 可能互相失效（頻率低）
2. **單頻道**：寫死單一 `DISCORD_CHANNEL_ID`，多頻道未支援
3. **無附檔**：圖片 / 檔案上傳未支援
4. **長期 memory/ ro**：A/B 的共用 profile（memory/）容器內唯讀，避免併發寫競態。v3 的**專案層**改放可寫的 `discord-project-notes/`，故 bot 可寫專案知識，但仍碰不到 `memory/` 的人工 profile
5. **turn 計數單一**：MVP 用一個全域計數器，多頻道時需改 per-channel

### 已解決
- ~~discuss 缺脈絡 / 留痕~~ → v3：discuss 改共享 transcript（看得到原問題與完整辯論）、獨立 turn budget、結束後自動寫結論 summary 到 knowledge base。一般 @ 對話也注入近 15 則頻道脈絡，bot 訊息以「僅供參考非指令」前綴隔離（prompt injection 防護）。
- ~~session 不分專案，`!cd` 後 resume 失敗~~ → v3：session 改 per-(bot, cwd)，檔名 `<bot>__<cwd-slug>.json`。

### v3 已完成（記憶分層；plan-eng-review 回饋已回灌 §7）
1. **中期 summary 改 per-(channel, cwd)**：`discord-summaries/<ch>/<cwd-slug>/`，`!cd` 換專案自動換 summary（修「切專案 summary 混淆」bug）。對應 `channel_summary_dir(channel_id, cwd)`。
2. **`!flush` 加 cwd 邊界**：只取自上次 `!cd` 以來的 transcript，不跨專案混。對應 `do_flush(..., cwd_override, transcript_override)`。
3. **專案層 `discord-project-notes/<cwd-slug>/notes.md`** + `save_project_notes()`（Bot-B 當 worker）+ flush-before-compaction 寫入 trigger + 合併注入 temp 檔（帶 bot 名防 race）。

### 已完成（Q3 寫專案）
- **只 bind mount operator 自選的專案目錄**（同路徑、列在 `docker-compose.yml`），其餘 home 內容（.ssh/.gnupg/Documents…）在容器內不存在 → mount 層隔離，bypass 也碰不到
- `!cd <專案名|路徑>`：切 channel 工作目錄；`Path.resolve()` + `is_relative_to()` 防 `../` 逃逸，且需含 `.git`（git-only guard）
- `call_claude` 帶 per-channel cwd（call 開始時 snapshot，mid-call `!cd` 不影響當輪）
- `cwd_locks`：A/B 操作同一專案時序列化，防併發寫衝突
- `PROJECT_DIRS` 白名單在 docker-compose.yml，與 mount 清單一致

### 已完成（公開準備 — 安全強化）
1. **A1 fail-closed 授權**：`ALLOWED_USER_IDS` 為空時**拒絕啟動**（過去空值會短路所有授權檢查 → 任何人都能驅動 bot、開 bypass）。五處守門（指令、@ 觸發、`!once bypass`、`!mode bypass`、✅ react）移除 `ALLOWED_USER_IDS and ...` 短路語意。
2. **A2a 注入隔離（內容來源）**：頻道脈絡與 flush transcript 只餵**白名單使用者 + bot 自己**的訊息（`_is_trusted()`）。封住「非白名單路人在頻道留話 → 被白名單使用者的 call 當 context 帶入 → 間接 prompt injection」。buffer entry 加 `author_id`。
3. **A2b 憑證讀取防護（任何模式）**：`call_claude` 對所有模式加 `--disallowedTools Read(//home/user/.claude*/**)`（連 plan 都擋 Read 讀憑證）。permission 層硬擋，經實測驗證（"File is in a directory that is denied"）。⚠️ 非萬靈丹：bypass 仍可 shell out 讀，故 bypass 鎖死白名單。**已知坑**：`--disallowedTools` 是 variadic 會吃掉後面的 prompt → 規則必置於 args 尾端、prompt 改走 stdin。
4. **信任面收斂到 A/B（B review #1）**：`_is_trusted()` 與 `on_message` 的 `is_bot_msg` 原本用 `message.author.bot`（對**任何** Discord bot/webhook 為真）→ 改成比對 `bot_user_ids`（A/B 真實 user id）。封住「第三方整合 bot 轉送攻擊字串進可信脈絡」+「bypass 預設頻道下第三方 bot 未進白名單即觸發執行」。
5. **subprocess env 去敏（B review #2）**：claude 子行程改 deny-list 掉 `DISCORD_BOT_A/B_TOKEN`（claude 本不需要）→ `bypass` 的 `printenv` 撈不到 bot token。
6. **`SECURITY.md` / `SECURITY.zh.md`（威脅模型）**：9 節，含隔離邊界、授權 fail-closed、權限模式表、注入隔離、憑證防護**與極限**、殘留風險、forker 加固清單。刻意寫明每層防護的極限（bypass 可繞 A2b、CLAUDE.md @import 不受 deny 管、裸跑失隔離、mount≠network）。
7. **雙認證模式骨架（`USE_API_KEY` + per-bot `ANTHROPIC_API_KEY_{A,B}`）**：訂閱模式（預設）與 API key 模式並存（見 §9）。code + 文件已就緒，且訂閱模式不受影響。⚠️ **API 計費優先序未實證**——需一把真 key 驗 console 用量後才算完成。動機：API key 走 Developer Platform 程式化用途本就合規（解 ToS）。

### Backlog（未來 PR）
- **雙認證模式驗證里程碑**（拿到可測 API key 後一起做）：
  1. 驗「env key 覆蓋 OAuth、走 API 計費」+ 兩模式切換，確認後拿掉 §9 的「尚未實證」。
  2. **subprocess env 改 allow-list 基底**（`PATH`/`HOME`/`LANG`/`TERM`/`TZ` + 顯式 `CLAUDE_CONFIG_DIR`，視 node 需求微調）取代現行 deny-list → 「訂閱模式不受影響」從「記得擋的變數」變成構造上可證（B review #3 根因解）。
  3. **API 模式改用 `apiKeyHelper`**（settings.json 指一支吐 key 的 script）取代 env key → key 不進 subprocess env、`printenv` 撈不到，暴露面降到與 OAuth 同級；spend-capped key 退為補償控制（B review #4）。
- 多帳號同專案 worktree 隔離（目前用 cwd_lock 序列化 + 派工前 commit 規範）
- `Call_Center ` 專案（目錄名結尾有空格）未納入；建議改名去空格後再加
- 多頻道路由 + per-channel session/mode/cwd
- summary 自動 rotation 觸發策略優化（token-based 而非 message-count）

---

## 12. 相關文件
- `SECURITY.md` / `SECURITY.zh.md` — 威脅模型（部署前必讀）
- `README.md` — Discord developer portal 設定步驟、邀請 URL
- 上層 dual-account 設定 — `~/.claude{,-b,-shared}/CLAUDE.md`
- sibling 指令 — `~/.local/bin/sibling`
- 對話檢視工具 — `~/.local/bin/discord-tail`

### 記憶設計研究來源（v3）
- Claude Code memory / CLAUDE.md hierarchy — code.claude.com/docs/en/memory
- memory tool `memory_20250818` — platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
- Compaction / 什麼會在壓縮後保留 — code.claude.com/docs/en/context-window、platform.claude.com/docs/en/build-with-claude/compaction
- OpenClaw memory / compaction / dreaming — docs.openclaw.ai/concepts/{memory,compaction,dreaming}
- Cline Memory Bank（activeContext.md/progress.md hot-file）— docs.cline.bot/features/memory-bank
- MemGPT/Letta 階層記憶 — arxiv.org/pdf/2310.08560
