# ai-discord-bridge — 規格文件 (SPEC)

> 版本：v4 · 最後更新：2026-07-09
> 狀態：日常運作中；exec loop（背景任務 + diff 審查門）與雙容器 egress split 程式碼完成，split cutover 待 operator live smoke
> v4 變更：新增執行層（背景 job / worktree diff gate / 附件 / M4 verify + exec-Bash / M5 evaluator）、雙容器架構（frontend / executor / 雙 proxy）、`approve` tier、bot 專用精簡 config dir、`bot.py` 拆成 `bridge/` package
> v3 變更（歷史）：session/summary 改 per-(bot, cwd)、新增 per-project notes 層、flush-before-compaction

---

## 1. 這是什麼

把 Discord 變成「使用者 + 兩個對等 Claude 帳號 (A / B)」的三方協作聊天室，
並在其上疊一個**受審查的執行層**：執行模式的任務跑在 throwaway git worktree，
完成後貼 diff 等人按 ✅ 才合併。

後端 `claude -p --resume`，**雙認證模式**（見 §10）：預設走各帳號的**訂閱額度**
（Agent SDK credits）；亦可 `USE_API_KEY=true` 改走 **Developer Platform API key**
計費（key 以 apiKeyHelper 檔案供給，不進 subprocess env）。

兩個帳號是 dual-account 設定的延伸——但 bot **不再直接跑在** `~/.claude{,-b}`：
每隻 bot 有專用精簡 config dir（`~/.claude-bot-{a,b}`，無 PII、無 `@import`），
各帳號的 `.credentials.json` 以 cron 同步的唯讀 staged 副本供給（免重登、計費不變；單檔直掛會被 refresh 的 rename 換 inode 弄到永久過期——見 SECURITY §9）。

### 設計目標
- 在手機 / 任何 Discord client 跟兩個 AI 協作、博弈思想、輸出方案
- 交派程式任務：背景執行、即時進度、diff 審查門、可驗證（M4 verify）
- 兩帳號額度分流（A=slot 1、B=slot 2），不互相吃 quota
- 對話、記憶、授權、網路 egress 都可控（威脅模型見 SECURITY.md）

---

## 2. 系統分層（本專案在第 3 層）

```
┌─ 第 1 層：雙帳號隔離 ────────────────────────────────┐
│  ~/.claude (A) · ~/.claude-b (B) · ~/.claude-shared (共用)  │
│  cswap 監控 5h/7d 用量；symlink 共用 CLAUDE.md/skills/memory │
├─ 第 2 層：CLI 互通（sibling）───────────────────────┤
│  sibling "msg"        A↔B 同步派工                          │
│  sibling bg "msg"     非同步派工 + job 管理                  │
├─ 第 3 層：Discord 聊天室 + 執行層（本專案）─────────┤
│  容器跑兩個 discord.Client；@ 觸發 claude -p                 │
│  執行模式 → 背景 job + worktree diff gate                    │
└──────────────────────────────────────────────────────┘
```

---

## 3. 架構與資料流

### 3.1 雙容器拓撲（phase 2，`docker-compose.example.yml`）

egress-exec-isolation 的最終形：**憑證與 egress 反向配對**——每個 secret 所在的
容器，其網路都到不了「該 secret 有用武之地」以外的地方。

```
   [手機 / 桌面 Discord client]
              │
              ▼
   [Discord Gateway (WebSocket)]
              │
   ┌──────────┴───────────────  routeless internal networks  ─────────────┐
   │                                                                       │
   │  discord-frontend ──────► proxy-discord ──► (Discord hosts only)      │
   │   · discord.py + 全部 bridge 邏輯（指令/記憶/worktree git 操作）        │
   │   · 持有：Discord bot tokens                                          │
   │   · 沒有：Claude 憑證、bot config dirs、settings.json                  │
   │        │                                                              │
   │        │ semantic IPC（unix socket，掛共用 discord-state volume；      │
   │        │ 只傳語意參數，argv/env 不跨界，executor 逐參數驗證）           │
   │        ▼                                                              │
   │  executor ──────────────► proxy-anthropic ──► (api.anthropic.com only)│
   │   · 唯一會 spawn `claude -p` 的地方（+ M4 verify 子行程）              │
   │   · 持有：Claude OAuth 憑證 / API key                                  │
   │   · 沒有：Discord token、channel id、使用者白名單                      │
   └───────────────────────────────────────────────────────────────────────┘
```

