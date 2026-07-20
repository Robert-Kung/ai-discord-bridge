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
  - **交給 #22 落地者處理**（2026-07-20 operator 拍板：不動他人審查中的 PR branch）。
    待改內容：`openspec/changes/2026-07-20-unattended-auto-mode/design.md` 的
    「needs a read-only registry mirror first」——本 change 結論是 mirror **不是**路徑，
    npm 的解鎖條件是無憑證 build 容器；Python 側已可經 EXTRA_FILTER opt-in。

## 6. 收尾

- [x] 6.1 執行完整 pytest 套件：**296 passed**（基準 275 → 296，含 docker-gated 的真實
  build／live proxy 測試，CI 的 ubuntu-latest 有 docker 故不會 skip）；README 兩語數字已同步
- [x] 6.2 派 `reviewer` 與 `security-reviewer` 複審（兩份皆完成 2026-07-20）。結論：
  核心論證（PyPI 讀寫分離 vs npm 端點合一）**經實測成立**——25 種 CONNECT 變形無繞過、
  `upload.pypi.org` 確認不可達且不在 Fastly 池、預設 build 逐位元組相同。
  兩份共同要求「修完 HIGH 再 merge」。
- [x] 6.3 依 finding 修正並重跑測試。已修：
  - **文件過度宣稱（兩份都列 HIGH／MED）**：`pypi.org` 是完整 Warehouse 網站應用，
    「read-only／no write path」不成立 → 改為精確的「搆不到套件上傳端點」，並新增殘留
    「攻擊者自有帳號的驗證後寫入」。兩語同步。
  - **verify import 執行第三方程式碼（reviewer H2）**：typosquat 套件在 `pytest` import
    時於持有憑證的容器內執行，完全不需注入 → 列為殘留清單第一項（曝險面最大改變）。
  - **diff gate 訊息可能超過 Discord 2000 字元（reviewer H1，實測會靜默吞掉整個審查門）**
    → header 改為單則預算制（`_HEADER_MAX`），diffstat 依剩餘空間裁切；加迴歸測試。
  - **審查訊息的 markdown 注入（security M4）**：檔名可含反引號閉合 code span → 改
    `escape_markdown` + 只顯示 basename；加 escape 測試。
  - **pip cache 跨 job 汙染（security H2）** → `PIP_NO_CACHE_DIR=1`。
  - **裸 `pip install` EACCES（reviewer M6，實測確認；先前 smoke 用 `--target` 蓋掉了）**
    → `PIP_REQUIRE_VIRTUALENV=1`（operator 拍板）：錯誤訊息可行動，且順帶關掉 user-site
    `.pth` 的持久化向量。README 兩語補 venv 工作流。
  - **`EXTRA_FILTER` 未驗證（兩份都列）**：`filter.anthropic` + `EXTRA_FILTER=filter.discord`
    原本 build 得過（＝把 Discord egress 給憑證容器）→ 兩個 arg 都改白名單 case，順帶
    擋掉 path traversal；加 build 失敗測試。
  - **executor 未證明 publish host 被拒（security M3）** → 加入 forbidden 探針
    （forbidden 探針由 proxy 本地回 403，不受 index 故障影響，不違反「不加可達性門」）。
  - **單容器未探 index host（reviewer M5）** → 手改 filter 的情境現在會被 canary 抓到。
  - **偵測漏 `requirements/*.txt`、quoted path、`.npmrc`、CI／Makefile 等（兩份都列）**
    → 擴充為「依賴宣告」＋「合併後會執行」兩類。
  - **測試品質（reviewer H3/H5、security M2）**：diff-gate 旗標原本只用 `inspect.getsource`
    斷言（反轉 `if deps:` 仍全綠）→ 改成用 `_FakeChannel` 實跑 `_post_diff_gate` 的行為
    測試，並 mutation-verify 通過；filter guard 的 `re.fullmatch` 改為 tinyproxy 的
    **無錨定** substring 語意，另加 meta-test 證明它抓得到掉錨變體；新增真實 proxy 的
    end-to-end deny 測試（空行若曾被當空 regex ＝ allow-all，檔案內容測試看不到）。
  - **`npm_config_cache` 寫死路徑（reviewer M6）** → 改由 HOME 推導。
  - **SNI 殘留描述不精確（reviewer L1）** → 實測收斂為 `python.map.fastly.net` 對應下的
    `www.python.org` / `test.pypi.org`，並記錄 tinyproxy 自解 DNS 這一有利面。
  - **SPEC.md 指向尚未存在／已過期的 `openspec/specs/`（reviewer M8）** → 改指 change 目錄
    並註明歸檔前後差異。
  - §8 加固清單兩語補上 opt-in 條目。
  未採納：`.pth`／下載側信道／agent 主動繞過等既有殘留維持「揭露而非宣稱關閉」。
- [ ] 6.4 開 PR（branch 先開再 commit，不直推 main，由 operator merge）——branch
  `feat/registry-egress-opt-in` 已開、已 commit，待 operator 確認後開 PR
- [ ] 6.5 live smoke：opt-in 開啟後跑一次真實 `pip install`，確認 sdist 未被優先選用、cache 可寫；並確認 frontend 誤建態真的被 canary 擋下（unit 綠 ≠ 可部署）
  - [x] 容器層已實測（2026-07-20，本機 docker，非 live 部署；修完 review finding 後重跑）：
    opt-in proxy build 後，executor image 以 uid 1000 經 proxy 在 **venv 內**
    `pip install requests==2.32.3` 成功（全部 wheel、無 sdist build）；裸 `pip install`
    如設計般以 `Could not find an activated virtualenv (required)` 拒絕（**不是** EACCES）；
    npm cache 可寫。CONNECT 探針：`pypi.org` / `files.pythonhosted.org` → 200，
    `upload.pypi.org` / `registry.npmjs.org` / `github.com` / `example.com` → 403 Filtered。
    另實測全域 npmrc（`/usr/etc/npmrc`）與 `/etc/pip.conf` 對 uid 1000 唯讀、`HOME` 不可寫
    （user-site `.pth` 持久化向量因此關閉）、專案內 `.npmrc` `ignore-scripts=false` 被 env
    蓋過。以上多數已固化為 docker-gated 測試，不只是一次性手測。
    ⚠️ 首次 smoke 用 `--target` 掩蓋了裸 `pip install` 的 EACCES（reviewer M6 抓到）——
    教訓：smoke 指令要用**文件教使用者的那一條**，不要用自己順手的變體。
  - [ ] 仍待 operator：真實 live compose 上的 frontend 誤建態 canary 擋下驗證
- [ ] 6.6 archive 後同步更新 `~/.claude-shared/memory/project_plan.md` 的 meta 行與狀態
