# ai-discord-bridge

> English version: [README.md](README.md) ｜ 設計規格： [SPEC.md](SPEC.md) ｜ 威脅模型： [SECURITY.zh.md](SECURITY.zh.md)

自架的雙 AI Discord 夥伴——一個可運作的、個人規模的參考實作，示範四層記憶模型、
A↔B 辯論編排，以及一個**受審查門控的執行迴圈**（基於 Claude Code）。

> **狀態**：個人實驗。單頻道、無支援 SLA。歡迎 fork 與改造——但別期待維護或回 issue。
> 安全關鍵邏輯由 296 個 pytest 測試覆蓋並在 CI 執行。

> ⚠️ **安全**：這個工具讓白名單內的 Discord 使用者以你的主機使用者身分、在你掛載的
> 目錄裡執行程式碼。**部署前請先讀 [SECURITY.zh.md](SECURITY.zh.md)**——對這類專案，
> 威脅模型與加固清單不是可選讀物。

它是一個 **Claude Code 的 control plane**：兩個 Discord bot（Bot-A、Bot-B）；一次
@-mention 變成一個 `claude -p --resume <sid>` 呼叫，疊上頻道脈絡、四層記憶、per-channel
權限模式。執行模式的任務以**背景 job 跑在 throwaway git worktree**，貼出 diff 等你核可
才會碰到你的 checkout。價值在於當作 **dual-agent 編排 / 記憶分層 / egress 圍堵 /
Discord control plane** 的參考實作——不是即裝即用的產品。每隻 bot 跑在專用精簡設定目錄
（`~/.claude-bot-{a,b}`），帳號憑證以 cron 同步的唯讀 staged 副本掛進去（[SECURITY.zh.md](SECURITY.zh.md) §9）；認證/計費選項見下方
[認證模式](#認證模式)。

## 架構亮點

- **四層記憶**：per-session `.jsonl` → per-(channel, cwd) 中期摘要 → per-cwd 專案筆記
  → 全域長期 profile（容器內只掛薄索引、唯讀）
- **flush-before-compaction**：由 `!flush`、訊息門檻、token 門檻、`!cd` 切專案觸發——
  在 Claude 的 context window 自動壓縮前先保存決策
- **雙 agent 辯論**：`!discuss <主題>`——A、B 在共享滾動 transcript 上輪流發言，獨立的
  turn budget 不會餓死一般的 @-mention
- **受審查門控的執行迴圈**：執行模式任務變成背景 job（`!jobs` / `!cancel`）即時串流進度；
  改動落在 throwaway git worktree，完成後貼 diff 等你 ✅ 合併 / ❌ 丟棄（逾時後
  `!merge` / `!discard`）。可選的每專案 **post-task verify** 與**另一帳號 advisory 審查**
  會貼在審查門上方。
- **權限分層**：per-channel 的 `plan` / `edit` / `approve` / `bypass`；`approve` 是逐指令
  人工核可 tier（MCP approver），`bypass` 未 opt-in 時結構性不可達；fail-closed 授權 +
  prompt injection 隔離 + canary 驗證過的憑證讀取 deny family（見 [SECURITY.zh.md](SECURITY.zh.md)）
- **egress 圍堵（雙容器 split）**：`discord-frontend`（只能連 Discord，持 bot token）與
  `executor`（持 Claude 憑證；egress 限 `api.anthropic.com` 加一小串 GET-only 唯讀文件
  allow-list，另可 opt-in 開啟唯讀 PyPI host 讓 agent 自行安裝 Python 依賴——仍無可
  publish 的 host，預設關閉，見 [SECURITY.md](SECURITY.md) §6）各自跑在 routeless internal 網路、
  各配一個 default-deny proxy——每個 secret 所在容器的網路都到不了另一個 secret 的
  用武之地。開機 canary fail-closed 證明各自的 deny 方向。

## 事前準備

- 兩個 Claude Code 帳號（Pro 或 Max），在 host 登入
- 兩個 Discord bot token（每帳號一個）
- 專用精簡 bot 設定目錄 `~/.claude-bot-{a,b}` + 憑證 staging 目錄與同步 cron——一次性設定見 [SECURITY.zh.md](SECURITY.zh.md) §9

<a id="認證模式"></a>
### 認證模式

- **API key 模式**（`USE_API_KEY=true` + per-bot `ANTHROPIC_API_KEY_A`/`_B`）——**公開 /
  forker 使用的建議路徑。** 走 Developer Platform 計費，對自動化 bot 而言是更乾淨的
  ToS 立足點。key **不進 subprocess 環境**：啟動時 materialize 成 0600 檔、由
  `apiKeyHelper` script 供給（見 [SECURITY.zh.md](SECURITY.zh.md) §6）。⚠️ 其計費*路由*
  **尚未對真 key 實證**（見 [SPEC.md](SPEC.md) §10）——依賴前請用**有額度上限**的 key 驗一次。
- **訂閱模式**（預設，各帳號的 `.credentials.json` 以 cron 同步的唯讀 staged 副本進容器——[SECURITY.zh.md](SECURITY.zh.md) §9）——保留給
  作者個人/本機環境。用訂閱憑證跑自動化 bot 是較灰色的 ToS 地帶，所以把它當*相容預設，
  而非推薦*。此模式下 `claude -p` 消耗 **Agent SDK credits**（預付池：Pro $20 / Max 5×
  $100 / Max 20× $200；用盡即硬停）。把 `MAX_BOT_TURNS` 設保守以控制花費。

## Discord 設定

1. 建一個 server（或用既有的），開一個 `#ai-chat` 頻道
2. 到 [discord.com/developers/applications](https://discord.com/developers/applications)
   建立**兩個** application：`Claude-A`、`Claude-B`
3. 每個 application：
   - Bot 分頁 → Add Bot → 複製 **Token**
   - Privileged Gateway Intents → 開啟 `MESSAGE CONTENT INTENT`
   - OAuth2 → URL Generator → scopes 勾 `bot`，permissions 勾 `Send Messages` +
     `Read Message History` + `Add Reactions` + `Attach Files`
   - 用產生的 URL 把 bot 邀進你的 server
4. 在 Discord client 開「開發者模式」（Settings → Advanced）
5. 右鍵 `#ai-chat` → 複製頻道 ID
6. 右鍵你自己的 user → 複製 user ID

## 設定

```bash
cp .env.example .env
# 填入：
#   DISCORD_BOT_A_TOKEN
#   DISCORD_BOT_B_TOKEN
#   DISCORD_CHANNEL_ID
#   ALLOWED_USER_IDS   （你的 Discord user ID）
```

把 `docker-compose.example.yml` 複製成 `docker-compose.yml`，並把專案 bind mount 與
`PROJECT_DIRS`（**兩個 service 都要**）改成你實際的專案目錄。樣板是**雙容器 split**
（frontend / executor / 兩個 egress proxy）；請讀檔頭註解——有幾個環境變數是跨行程
耦合的，兩個 service 必須設一致。

## 啟動

```bash
docker compose up -d --build
docker compose logs -f
```

## exec job 內的依賴安裝

executor 沒有一般的對外路由，agent 只裝得到 proxy allow-list 放行的東西。

- **Python**：預設關閉。把 compose 裡 `proxy-anthropic` 底下的
  `EXTRA_FILTER: filter.pypi` 取消註解，再
  `docker compose build proxy-anthropic && docker compose up -d`，agent 就能從 PyPI 安裝。
  只開讀取 host，`upload.pypi.org` 維持封鎖。**安裝必須進 venv**——
  `python3 -m venv .venv && ./.venv/bin/pip install …`——因為 user-site 安裝會跨 job
  持久存在（其中的 `.pth` 會在每次直譯器啟動時執行，包含持有憑證的 executor）。裸的
  `pip install` 會以 `Could not find an activated virtualenv (required)` 失敗，那是護欄
  不是 bug；每專案的 verify 指令也應改用該 venv。啟用前請先讀
  [SECURITY.zh.md](SECURITY.zh.md) §6——殘留風險在那裡逐項列出，沒有被含糊帶過，其中
  包含「verify 期間第三方程式碼會在 import 時執行」這一項。
- **Node**：**維持 vendored，沒有 opt-in 可用。** `registry.npmjs.org` 的 publish 端點就是
  同一個 host，放行等於給被注入的 agent 一條可自帶 token 的寫出通道。請在 host 跑
  `npm ci`，把 `node_modules` 隨專案掛進容器，agent 離線跑 `npm test`。無論如何，所有
  spawn 出來的子行程都停用 lifecycle script。npm 自動安裝的**解鎖條件**是：安裝改在
  無憑證 build 容器內進行——那裡就算 index 可達，攻擊者也沒有東西可偷。

兩條 spawn 路徑（agent 與 verify 指令）一律帶 `npm_config_ignore_scripts=true`、
`PIP_PREFER_BINARY=1`、`PIP_REQUIRE_VIRTUALENV=1` 與 `PIP_NO_CACHE_DIR=1`，與 opt-in
是否開啟無關。它們防的是誤觸而非有敵意的 agent——真正的邊界是 routeless egress。

## 驗證你的部署（smoke test）

單元測試（`pip install -r requirements-dev.txt && pytest`，296 個）涵蓋安全關鍵邏輯——
fail-closed 授權、`!cd` 路徑/逃逸防護、信任過濾、env 去敏、exec loop 的 job/worktree/verify
機制、egress canary 邏輯——並在 CI 跑。它們不碰真 Discord/Claude，所以端到端接線用手動確認：

1. `docker compose config`——compose 檔可解析、mount 路徑解得開。
2. **fail-closed 授權**：把 `ALLOWED_USER_IDS` 清空啟動 → 容器須立刻退出（`refusing to start`）。再設回你的 id。
3. **canary 全綠**：log 顯示 egress canary（split 下每容器各自跑）與 settings-deny canary 都在服務前通過。
4. **bot 在線**：`docker compose logs` 顯示 A、B 都 `logged in as ...`。
5. **用白名單帳號**在頻道：`!help`、`!state`、`!mode plan`、`!cd <你的專案>`，然後 `@Bot-A hello` → A 回應。
6. **exec round-trip**：`!mode edit`，請 bot 做個小改動 → 背景 job 串流進度、貼 diff、你按 ✅ 合併（❌ 丟棄）。
7. **API key 模式**（若啟用）：`USE_API_KEY=true` 但 key 留空 → 容器須拒絕啟動。

## 用法

在 `#ai-chat` 內：

| 輸入 | 效果 |
|------|------|
| `@Bot-A <訊息>` | 只有 A 回 |
| `@Bot-A @Bot-B <訊息>` | 兩個都回 |
| A 在回覆中 @-mention `@Bot-B` | B 回應（辯論模式） |
| 你發任何訊息 | 重置 A↔B 輪數計數器 |

`plan` 模式（預設）下 mention 是一般對話呼叫。`edit` / `approve` / `bypass` 模式下則變成
**背景 exec job**：工作在 throwaway git worktree 進行、進度串流到狀態訊息、完成的 diff
等你 ✅/❌。觸發訊息上的附件會被收進 job 當未受信任的 context。

**指令**（前綴 `!`，只由 Bot-A 處理以避免雙觸發）：

| 指令 | 效果 |
|------|------|
| `!cd <專案>` | 切工作目錄（限白名單 git 專案）；先 flush 前一專案脈絡 |
| `!mode plan\|edit\|bypass\|approve` | 設此頻道的權限模式（`bypass`/`approve` 需各自的 opt-in tier） |
| `!jobs` | 列出背景 exec job（執行中 / 待審） |
| `!cancel <id>` | 取消執行中的 job（終止整個 process group） |
| `!merge <id>` / `!discard <id>` | 合併 / 丟棄某個待審 diff |
| `!discuss <主題>` | 結構化 A↔B 辯論（共享滾動 transcript） |
| `!flush` | 手動 flush——存中期摘要 + 專案筆記 |
| `!reset a\|b` | 清掉某 bot 的 session（摘要保留） |
| `!state` | 顯示頻道狀態、cwd、context tokens、帳號用量 |

> 完整指令表（含 session 機制與權限欄）見 [SPEC.md](SPEC.md) §5；執行層見 §6。

A↔B 輪數計數器在 `MAX_BOT_TURNS`（預設 6）硬停。

## 為什麼 bind mount 用相同絕對路徑

容器內的設定與狀態路徑錨定在 `/home/user/...`，且 exec job 的 worktree 以絕對路徑連回
各專案的 `.git`。專案目錄與 `~/.claude-shared` 的狀態子目錄必須掛到與 host **相同的
絕對路徑**（樣板 compose 就是這樣寫的）——否則 `!cd` 白名單、worktree 合併、共用
volume 上的 IPC socket 都會無聲失效。

`memory/project_plan.md` 索引與 M4 的 `discord-verify/` 設定目錄刻意以**唯讀**掛載——
後者正是讓 post-task verify 訊號無法被受檢 agent 偽造的關鍵（[SECURITY.zh.md](SECURITY.zh.md) §4）。

## 已知限制

1. **單頻道**——寫死單一頻道 ID；turn 計數器是全域的。多頻道路由在 backlog。
2. **OAuth refresh 競態**——bot 與 host 可能在 token refresh 上競態。實務罕見；split 部署
   下容器內憑證是唯讀掛載，由 host 保鮮。
3. **重啟遺失記憶體狀態**——進行中的 plan 確認、buffer、turn 計數會重置；session、摘要、
   待審 job 都持久。「已 commit 但 diff 還沒貼出」邊界上的 job 會在重啟 GC 被收走
   （已知 finding，目前接受）。
4. **無 thread/reply 巢狀、無 slash command**——未來 backlog。
5. **測試是單元層級**——套件在 CI 覆蓋安全關鍵與 exec loop 邏輯；無對真 Discord/Claude
   的整合測（那部分靠上面的手動 smoke test）。

## 無支援

這是個人日常使用的專案，不是維護中的 library。歡迎 PR，但無法保證 review 或即時回應。
壞掉的話，實作在 `bridge/`（入口：`bot.py`、`executor.py`）。

## 授權

MIT——見 [LICENSE](LICENSE)。
