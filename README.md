# ai-discord-bridge

把 Discord 接成 dual-account Claude Code 的聊天室。

**架構**：兩個 Discord bot user（Bot-A、Bot-B）跑在同一個 Docker 容器內，分別對應 host 的 `~/.claude/`（帳號 A）與 `~/.claude-b/`（帳號 B）。被 @-mention 時呼叫 `claude -p --resume <sid>`，使用 Pro 帳號額度（非 Anthropic API）。

---

## 你需要先在 Discord 那邊做的事

1. 建一個 Discord server（或用既有的），開一個 channel `#ai-chat`
2. 到 https://discord.com/developers/applications 建立**兩個** application：`Claude-A`、`Claude-B`
3. 每個 application：
   - Bot 分頁 → Add Bot → 複製 **Token**
   - Privileged Gateway Intents → 打開 `MESSAGE CONTENT INTENT`
   - OAuth2 → URL Generator → scopes 勾 `bot`，permissions 勾 `Send Messages` + `Read Message History`
   - 用產生的 URL 把 bot 邀請進你的 server
4. 在 Discord client 開「開發者模式」（Settings → Advanced）
5. 右鍵 `#ai-chat` channel → 複製頻道 ID
6. 右鍵自己的 user → 複製 user ID

---

## 設定

```bash
cp .env.example .env
# 編輯 .env，填入：
#   DISCORD_BOT_A_TOKEN
#   DISCORD_BOT_B_TOKEN
#   DISCORD_CHANNEL_ID
#   ALLOWED_USER_IDS（你的 user id）
```

---

## 啟動

```bash
docker compose up -d --build
docker compose logs -f
```

---

## 使用

在 `#ai-chat` 內：
- `@Bot-A 幫我看這個 RAG 設計` → 只有 A 會回
- `@Bot-A @Bot-B 兩位都聊聊` → A、B 都會回
- A 回覆裡 `@Bot-B` → B 會看到並回應（互辯）
- A、B 互答總輪數累計 `MAX_BOT_TURNS`（預設 6）後停止；你再講話會重置

---

## 路徑說明（為什麼 bind mount 用同路徑）

Host 的 `~/.claude/skills` 是 symlink 指向 `~/.claude-shared/skills/`，且 `CLAUDE.md` 用 `@/home/user/.claude-shared/CLAUDE.md` 絕對路徑 import。容器內必須掛到**同樣絕對路徑** `/home/user/.claude{,-b,-shared}`，否則 symlink 和 import 都會斷。

`memory/` 子目錄掛 read-only：避免 bot 跟 host 互動 session 同時寫共用記憶造成競態。Bot 觀察學到的東西在 MVP 不持久化（若 claude 嘗試寫 memory，FS 會回 EACCES，container 內看到 error log 但不致命）。

---

## 已知殘留風險（MVP 接受）

1. **OAuth credential refresh race**：bot 跟 host 同時 refresh token 可能互相失效。token refresh 不頻繁，撞期機率低，先觀察。
2. **單頻道**：MVP 寫死單一頻道，多頻道路由未來再加。
3. **無附檔支援**、無 thread / reply 巢狀、無 slash command — 都是後續 backlog。

---

## 檔案

- `bot.py` — 主程式（兩個 discord.Client + claude 呼叫）
- `Dockerfile` — python:3.12-slim + Node 20 + claude-code CLI + discord.py
- `docker-compose.yml` — bind mount + restart 策略
- `.env.example` — 環境變數樣板