- 兩個 app 容器都在 `internal: true`（routeless）網路上，唯一出口是各自的
  default-deny CONNECT proxy（tinyproxy + hostname allow-list，`./egress-proxy`）。
- **啟動 canary（fail-closed）**：每個容器開機時證明**自己那一側的 deny 方向**
  ——frontend 證明 Anthropic 不可達、executor 證明 Discord 不可達，加上
  allow-list 是 default-deny、必要 host 可達。任一不成立 → 拒絕服務。
- 單容器模式（`EXECUTOR_SOCKET` 未設）仍支援：claude 在本地 spawn，
  `EGRESS_PROXY_URL` 有設就跑 phase-1 canary、沒設就是無 egress 圍堵的開發姿勢。
  M4 的 verify/exec-Bash tier 在單容器**構造上 inert**（見 §7.4）。

### 3.2 一則訊息的生命週期

1. 兩個 client 同時收到 `on_message`（非本頻道 / 自己發的先濾掉）
2. **Bot-A 額外職責**：寫 channel buffer、累計訊息數、達門檻觸發背景 flush、解析 `!` 指令
3. 判斷是否被 mention（user mention 或 role mention）；信任過濾（白名單 + A/B 自己）
4. 過 turn budget 閘門（A/B 共用計數，防互答失控）
5. 決定 permission mode：`!once` override > channel 預設
6. **分流**：
   - `plan`（對話層）→ 直接 `call_claude`，回覆切塊送回
   - `edit` / `approve` / `bypass`（執行層）→ 建**背景 exec job**：git worktree、
     stream 進度、diff 審查門（見 §6）；`bypass` 先過 plan-then-execute ✅（`!yolo` 跳過）
7. split 部署下，每次 claude spawn 都是 frontend → executor 的 IPC 請求；
   單容器則本地 spawn

---

## 4. 檔案結構

### 4.1 Repo（`~/ai-discord-bridge/`）
```
ai-discord-bridge/
├── bot.py                       入口（薄 main()：load_config → job 回收/GC → canary gates → 啟動兩個 client）
├── executor.py                  phase-2 executor 入口（load_executor_config → egress canary → settings canary → serve IPC）
├── bridge/                      主套件
│   ├── config.py                env 讀取 + fail-closed 驗證（import 無副作用；load_config()/load_executor_config()）
│   ├── frontend.py              Discord 表面：on_message 路由、指令、diff gate 貼文、HELP/STARTUP
│   ├── runner.py                執行 chokepoint：claude args/env、settings canary、exec 串流、
│   │                            executor IPC（serve/client 兩側）、M4 verify + exec-settings
│   ├── jobs.py                  背景 job registry（記憶體 + JSON mirror；restart 回收、!jobs/!cancel）
│   ├── worktree.py              git worktree helpers（建立/commit/diff/merge/discard/GC）
│   ├── egress.py                egress canary（三探針；phase-1 與 split 兩種姿勢）
│   ├── memory.py                四層記憶：buffer/flush/summary/專案筆記/召回指路
│   ├── sessions.py              per-(bot, cwd) session id + per-channel state
│   ├── discuss.py               !discuss 辯論 + M5 evaluator（evaluate_diff）
│   ├── approver_ipc.py          approve tier 的 Discord ✅ socket server
│   ├── trust.py                 白名單/信任過濾、resolve_project_cwd（逃逸/git guard）
│   ├── state.py / util.py       全域執行期狀態 / 小工具
├── mcp_approver.py              approve tier 的 MCP permission-prompt server（claude 掛載）
├── approver-allowlist.json      approve tier 自動放行的指令 allow-list
├── settings.json                server 端安全設定（permissions.deny family；--settings 每 call 都帶）
├── egress-proxy/                default-deny CONNECT proxy（tinyproxy；filter.anthropic / filter.discord）
├── Dockerfile                   python:3.12-slim + Node20 + claude-code CLI + discord.py
├── docker-compose.example.yml   公開樣板：雙容器 + 雙 proxy + routeless 網路（真實 compose gitignore）
├── README.md / README.zh.md     公開入口（英 / 中）
├── SPEC.md                      ← 本文件（設計規格，中文）
├── SECURITY.md / SECURITY.zh.md 威脅模型（英 / 中；部署前必讀）
├── docs/                        內部計劃/修復紀錄
├── openspec/                    規格真相源（specs/ + changes/）
├── tests/                       pytest 套件（230 tests；安全關鍵邏輯 + fail-closed + exec loop）
├── .github/workflows/test.yml   CI：pip install + pytest
└── scripts/                     archive-old-jsonl.sh、refresh-cswap-usage.py、expire-oauth-token.sh
```

