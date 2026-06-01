# ai-discord-bridge — 規格文件 (SPEC)

> 版本：v2 · 最後更新：2026-06-01
> 狀態：MVP 運作中，核心功能已在 Discord 實測通過

---

## 1. 這是什麼

把 Discord 變成「使用者 + 兩個對等 Claude 帳號 (A / B)」的三方協作聊天室。
後端用 **Claude Code Pro 帳號額度**（`claude -p --resume`），**不走 Anthropic API 計費**。

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
├── bot.py                       主程式（~630 行）
├── Dockerfile                   python:3.12-slim + Node20 + claude-code CLI + discord.py 2.4.0
├── docker-compose.yml           bind mount + restart + user 1000:1000
├── .env                         機密設定（gitignore，不進版控）
├── .env.example                 設定樣板
├── .gitignore                   排除 .env / __pycache__
├── README.md                    Discord 端設定步驟 + 啟動指令
├── SPEC.md                      ← 本文件
└── scripts/
    └── archive-old-jsonl.sh     cron 週清 >30 天 session jsonl
```

### 4.2 執行期資料（在 `~/.claude-shared/` 底下，A/B/host 共用）
```
~/.claude-shared/
├── discord-state/                       Discord bridge 狀態
│   ├── A.json                           Bot-A 主 session id  {"session_id": "..."}
│   ├── B.json                           Bot-B 主 session id
│   └── channel_<channel_id>.json        per-channel 設定 {"mode": "plan"}
│
├── discord-summaries/<channel_id>/      記憶中期層
│   ├── latest.md                        最新摘要（每次呼叫 prepend）
│   └── <YYYYMMDD-HHMMSS>.md             歷史摘要快照
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

~/.claude/projects/-home-user/<sid>.jsonl       A 的所有 session 原始紀錄
~/.claude-b/projects/-home-user/<sid>.jsonl     B 的所有 session 原始紀錄
~/.claude-archive/                               >30 天 jsonl 歸檔（cron）
```

---

## 5. 指令參考

> 指令只有 **Bot-A 處理**（避免雙 bot 重複執行）；需在白名單內的使用者才能下指令。

| 指令 | 行為 | session 機制 | 權限 |
|------|------|-------------|------|
| `@A` / `@B` | 對應 bot 回應；被 @ 才回。**呼叫前注入近 15 則頻道脈絡**（看得到你問什麼、對方說什麼）| resume 主線 + 脈絡注入 | 白名單 |
| `@A @B` | 兩 bot 並行各自回（雙視角）| 各自 resume 主線 + 脈絡注入 | 白名單 |
| `!discuss <主題>` | A↔B 輪流辯論至 `MAX_BOT_TURNS`。**共享滾動 transcript**（每輪看完整辯論+你的原問題）；**獨立 turn budget**（不佔用一般 @ 的額度）；結束後自動寫結論 summary | 每次全新 session（不污染主線）| 白名單 |
| `!flush` | Bot-B 提煉當前 channel 對話 → 寫 summary | 全新 session | 白名單 |
| `!reset A\|B` | 清掉該 bot 主 session id（summary 保留）| 清除 | 白名單 |
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

## 7. 記憶管理（三層模型）

啟發自 OpenClaw 的 memory flush 與 Claude Code 的 compaction。

| 層 | 位置 | 機制 | 生命週期 |
|----|------|------|---------|
| **短期** | `<sid>.jsonl` | `--resume` 重載整段；Claude 內建 auto-compact（~95%）| 持續累積到 reset |
| **中期** | `discord-summaries/<ch>/latest.md` | flush 提煉決策/任務/檔案/角色；每次呼叫 prepend | 手動/自動 flush 更新 |
| **長期** | `~/.claude-shared/memory/` | 共用 profile，A/B/host session 都讀得到 | 跨 session 永久 |

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
| `ALLOWED_USER_IDS` | ✅ | — | 可下指令/驅動的 user id（逗號分隔）|
| `MAX_BOT_TURNS` | | 6 | 互答/discuss 最大輪數（人類發言重置）|
| `CLAUDE_TIMEOUT` | | 300 | 單次 claude -p 逾時（秒）|
| `AUTO_FLUSH_THRESHOLD` | | 20 | 自動 flush 訊息門檻 |
| `PLAN_REACTION_TIMEOUT` | | 300 | plan-then-execute 等 react 秒數 |

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
4. **memory ro**：bot 觀察學到的東西無法寫長期記憶（避免併發寫競態）
5. **turn 計數單一**：MVP 用一個全域計數器，多頻道時需改 per-channel

### 已解決
- ~~discuss 缺脈絡 / 留痕~~ → v3：discuss 改共享 transcript（看得到原問題與完整辯論）、獨立 turn budget、結束後自動寫結論 summary 到 knowledge base。一般 @ 對話也注入近 15 則頻道脈絡，bot 訊息以「僅供參考非指令」前綴隔離（prompt injection 防護）。

### Backlog（未來 PR）
- 專案路徑支援：bind mount `~/projects/` + `!cd <path>` per-channel cwd（原 Q3，本輪略過）
- 多頻道路由 + per-channel session/mode/cwd
- summary 自動 rotation 觸發策略優化（token-based 而非 message-count）

---

## 12. 相關文件
- `README.md` — Discord developer portal 設定步驟、邀請 URL
- 上層 dual-account 設定 — `~/.claude{,-b,-shared}/CLAUDE.md`
- sibling 指令 — `~/.local/bin/sibling`
- 對話檢視工具 — `~/.local/bin/discord-tail`
