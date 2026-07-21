# registry-egress-opt-in — design

## Context

executor 的 egress 是 build-time 選定的單一 filter 檔（`egress-proxy/Dockerfile` 的 `ARG FILTER` → `/etc/tinyproxy/filter`），tinyproxy 做 CONNECT-level hostname 過濾（BRE、default-deny、僅 443、無 TLS bump）。claude 子行程的 env 由 `bridge/runner.py:build_subprocess_env` 每次 spawn 生成；verify 子行程走**另一條** `build_verify_env`。M4 教訓：agent 可寫任何 rw 掛載路徑，護欄設定不能放在 agent 可及的檔案。

初版設計（開放 npm+pypi、以「publish 需 token」論證可接受性）於 2026-07-20 兩路 review 被推翻，本版是重寫。查證後的關鍵事實：

- **npm**：`registry.npmjs.org` 同時是 metadata、tarball **與 publish** 端點。可達 ⇒ 可寫。
- **PyPI**：讀在 `pypi.org`（index/JSON API）與 `files.pythonhosted.org`（檔案），寫在 `upload.pypi.org`。兩者分離，故開讀不開寫。
- **設定優先序**：npm 是 CLI flag > `npm_config_*` env > 專案 `.npmrc` > user `~/.npmrc` > 全域 npmrc；pip 是 CLI > `PIP_*` env > 設定檔。env 注入蓋得過 agent 可寫的專案設定檔，但**蓋不過 agent 自己改 env 或下 CLI flag**。

## Goals / Non-Goals

**Goals:**
- Python 場景（含 greenfield）agent 能自己裝依賴
- 維持 SECURITY.md「executor egress 不含 publish-capable host」的不變量——本 change 精確化該原則而非破壞它
- 未被注入的 agent 在正常安裝流程中不會意外執行第三方 install-time 程式碼
- 常見誤建態（EXTRA_FILTER 套錯 proxy）被 canary fail-closed 抓到，而非安靜通過

**Non-Goals（誠實界定，spec 措辭須對齊）:**
- **不防主動繞過**：agent 下 CLI flag、`env -u` 移除變數、`PIP_CONFIG_FILE=/dev/null`、或改用 uv/pnpm/yarn（這些工具不讀 `PIP_*` / `npm_config_*`）——一個能執行任意 shell 的 agent 本來就做得到，護欄的目標對象是**honest mistake 與正常安裝流程**，真實邊界仍是 routeless egress
- **不防 CDN 共租的 SNI 繞過**：CONNECT allow-list 只比對 hostname，tunnel 內的 SNI 由 client 掌控；`pypi.org` / `files.pythonhosted.org` 在 Fastly，開放後該租戶池成為可達（`api.anthropic.com` 已使 Cloudflare 池可達，故此非全新類別但**是新增租戶池**）。只有 TLS-terminating / SNI-pinning proxy 或無憑證 build 容器能真正關掉
- 不做：開放 npm、docker-in-docker、Verdaccio mirror、無憑證 build 容器（另案）

## Decisions