### 4.2 執行期資料（`~/.claude-shared/` 底下）
```
~/.claude-shared/
├── discord-state/                       bridge 狀態（rw 掛兩個容器 = IPC 共用面）
│   ├── <bot>__<cwd-slug>.json           per-(bot, cwd) session id
│   ├── channel_<channel_id>.json        per-channel 設定 {"mode","cwd"}
│   ├── executor.sock / approver.sock    split IPC / approve tier socket
│   ├── exec-settings.json               M4 exec-tier settings（每次 spawn 前重生，見 §7.4）
│   ├── jobs/<id>/                       job JSON mirror + diff.patch + attachments/
│   └── worktrees/<slug>/<id>/           exec job 的 throwaway git worktree
│
├── discord-verify/                      M4 verify 設定（executor 內 :ro 掛載 — 防偽造）
│   └── <cwd-slug>                       每專案一檔：shell verify 指令（operator 在 host 撰寫）
│
├── discord-summaries/<ch>/<cwd-slug>/   記憶中期層（latest.md + 歷史快照）
├── discord-project-notes/<cwd-slug>/    記憶專案層（notes.md + 快照）
├── plans/                               plan 落地區
└── memory/project_plan.md               薄索引；經 ~/.claude-bot-plan/ staged 副本 :ro 進容器

~/.claude-bot-a / ~/.claude-bot-b        bot 專用精簡 config dir（executor 才掛；憑證為 symlink）
~/.claude-bot-creds/{a,b}/               憑證 staging 目錄（host cron 同步、executor :ro 掛載）
~/.claude-bot-plan/                      plan 索引 staging 目錄（同 cron；兩容器 :ro 掛在 memory/ 路徑）
~/.claude/projects/... ~/.claude-b/...   host 端 session jsonl（bot 容器不掛）
```

---

## 5. 指令參考

> 指令只有 **Bot-A 處理**（避免雙 bot 重複執行）；需在白名單內的使用者才能下指令。

| 指令 | 行為 | 權限 |
|------|------|------|
| `@A` / `@B` | 對應 bot 回應；被 @ 才回。呼叫前注入近 15 則**受信任**頻道脈絡；bot 可 @對方 徵詢（受 `MAX_BOT_TURNS` 限制）| 白名單 |
| `@A @B` | 兩 bot 並行各自回（雙視角）| 白名單 |
| `!discuss <主題>` | A↔B 輪流辯論至 `MAX_BOT_TURNS`；共享滾動 transcript、獨立 turn budget；結束自動寫結論 summary | 白名單 |
| `!jobs` | 列出背景 exec job（執行中 / 待審，含 id/專案/年齡/狀態）| 白名單 |
| `!cancel <id>` | 取消執行中的 job（SIGTERM 整個 process group → 5s → SIGKILL）| 白名單 |
| `!merge <id>` | 合併某待審 job 的 `bridge/<id>` 分支到 live checkout（嚴格協定，見 §6.2）| 白名單 |
| `!discard <id>` | 丟棄待審 job（刪分支 + worktree + 狀態）| 白名單 |
| `!flush` | Bot-B 提煉當前 channel 對話 → 寫 summary（+ 專案筆記）| 白名單 |
| `!reset A\|B` | 清掉該 bot 主 session id（summary 保留；預設 A）| 白名單 |
| `!cd <專案名\|路徑>` | 切 channel 工作目錄（限白名單 git 專案；`~` 回根）；切離前自動 flush 舊專案 | 白名單 |
| `!mode plan\|edit\|bypass\|approve` | 設 channel 預設權限模式 | bypass/approve 需 opt-in tier + 白名單 |
| `!once <mode>` | 單一訊息用此模式（訊息末尾加）| 同上 |
| `!yolo` | bypass 跳過 plan-then-execute（單訊息）| 白名單 |
| `!state` | channel 模式 / cwd / buffer / summary / context tokens / A·B 5h·7d 用量 | 白名單 |
| `!help` | 指令參考 | 白名單 |

