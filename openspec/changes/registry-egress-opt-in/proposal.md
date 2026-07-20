# registry-egress-opt-in

## Why

exec loop 的 agent 裝不了依賴：既有專案靠 host 預裝的環境離線跑，greenfield 場景 agent 建得出專案骨架卻卡在第一個 `pip install`。本 change 讓 **Python 場景**的 agent 能自己裝依賴，Node 場景維持 vendored（operator 在 host 跑 `npm ci`、`node_modules` 隨專案掛入，agent 離線跑 `npm test`）。

**範圍限縮的理由（2026-07-20 review 推翻初版）**：初版提案主張「npm 外洩需 publish、而 publish 需要容器內不存在的 token」——這個論證錯了。本專案的主要威脅是間接 prompt injection，注入的 payload 可以**自帶**攻擊者的 npm token；且 npm 的 publish 端點就是 `registry.npmjs.org` 本身，proxy 是 CONNECT-only（無 TLS bump），「可達」的真正含義是「任意 method + 任意 body」。`SECURITY.md` 既有段落早已正確記載這一點。

**PyPI 與 npm 的結構性不對稱（本 change 的立足點）**：PyPI 的上傳端點是 `upload.pypi.org`，與讀取用的 `pypi.org` / `files.pythonhosted.org` 分屬不同 host。開放後兩者**不會**授予寫入路徑，SECURITY.md「不放 publish-capable host」的原則與 README 對外宣稱因此**維持成立**——本 change 是把該原則講得更精確，不是推翻它。npm 沒有這種分離，故 `registry.npmjs.org` 留在門外，Node 的自動安裝能力延後到無憑證 build 容器。

## What Changes

- **executor egress**：`pypi.org`、`files.pythonhosted.org` 放獨立 filter 檔，proxy build-arg opt-in，**預設關**。`registry.npmjs.org` 明確**不納入**，filter 檔頭記載原因避免後人隨手加。
- **install 護欄**：`build_subprocess_env` 與 `build_verify_env` 兩條 spawn 路徑均注入 `PIP_PREFER_BINARY=1`（及 npm 對應值，涵蓋 vendored 場景下的 `npm test`）；executor image 加全域 pip.conf / npmrc 作 belt。
- **誤建防呆**：frontend canary 的 forbidden hosts 加入 registry hosts（誤把 EXTRA_FILTER 套到 proxy-discord 會被 fail-closed 抓到，而非安靜通過）；Dockerfile 加 build-time 斷言拒絕 `FILTER=filter.discord` 與 `EXTRA_FILTER` 併用。
- **供應鏈逃逸標示**：diff gate 對 lockfile / manifest 變更做顯著標記——容器內的護欄止於 commit，operator 在 host 或 CI 跑安裝時沒有這道護欄。
- **文件**：SECURITY.md / zh 改寫既有 publish-capable host 段落（精確化而非推翻）、補齊殘餘風險清單；README / README.zh 的對外宣稱經核對維持為真但需加註 opt-in 的存在。
- **明確不做**：開放 `registry.npmjs.org`；docker-in-docker；Verdaccio mirror；無憑證 build 容器（roadmap，也是 Node 自動安裝的解鎖條件）。

## Capabilities

### New Capabilities
- `registry-install-guardrails`: opt-in registry egress 下的 install-time 程式碼執行防護——涵蓋範圍、強制通道，以及誠實界定的不防範圍。

### Modified Capabilities
- `egress-containment`: executor allow-list 可經 build-time opt-in 追加**唯讀** package index hosts（獨立 filter 檔、預設關、排除 publish-capable host）；canary 的 default-deny 證明不受影響，frontend canary 的 forbidden 集合擴充。

## Impact

- `egress-proxy/`：新增 `filter.pypi`；Dockerfile 的 EXTRA_FILTER 機制與 build-time 斷言
- `docker-compose.example.yml`：opt-in 註解（操作者本地 compose 為 gitignored，依 example 自行更新）
- `Dockerfile`（executor image）：全域 pip.conf / npmrc；cache 目錄可寫性
- `bridge/runner.py`：`build_subprocess_env` 與 `build_verify_env` 的護欄注入
- `bridge/egress.py` / `bot.py`：frontend canary forbidden hosts
- `bridge/worktree.py`：diff gate 的 lockfile/manifest 標示
- `SECURITY.md` / `SECURITY.zh.md` / `README.md` / `README.zh.md` / `SPEC.md`：doc-delta
- 測試：filter 組合、護欄兩路徑、canary 誤建態、diff 標示
