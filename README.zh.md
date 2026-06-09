# ai-discord-bridge

> English version: [README.md](README.md) ｜ 設計規格： [SPEC.md](SPEC.md) ｜ 威脅模型： [SECURITY.zh.md](SECURITY.zh.md)

自架的雙 AI Discord 夥伴——一個可運作的、個人規模的參考實作，示範四層記憶模型與
A↔B 辯論編排模式（基於 Claude Code）。

> **狀態**：個人實驗 / MVP。單頻道、無測試套件、無支援 SLA。歡迎 fork 與改造——但別
> 期待維護或回 issue。

> ⚠️ **安全**：這個工具讓白名單內的 Discord 使用者以你的主機使用者身分、在你掛載的
> 目錄裡執行程式碼（`bypass` 模式 = 任意執行）。**部署前請先讀
> [SECURITY.zh.md](SECURITY.zh.md)**——對這類專案，威脅模型與加固清單不是可選讀物。

它是一個 **Claude Code 的 control plane**：兩個 Discord bot（Bot-A、Bot-B）跑在同一個
Docker 容器內；一次 @-mention 變成一個 `claude -p --resume <sid>` 呼叫，疊上頻道脈絡、
四層記憶、per-channel 權限模式。價值在於當作 **dual-agent 編排 / 記憶分層 / Discord
control plane** 的參考實作——不是即裝即用的產品。每個 bot 綁定自己的 Claude Code 設定
目錄（`~/.claude/`、`~/.claude-b/`）；認證/計費選項見下方 [認證模式](#認證模式)。

## 架構亮點

- **四層記憶**：per-session `.jsonl` → per-(channel, cwd) 中期摘要 → per-cwd 專案筆記
  → 全域長期 profile（容器內唯讀）
- **flush-before-compaction**：由 `!flush`、訊息門檻、`!cd` 切專案觸發——在 Claude 的
  context window 自動壓縮前先保存決策
- **雙 agent 辯論**：`!discuss <主題>`——A、B 在共享滾動 transcript 上輪流發言，獨立的
  turn budget 不會餓死一般的 @-mention
- **權限分層**：per-channel 的 `plan` / `edit` / `bypass` 模式；`bypass` 需明確白名單；
  fail-closed 授權 + prompt injection 隔離 + 憑證讀取 deny（見 [SECURITY.zh.md](SECURITY.zh.md)）

## 事前準備

- 兩個 Claude Code 帳號（Pro 或 Max），分別登入主機的 `~/.claude/` 與 `~/.claude-b/`
- 兩個 Discord bot token（每帳號一個）

<a id="認證模式"></a>
### 認證模式

- **API key 模式**（`USE_API_KEY=true` + per-bot `ANTHROPIC_API_KEY_A`/`_B`）——**公開 /
  forker 使用的建議路徑。** 走 Developer Platform 計費,對自動化 bot 而言是更乾淨的
  ToS 立足點。⚠️ 其計費路由**尚未對真 key 實證**（見 [SPEC.md](SPEC.md) §9）——依賴前請
  用**有額度上限**的 key 驗一次,且注意 key 會在 subprocess 環境裡（[SECURITY.zh.md](SECURITY.zh.md) §6）。
- **訂閱模式**（預設,掛載的 `~/.claude{,-b}` 憑證）——保留給作者個人/本機環境。用訂閱
  憑證跑自動化 bot 是較灰色的 ToS 地帶,所以把它當*相容預設,而非推薦*。此模式下
  `claude -p` 消耗 **Agent SDK credits**（預付池:Pro $20 / Max 5× $100 / Max 20× $200;
  用盡即硬停）。把 `MAX_BOT_TURNS` 設保守以控制花費。

## Discord 設定

1. 建一個 server（或用既有的），開一個 `#ai-chat` 頻道
2. 到 [discord.com/developers/applications](https://discord.com/developers/applications)
   建立**兩個** application：`Claude-A`、`Claude-B`
3. 每個 application：
   - Bot 分頁 → Add Bot → 複製 **Token**
   - Privileged Gateway Intents → 開啟 `MESSAGE CONTENT INTENT`
   - OAuth2 → URL Generator → scopes 勾 `bot`，permissions 勾 `Send Messages` +
     `Read Message History`
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

把 `docker-compose.example.yml` 複製成 `docker-compose.yml`，並把 volume 掛載改成你實際
的專案目錄。

## 啟動

```bash
docker compose up -d --build
docker compose logs -f
```

## 驗證你的部署（smoke test）

單元測試（`pip install -r requirements-dev.txt && pytest`）涵蓋安全關鍵邏輯——fail-closed 授權、`!cd` 路徑/逃逸防護、信任過濾、subprocess env 去敏——並在 CI 跑。它們不碰真 Discord/Claude，所以端到端接線用手動確認：

1. `docker compose config`——compose 檔可解析、mount 路徑解得開。
2. **fail-closed 授權**：把 `ALLOWED_USER_IDS` 清空啟動 → 容器須立刻退出（`refusing to start`）。再設回你的 id。
3. **bot 在線**：`docker compose logs` 顯示 A、B 都 `logged in as ...`。
4. **用白名單帳號**在頻道：`!help`、`!state`、`!mode plan`、`!cd <你的專案>`，然後 `@Bot-A hello` → A 回應。
5. **API key 模式**（若啟用）：`USE_API_KEY=true` 但 key 留空 → 容器須拒絕啟動。

## 用法

在 `#ai-chat` 內：

| 輸入 | 效果 |
|------|------|
| `@Bot-A <訊息>` | 只有 A 回 |
| `@Bot-A @Bot-B <訊息>` | 兩個都回 |
| A 在回覆中 @-mention `@Bot-B` | B 回應（辯論模式） |
| 你發任何訊息 | 重置 A↔B 輪數計數器 |

**指令**（前綴 `!`，只由 Bot-A 處理以避免雙觸發）：

| 指令 | 效果 |
|------|------|
| `!cd /path/to/project` | 切工作目錄；先 flush 前一專案脈絡 |
| `!flush` | 手動 flush——存中期摘要 + 專案筆記 |
| `!discuss <主題>` | 結構化 A↔B 辯論（共享滾動 transcript） |
| `!mode plan\|edit\|bypass` | 設此頻道的權限模式 |
| `!reset a\|b` | 清掉某 bot 的 session（摘要保留） |
| `!state` | 顯示頻道狀態、buffer、摘要狀態 |

> 完整指令表（含 session 機制與權限欄）見 [SPEC.md](SPEC.md) §5。

A↔B 輪數計數器在 `MAX_BOT_TURNS`（預設 6）硬停。

## 為什麼 bind mount 用相同絕對路徑

`~/.claude/skills` 是 symlink 指向 `~/.claude-shared/skills/`，且 `CLAUDE.md` 用
`@/home/user/.claude-shared/CLAUDE.md`（絕對路徑 import）。容器必須掛到**相同絕對路徑**
`/home/user/.claude{,-b,-shared}`，否則 symlink 與 `@import` 會無聲失效。

`memory/` 子目錄以**唯讀**掛載，避免 bot 容器與互動式 host session 互相寫競態。

## 已知限制

1. **單頻道**——MVP 寫死單一頻道 ID，多頻道路由在 backlog。
2. **OAuth refresh 競態**——bot 與 host 可能在 token refresh 上競態。實務罕見；MVP 接受。
3. **無附檔**、無 thread/reply 巢狀、無 slash command——皆未來 backlog。
4. **測試有限**——單元測涵蓋安全關鍵純邏輯（授權、路徑/信任防護、env 去敏）並在 CI 跑;無對真 Discord/Claude 的整合測（那部分靠手動 smoke test）。

## 無支援

這是個人日常使用的專案，不是維護中的 library。歡迎 PR，但無法保證 review 或即時回應。
壞掉的話，整個實作都在 `bot.py`。

## 授權

MIT——見 [LICENSE](LICENSE)。