### 自動行為
- **自動 flush**：channel 累計 `AUTO_FLUSH_THRESHOLD`（預設 20）則訊息 → 背景 flush
- **token 三段式**：400k 寫 summary 存檔（保留對話線）、700k 濃縮+重置、900k 硬重置
- **summary + 專案筆記 prepend**：每次 call 合併注入 `--append-system-prompt-file`
- **跨 session 召回**：有更早摘要時在 system prompt 指路（摘要目錄 + 「自己 Grep/Read」），
  由 agent 自行檢索；刻意不做 bridge 端檢索管線
- **啟動公告**：重啟後 Bot-A 貼功能清單；有待審 job 會重新列出（`!merge`/`!discard` 續用）

---

## 6. 執行層（agent-exec-loop）

執行模式（`edit`/`approve`/`bypass`）的 @-mention 不再同步等待，而是變成**背景 exec job**：

### 6.1 背景 job + 串流（M1）
- job registry：記憶體 dict + `discord-state/jobs/` JSON mirror；重啟後 running → `orphaned`、
  待審 job 重載回 registry。每專案同時最多 **1 個** exec job（含待審——防從未審 HEAD 再分支）。
- `claude -p --output-format stream-json --verbose` 串流；狀態訊息以 reply 貼出、
  ≤1 edit / `EXEC_STATUS_EDIT_INTERVAL` 秒節流，帶 job id 與 `!cancel` 提示。
- `EXEC_TIMEOUT`（預設 1800s）獨立於 `CLAUDE_TIMEOUT`；逾時/取消都 kill 整個 process group。
- exec job 持 `cwd_locks[project]` 但**不持** `bot_locks` —— 長任務不擋同 bot 的對話呼叫。

### 6.2 Worktree + diff 審查門（M2）
- 任務跑在 `discord-state/worktrees/<slug>/<id>`（`git worktree add -b bridge/<id>`，從 HEAD 建）：
  **live checkout 全程不被碰**。subprocess cwd = worktree，但 session/鎖/token 記帳仍 key 在專案。
- 完成後 commit 到分支 → 貼 `--stat` + 完整 diff（≤1500 字 inline，否則 `.txt` 附件）→
  ✅ 合併 / ❌ 丟棄。逾時無人審 → **park 為待審**（永不自動丟棄），`!merge`/`!discard` 處理。
- 合併協定：持專案鎖；live tree `git status --porcelain` 不乾淨 → 拒絕（分支保留）；
  衝突 → `git merge --abort` + 回報 + 保留分支；永不 force。
- 啟動 GC：`git worktree prune` + 清掉非待審的 `bridge/<id>` 分支/worktree/job 狀態。

### 6.3 附件（M3）
- 白名單使用者訊息夾帶的附件下載到 `discord-state/jobs/<id>/attachments/`
  （**worktree 之外**——進不了 diff、蓋不掉 repo 檔）；檔名去路徑成分、
  單檔/總量/數量/時間四重上限。
- 路徑注入 exec prompt 時明確框成**未受信任的資料，非指令**。

### 6.4 Verify + exec-tier Bash（M4；gate rationale）
兩者是同一個信任決策（agent 級代碼執行），共用一個 flag（`ENABLE_EXEC_BASH`）與同一個
gate `runner.m4_live()`：**flag 有開 AND 跑在 phase-2 executor**（`EXECUTOR_SOCKET` 有設，
且該 executor 的「Discord 不可達」egress canary 開機已證明）。單容器 / frontend 側 →
posture 未證明 → tier **構造上 inert**（fail-closed），flag 開了也一樣。

Rationale：headless `acceptEdits` 不給 Bash，所以 exec job 本來不能跑 shell；一旦開 Bash，
名稱式 deny family 只是 speed-bump（`sh -c curl`、`python -c`… 可繞），**真正的圍堵是
executor 的 routeless egress**（只到 Anthropic；Discord 與任意 host 都沒路由）。所以 Bash
只在那個已證明的 posture 裡開放——同時這也讓 diff gate 健全（可寫面 = 受審面）。

- **exec-settings**：`write_exec_settings` 從 settings.json 衍生「base deny family + Bash allow」
  的檔；**每次 spawn 前重生**（exec agent 寫得到該 rw 路徑——竄改留不到下個 job）；
  只用於 live 串流 job，其他呼叫一律 base deny-only。開機另有 **exec-settings canary**：
  證明 Bash-allow 設定真的載入且 deny 仍生效（claude 對無效 settings 檔會無聲忽略），fail-closed。
