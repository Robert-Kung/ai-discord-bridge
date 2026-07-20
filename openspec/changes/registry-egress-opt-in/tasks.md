## 1. Proxy 側 opt-in 機制

- [x] 1.1 新增 `egress-proxy/filter.pypi`：`^pypi\.org$`、`^files\.pythonhosted\.org$` 兩條；檔頭註記刻意排除 `registry.npmjs.org`（publish 端點同 host）、`upload.pypi.org`、`github.com`/`codeload`，並寫明僅供 executor 側 split 部署
- [x] 1.2 `egress-proxy/Dockerfile`：無條件 `COPY filter.* /tmp/filters/`，`RUN` 依 `ARG EXTRA_FILTER=""` 條件拼接（拼接前補 `printf '\n'`）。驗收＝EXTRA_FILTER 為空時 `/etc/tinyproxy/filter` 與 `filter.anthropic` 逐位元組相同
- [x] 1.3 Dockerfile build-time 斷言：`FILTER=filter.discord`（或合併 `filter`）且 `EXTRA_FILTER` 非空 → build 失敗並印出原因
- [x] 1.4 `docker-compose.example.yml`：proxy-anthropic args 加註解掉的 `EXTRA_FILTER: filter.pypi`，說明需 `docker compose build proxy-anthropic`，並明示不適用於 proxy-discord 與單容器姿勢
- [x] 1.5 測試：無 EXTRA_FILTER 時 filter 無 pypi 條目；有 EXTRA_FILTER 時 pypi 兩 host 在列且原 Anthropic／文件站條目一條不少；`filter.discord` + EXTRA_FILTER 的 build 必失敗
- [x] 1.6 測試：repo 內任何 filter 檔皆不含 `registry.npmjs.org` 與 `upload.pypi.org`（防後人隨手加）

## 2. Install 護欄

- [x] 2.1 `bridge/runner.py:build_subprocess_env` 注入 `npm_config_ignore_scripts="true"`（字面值，勿用 `1`）與 `PIP_PREFER_BINARY=1`
- [x] 2.2 `bridge/runner.py:build_verify_env` 注入同兩值——verify 常態跑 `pip install -e . && pytest`，是主要路徑非補洞
- [x] 2.3 確認兩變數不被 `config._SUBPROCESS_ENV_DENY` 濾掉
- [x] 2.4 executor `Dockerfile` 加全域 npmrc 與 pip.conf，owner 為 root（executor 以 uid 1000 跑故不可寫）
- [x] 2.5 排掉 cache 目錄不可寫問題：建立可寫 HOME／cache 目錄或注入 `PIP_CACHE_DIR`、`npm_config_cache`（事前處理，不留給 live smoke）
- [x] 2.6 測試：兩條路徑的護欄值存在（含 opt-in 關閉時）；mutation-verify——拿掉注入則測試必須轉紅
- [x] 2.7 測試：worktree 內 `.npmrc` 設 `ignore-scripts=false` 時 env 值仍勝出；全域設定檔非 uid 1000 可寫

## 3. 誤建防呆與 canary

- [x] 3.1 frontend canary 的 forbidden hosts 加入 pypi 兩 host（誤把 EXTRA_FILTER 套到 proxy-discord 會 fail-closed，而非安靜通過）
- [x] 3.2 確認 executor canary 邏輯不變：probe 1/2 的 default-deny 證明照跑照 fail-closed；**不**新增 index 可達性門
- [x] 3.3 測試：frontend 在 pypi 可達時拒絕服務；executor 在 opt-in 開啟下仍要求 control host 直連失敗＋經 proxy 403

## 4. 供應鏈逃逸標示

- [x] 4.1 `bridge/worktree.py` 的 diff gate：偵測 `requirements*.txt`／`pyproject.toml`／`package*.json`／lockfile 變更並在審查訊息顯著標示
- [x] 4.2 測試：含依賴變更的 job diff 會帶出標示；不含者不誤報

## 5. 文件

- [x] 5.1 `SECURITY.md` 既有 publish-capable host 段落（約 204-207 行）**改寫**為精確版：讀寫端點分離者可 opt-in 開讀（pypi.org／files.pythonhosted.org），端點合一者（registry.npmjs.org）維持排除——精確化原則而非推翻
- [x] 5.2 `SECURITY.md` 新增 opt-in 段落：預設關、啟用程序、僅限 split 部署 executor 側、護欄涵蓋範圍
- [x] 5.3 `SECURITY.md` 殘餘風險清單逐項列出：無 wheel 時 sdist build／`.pth` 於解譯器啟動執行／agent 主動繞過（CLI flag、`env -u`、`PIP_CONFIG_FILE=/dev/null`、uv・pnpm・yarn Berry）／同 CDN 租戶的 SNI 繞過（新增 Fastly 池）／下載統計側信道／commit 後在 host・CI 執行不受護欄約束
- [x] 5.4 `SECURITY.md` roadmap：無憑證 build 容器為最終形，且是啟用 npm 的前置條件
- [x] 5.5 `SECURITY.zh.md` 同步 5.1–5.4 全部內容（逐項對應，非摘要）
- [x] 5.6 核對 `README.md:20` 與 `README.zh.md:40` 的「no publish-capable host／無可 publish 的 host」——經本 change 後仍為真，加註 opt-in 的唯讀 index 例外
- [x] 5.7 `SPEC.md`：§3.1 egress 敘述、檔案樹的 filter 清單、以及改 filter 需 `--build` 的說明，三處同步；指向 openspec 為單一來源
- [x] 5.8 `README.md` 測試數字與實跑值對齊（現寫 230，實際待核）
- [x] 5.9 Node 場景文件：說明 vendored node_modules 的操作方式，及 npm 自動安裝的解鎖條件
- [ ] 5.10 對齊 PR #22（auto-mode proposal）的「mirror」註記，改為指向本 change 結論

## 6. 收尾

- [ ] 6.1 執行完整 pytest 套件（現有基準不得退步，以實跑數字為準）
- [ ] 6.2 派 `reviewer` 與 `security-reviewer` 複審（前一輪 H1 已重新設計，本輪重點在新論證是否成立、防呆是否真的 fail-closed）
- [ ] 6.3 依 finding 修正並重跑測試
- [ ] 6.4 開 PR（branch 先開再 commit，不直推 main，由 operator merge）
- [ ] 6.5 live smoke：opt-in 開啟後跑一次真實 `pip install`，確認 sdist 未被優先選用、cache 可寫；並確認 frontend 誤建態真的被 canary 擋下（unit 綠 ≠ 可部署）
- [ ] 6.6 archive 後同步更新 `~/.claude-shared/memory/project_plan.md` 的 meta 行與狀態