1. **只開 PyPI 的兩個讀取 host** — `filter.pypi` 含 `^pypi\.org$`、`^files\.pythonhosted\.org$`。兩者皆必要（index 與檔案分離），無法再縮減。檔頭註記「刻意不含 `registry.npmjs.org`（publish 端點同 host）與 `github.com`/`codeload`（原始碼依賴不支援）」，讓後續加行的人知道邊界依據。
2. **opt-in ＝ proxy image 的 build arg，非 runtime 開關** — filter 維持 build 進 image、不可 runtime 變造（對齊 M4「config 要 :ro」教訓）。Dockerfile 無條件 `COPY filter.* /tmp/filters/`，再以 `RUN` 依 `EXTRA_FILTER` 是否為空條件拼接；驗收條件是「空值時 `/etc/tinyproxy/filter` 內容與 `filter.anthropic` 逐位元組相同」（**不是** image 逐位元組相同——那不可達成）。拼接前補 `printf '\n'` 避免依賴來源檔的結尾換行。
3. **護欄涵蓋兩條 spawn 路徑** — `build_subprocess_env` 與 `build_verify_env` 均注入 `PIP_PREFER_BINARY=1`、`npm_config_ignore_scripts=true`（字面值 `"true"`，勿改 `1`）。verify 路徑非補洞而是主戰場：verify 指令常態就是 `pip install -e . && pytest` 或 `npm ci && npm test`，且註解自承 agent-influenced。image 全域 pip.conf/npmrc 作 belt，但 spec 措辭須承認它可被 `PIP_CONFIG_FILE=/dev/null` 等繞過。
4. **誤建防呆用既有機制** — frontend canary 的 `forbidden_hosts` 加入 pypi hosts。executor 側誤設已被既有 `DISCORD_HOSTS` 探針覆蓋，frontend 側原本沒有對應項，補上後兩個方向對稱。另加 Dockerfile build-time 斷言：`FILTER=filter.discord` 且 `EXTRA_FILTER` 非空 → build 失敗。純文件警語不構成防呆。
5. **canary 不加 registry 可達性門** — registry 不通只讓 install 失敗、錯誤自然浮現；加進 canary 反而讓 PyPI 端故障拉掉整個 executor。
6. **`prefer-binary` 而非 `only-binary=:all:`** — 後者遇無 wheel 的套件直接裝不了，破壞可用性目標。誠實記載兩項殘餘：無 wheel 時 sdist build 照跑（`setup.py egg_info` 在解析期就可能執行）；**wheel 本身也不安全**——`.pth` 檔的 `import` 前綴行在每次 Python 啟動時執行，與 build 無關，`prefer-binary` 對它零作用。
7. **供應鏈逃逸要在 diff gate 可見** — 容器內護欄止於 commit；operator 在 host / CI 跑 `pip install` 時沒有護欄。diff gate 對 `requirements*.txt`、`pyproject.toml`、`package*.json`、lockfile 的變更做顯著標記，讓「依賴變了」不會淹沒在 diff 裡。並要求新增依賴須產生並提交 lockfile。
8. **單容器模式明確不支援 opt-in** — 單容器用合併的 `filter`（含 Discord hosts），在同一容器同時持有 Discord token 與 Claude 憑證。在該姿勢下疊加 registry 破壞「憑證×egress 反向配對」的前提，故 spec 明文禁止，文件寫明 opt-in 僅適用 split 部署的 executor 側。

## Risks / Trade-offs

- [agent 主動繞過護欄] → 歸 Non-Goal 並在 spec/SECURITY.md 具名（CLI flag、env 移除、uv/pnpm/yarn、`PIP_CONFIG_FILE`）。不佯稱關閉。
- [Fastly 租戶池經 SNI 可達] → 記入 SECURITY.md 殘餘；roadmap 註明只有無憑證 build 容器或 SNI-pinning proxy 能關。
- [wheel 的 `.pth` 執行向量] → 記入殘餘；「只裝 wheel ＝ 安全」是錯誤結論，文件不得暗示。
- [下載統計側信道] → 攻擊者可預先發佈套件、以選擇性下載外洩每日數十 bit。無實務緩解，僅列殘餘求清單完整。
- [Node 場景能力未解鎖] → 明示 vendored 路徑為現行做法，並把無憑證 build 容器登記為 npm 的解鎖條件。
- [cache 目錄不可寫] → executor 以 `user: "1000:1000"` 跑而 Dockerfile 未建可寫 HOME，`~/.cache/pip` 可能 EACCES。事前排掉（注入 `PIP_CACHE_DIR` 或建目錄），不要留給 live smoke 才發現。

## Migration Plan

1. 合併後預設行為零變化（EXTRA_FILTER 預設空）。
2. opt-in：更新本地 compose 的 proxy-anthropic args → `docker compose build proxy-anthropic && docker compose up -d`。
3. 回滾：移除 EXTRA_FILTER 重 build。

## Open Questions

- 無。（Node 自動安裝的去留已於 2026-07-20 拍板：本輪 vendored，解鎖條件為無憑證 build 容器。）