- **post-task verify**：job 完成後，從 `VERIFY_CONFIG_DIR/<cwd-slug>`（預設
  `~/.claude-shared/discord-verify/`，executor 內 **:ro 掛載**）讀每專案 verify 指令，
  在 worktree 內以 stripped env（無 Discord token / API key）、獨立 `VERIFY_TIMEOUT`、
  獨立 process group 執行；結果（pass/fail + output tail）貼在 diff gate 上方。
  沒設定 → 明講「未設定 verify」，**絕不假綠**；verify 失敗只降級為註記，不擋 gate。
  指令**永不**從 repo/worktree 讀（agent 寫得到）；:ro 掛載使綠燈**不可被受檢 agent 偽造**。

### 6.5 Dual-account evaluator（M5，optional）
- `ENABLE_EXEC_EVALUATOR`（預設關）：job 的 diff 交給**另一隻** bot 審（sessionless、
  plan-mode、懷疑式 + 未受信任資料框架），findings 貼在 diff gate 上方。
- **advisory by construction**：evaluator 路徑碰不到 `pending_actions` 與 merge 路徑；
  任何失敗降級為 log；就算它說「merge it」也不會 merge——人類 ✅ 是唯一合併權威。

---

## 7. 權限模式（授權執行）

> 限制：`claude -p` 是一次性 subprocess，授權必須在呼叫前決定。
> 實證（preflight gate）：headless 下 `--allowedTools` **不會限制**（非列出的指令照跑）——
> 所以 edit/bypass 之間沒有硬能力邊界，真正的限制手段是 deny family、approve tier 與 egress 圍堵。

| 模式 | CLI 旗標 | 說明 |
|------|---------|------|
| `plan`（預設）| `plan` | 只讀規劃（對話層；唯一不走背景 job 的模式）|
| `edit` | `acceptEdits` | 自動接受檔案編輯；跑背景 job + diff gate；受 deny family 約束 |
| `approve` | `default` + MCP approver | **逐指令核可**：allow-list（`approver-allowlist.json`）自動放行，其餘丟 Discord 等 ✅；逾時/錯誤 = 拒絕（fail-closed）。需 `ENABLE_APPROVER_TIER` |
| `bypass` | `bypassPermissions` | 全自動；需 `ENABLE_BYPASS_TIER`（預設**結構性不可達**）+ 白名單；先過 plan-then-execute ✅（`!yolo` 跳過）|

- 每次 call 都帶 `--settings settings.json`（deny family：憑證路徑 Read、env dump、
  curl/wget/WebFetch），**deny 在所有模式含 bypass 都優先**。開機 **settings canary**
  證明檔案真的載入（claude 會無聲忽略驗證失敗的 settings），canary 不過 → 拒絕啟動；
  canary 對「無法跑 claude」(OAuth 過期等) 採 in-process backoff 重試，不進 crash-loop。
- plan-then-execute 是防誤觸的 speed-bump，**不是**對惡意請求的安全邊界——完整的
  邊界分析與殘餘風險見 SECURITY.md §4–§7。

---

## 8. 記憶管理（四層模型）

啟發自 OpenClaw 的 memory flush/dreaming、Claude Code 的 compaction 與 `MEMORY.md`、Cline Memory Bank 的 hot-file。
（研究來源見 §13；三方收斂的 pattern：index 常駐+detail 按需、flush-before-compaction、靜態 context 從 disk 重注入、按 cwd 切記憶。）

| 層 | 位置 | key | 機制 | 生命週期 |
|----|------|-----|------|---------|
| **短期** | `<sid>.jsonl` | (bot, cwd) | `--resume` 重載整段；Claude 內建 auto-compact | 持續累積到 reset |
| **中期** | `discord-summaries/<ch>/<cwd-slug>/latest.md` | (channel, cwd) | flush 提煉決策/任務/檔案/角色；呼叫時 prepend | 手動/自動 flush 更新 |
| **專案** | `discord-project-notes/<cwd-slug>/notes.md` | cwd | 慢變化架構文件；merge-on-write；呼叫時 prepend | 隨專案演進 |
| **長期** | `~/.claude-shared/memory/` | 全域 | host 端人工 profile；容器只掛 `project_plan.md`（:ro）| 跨 session 永久 |

### 為什麼這樣切 key（v3 設計核心）
- **短期/中期/專案 都按 cwd 切**：session 本來就是 Claude 按 cwd 分目錄存的，summary 與 project notes 若不跟著切，`!cd my-project` 後會 prepend 到一份**混了前一專案**的脈絡 → 誤導。
- **中期 = (channel, cwd)，專案 = 只 cwd**：summary 是某對話線的濃縮（channel 私有）；project notes 是專案級知識（多個 channel 在同一專案應**共享**那一份，不分裂）。
- **專案層為何不放 `memory/` 下**：`memory/` 屬 host 端人工 profile（bot-identity 隔離後容器只掛薄索引檔）。bot 要能寫，故 project notes 放在 rw 的 `discord-project-notes/`。

### flush-before-compaction
中期/專案層的更新**不只**靠 `!flush` 與訊息門檻被動觸發，還在以下時機主動落盤：
- `do_flush()` 跑完 → 同一份 transcript 順手更新對應 cwd 的 project notes（一個 flush 週期只跑一次）。
- `!cd` **切離**當前專案前 → 先對舊 cwd 跑一次 project-notes 更新。
- token 門檻（`FLUSH_TOKEN_THRESHOLD` / `RESET_TOKEN_THRESHOLD`）觸發 checkpoint / 濃縮重置。
- 不在每次 bot call 後寫 project notes —— 它是慢變化文件，非逐則對話。

### flush 一次呼叫雙段輸出
summary 與 project notes **不分兩次 `claude -p`**。`do_flush()` 用單次呼叫產出兩段，以分隔線切分後各自寫檔：
```
=== CHANNEL_SUMMARY ===   （決策/任務/檔案/角色 → 中期層）
=== PROJECT_NOTES ===     （merge 現有 notes + 本次對話 → 專案層）
```
- notes 更新的呼叫用 `cwd=DEFAULT_CWD`（不持專案鎖），不會卡在該專案 exec job 的鎖後面。
- DEFAULT_CWD（`/home/user`，非專案）不寫 project notes。

### 注入合併（單檔限制的處理）
`--append-system-prompt-file` 只吃一個檔案。呼叫前把「中期 summary + 專案 notes」合併成
一個 temp 檔再傳（帶 bot 名防 A/B 同 channel 互蓋；split 部署下放共用 volume 讓 executor 讀得到）。

### GC
merge-on-write + 寫前把舊版 rename 成 timestamped、留最近 3 份；notes 超長時 prompt 內強制壓縮。

### `--resume` 的本質（重要觀念）
- 每次 `claude -p` 進程都是**一次性**的；啟動時把整個 `<sid>.jsonl` 重新讀進來當 context。
- 比喻：每次失憶但帶日記的人——進程是人（一次性），jsonl 是日記（持久）。
- 代價：jsonl 越大每次重讀 token 越多 → summary 層負責濃縮；token 三段式門檻防撞頂。

---

## 9. A↔B 協作的兩條通道

| 通道 | 入口 | 用途 |
|------|------|------|
| **sibling CLI** | terminal / 各自 session 內 | A、B 程式化互相派工（同步/非同步 job）|
| **Discord** | #ai-chat | 使用者主導的三方對話、辯論、派任務、diff 審查 |

兩條通道的 session **完全獨立**（不同 session id 檔），不互相干擾。

---

## 10. 設定（環境變數）

| 變數 | 必填 | 預設 | 說明 |
|------|------|------|------|
| `DISCORD_BOT_A_TOKEN` / `_B_TOKEN` | ✅ | — | bot 權杖（只給 frontend 容器）|
| `DISCORD_CHANNEL_ID` | ✅ | — | 監聽的頻道 ID |
| `ALLOWED_USER_IDS` | ✅ | — | 可驅動 bot 的 user id（逗號分隔）；**留空 = fail-closed 拒絕啟動** |
| `PROJECT_DIRS` | ✅ | — | `!cd` 白名單（逗號分隔絕對路徑；須含 `.git`、與 bind mount 一致；executor 也用它驗 cwd）|
| `USE_API_KEY` | | false | 認證模式：false=訂閱 OAuth；true=apiKeyHelper 供 key |
| `ANTHROPIC_API_KEY_A` / `_B` | △ | — | per-bot key（或放 config dir 的 key 檔）；`USE_API_KEY=true` 缺 → 拒絕啟動 |
| `ENABLE_BYPASS_TIER` | | off | bypass tier opt-in（關閉時 bypass 結構性不可達）|
| `ENABLE_APPROVER_TIER` | | off | approve（逐指令核可）tier opt-in |
| `ENABLE_EXEC_BASH` | | off | M4：post-task verify + exec-tier Bash（只在 phase-2 executor 內 live，見 §6.4）|
| `ENABLE_EXEC_EVALUATOR` | | off | M5：另一帳號 advisory 審 diff |
| `MAX_BOT_TURNS` | | 6 | 互答/discuss 最大輪數（人類發言重置）|
| `CLAUDE_TIMEOUT` | | 300 | 對話呼叫逾時（秒）；**split 下兩容器須一致** |
| `EXEC_TIMEOUT` | | 1800 | 背景 exec job 逾時；**split 下兩容器須一致** |
| `EXEC_STATUS_EDIT_INTERVAL` | | 2.0 | 進度訊息 edit 節流（秒）|
| `EXEC_TRACE_LINES` | | 12 | 進度訊息保留的 tool-use trace 行數 |
| `EXEC_ATTACH_MAX_COUNT` / `_MAX_BYTES` / `_MAX_TOTAL_BYTES` / `_TIMEOUT` | | 5 / 10MB / 25MB / 60 | 附件上限 |
| `VERIFY_TIMEOUT` | | 600 | M4 verify 指令逾時；**split 下兩容器須一致**（frontend 等 +30s）|
| `VERIFY_OUTPUT_TAIL` | | 2000 | verify 輸出附回的尾巴長度 |
| `VERIFY_CONFIG_DIR` | | `~/.claude-shared/discord-verify` | verify 指令目錄（executor 內 :ro）|
| `EGRESS_PROXY_URL` | | — | 設定後啟用 egress 圍堵（proxy + canary）；未設 = 無圍堵 |
| `EXECUTOR_SOCKET` | | — | 設定後 = split 部署（frontend 變 IPC client；executor.py 綁此 socket）|
| `AUTO_FLUSH_THRESHOLD` | | 20 | 自動 flush 訊息門檻 |
| `FLUSH_TOKEN_THRESHOLD` / `RESET_TOKEN_THRESHOLD` / `HARD_RESET_TOKEN_THRESHOLD` | | 400k / 700k / 900k | token 三段式（0=關該段）|
| `PLAN_REACTION_TIMEOUT` | | 300 | ✅/❌ 等待秒數（plan 確認與 diff gate 皆用）|
| `CANARY_RETRY_BASE` / `_MAX` | | 15 / 300 | canary「暫時跑不動」的 in-process backoff |
| `BRIDGE_SETTINGS_PATH` | | 容器內掛載路徑 | 覆寫 server 端安全設定檔位置 |
| `BRIDGE_SKIP_CANARY` | | — | 只供離線開發；與 `ENABLE_BYPASS_TIER` 同設會拒絕啟動 |

### 認證模式（雙模式）
- **訂閱模式（預設）**：各 bot 用其精簡 config dir 內 bind-mount 進來的 OAuth 憑證；
  subprocess env 無條件 strip 整個 auth/計費路由家族（`ANTHROPIC_API_KEY*`、
  `ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_BASE_URL`、Bedrock/Vertex 開關）——host stray 值
  無法靜默改路由或計費。
- **API key 模式**：`USE_API_KEY=true`。key **不進 subprocess env**：啟動時 materialize 成
  config dir 內 0600 檔 + `apiKeyHelper` script（機制已 live 驗證：CLI 會諮詢它）；
  `printenv` 撈不到。⚠️ 「env/helper key 覆蓋 OAuth 走 API 計費」的**計費優先序尚未以
  真 key 對帳實證**——依賴前請用 spend-capped key 驗一次 console 用量。

---

## 11. 部署 / 維運

```bash
# 啟動 / 改 code 後重建（restart 不會載入新 code，必須 --build）
cd ~/ai-discord-bridge
docker compose up -d --build

# 看 log（split 部署三個服務名：discord-frontend / executor / proxy-*）
docker compose logs -f

# 重置某 bot 對話（或從 Discord !reset A）
rm ~/.claude-shared/discord-state/A__*.json

# 掛 cron 週清舊 jsonl
0 3 * * 0 /home/user/ai-discord-bridge/scripts/archive-old-jsonl.sh >> ~/.claude-archive/archive.log 2>&1
```

- ⚠️ **改 code 後一定要 `--build`**；proxy 的 allow-list filter 是 build 時烤進 image，
  改 `egress-proxy/filter.*` 也要 `--build`。
- **split cutover 程序**（含 OAuth refresh host 的 pin 法與已觀測結果）見 SECURITY.md §6。
- **M4 啟用前置**：split 跑起來 + `discord-verify/<cwd-slug>` 寫好 verify 指令（host 端），
  再設 `ENABLE_EXEC_BASH=1`（兩容器都設，compose 樣板已接好）。

---

## 12. 已知限制與 Backlog

### 已知限制（接受）
1. **OAuth credential refresh race**：容器與 host session 同時 refresh 可能互相失效（頻率低；
   split 下容器內憑證 :ro，refresh 由 host 保鮮）
2. **單頻道**：寫死單一 `DISCORD_CHANNEL_ID`，多頻道未支援；turn 計數也是單一全域計數器
3. **重啟遺失記憶體狀態**：進行中的 plan 確認、buffer、turn 計數重啟即消失（session/summary/待審 job 皆持久）
4. **restart 邊界的 committed-ungated 工作**：job 完成 commit 後、diff 貼出前重啟 →
   該分支不在待審清單、會被啟動 GC 收走（M5 審查已知 finding，M6+ 再議）
5. **無 rate limiting**：白名單使用者可自由花額度；`MAX_BOT_TURNS` 只管 bot↔bot

### 主要里程碑（已完成，細節見 openspec/）
- **v2**：雙帳號聊天室 + 權限模式 + plan-then-execute
- **v3**：四層記憶（per-(bot,cwd) session、(channel,cwd) summary、cwd 專案筆記、召回指路）
- **公開準備**：fail-closed 授權、注入隔離、憑證 deny family + settings canary、
  bilingual SECURITY、pytest + CI
- **egress-exec-isolation**：bot-identity 隔離（精簡 config dir、單檔憑證 mount、
  shared 目錄白名單掛載）→ apiKeyHelper → phase-1 egress proxy + 三探針 canary →
  refresh host 實測 pin（`api.anthropic.com` only）→ **phase-2 雙容器 split**（semantic IPC、
  per-container canary）
- **agent-exec-loop M1–M5**：背景 job → worktree diff gate → 附件 → verify + exec-Bash
  （phase-2 gated）→ dual-account evaluator
- **execution-permissions**：bypass tier 化（OV4）、approve（MCP approver）tier、
  exec-settings canary

### 待辦（operator / 收尾）
1. **egress task 5.4 — split live smoke**：切 live compose、兩 canary 綠、@ round-trip、
   OAuth refresh 走 executor proxy（程序見 SECURITY §6）
2. **exec-loop M4 live smoke**：split 跑起來 + 真 :ro verify config，@ 一個改碼任務看
   verify 結果貼上 diff gate
3. **exec-loop M6**：全量 smoke（文件 pass 已完成於本版）

### Backlog（未來）
- API 計費優先序實證（spend-capped key 對帳 console）
- restart 邊界 orphan 分支的保守 GC（上表限制 4）
- 多頻道路由 + per-channel turn budget
- `Call_Center ` 專案（目錄名結尾有空格）改名後再納入白名單

---

## 13. 相關文件
- `SECURITY.md` / `SECURITY.zh.md` — 威脅模型（部署前必讀；egress/deny/tier 的邊界與殘餘風險）
- `README.md` / `README.zh.md` — 設定步驟、啟動、用法
- `openspec/specs/` — 行為規格真相源（`agent-trust-layers`、`execution-permissions`、
  `bridge-module-boundaries`、`cross-session-recall`、`bot-identity-isolation`…）
- `docs/` — 內部修復/釋出計畫
- 上層 dual-account 設定 — `~/.claude{,-b,-shared}/CLAUDE.md`；sibling — `~/.local/bin/sibling`

### 記憶設計研究來源（v3）
- Claude Code memory / CLAUDE.md hierarchy — code.claude.com/docs/en/memory
- memory tool `memory_20250818` — platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
- Compaction — code.claude.com/docs/en/context-window、platform.claude.com/docs/en/build-with-claude/compaction
- OpenClaw memory / compaction / dreaming — docs.openclaw.ai/concepts/{memory,compaction,dreaming}
- Cline Memory Bank — docs.cline.bot/features/memory-bank
- MemGPT/Letta 階層記憶 — arxiv.org/pdf/2310.08560
