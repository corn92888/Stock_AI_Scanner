# 📈 台股 AI 技術分析選股系統 (Stock AI Scanner)

這是一個基於 Python 開發的台股技術分析選股工具，可自動掃描台股市場，挑選出「順勢突破」、「逆勢抄底」與「波段蓄勢」的潛力標的，並透過 Telegram 自動發送每日選股報表。

## 🌟 核心功能

* **🤖 雙核心掃描引擎**：提供「盤中即時動能狙擊 (Intraday)」與「盤後嚴格條件篩選 (EOD)」兩套各自獨立的選股系統。
* **📊 產出選股日報 (Excel)**：依據三大策略自動分類，產出易於閱讀的 `.xlsx` 表格。
* **🗃️ 累積選股訊號 (SQLite)**：每次掃描會同步寫入 `data/stock_scanner.db`，保留日後回測所需的原始訊號。
* **📱 Telegram 自動推播**：執行完畢後自動將挑出的標的、全市場監控摘要或精簡盤中分析報告傳送至指定的 Telegram 群組或對話。
* **🖥 視覺化戰情室 (Web UI)**：提供基於 Streamlit 開發的網頁 Dashboard，可輸入股票代號即時驗證技術指標 (如 SuperTrend 三線、布林通道等)，也可輸入目前持股，結合最新市場監控與策略訊號做部位分析。
* **☁️ 雲端股票倉**：持股頁可接 Supabase/Postgres，讓每個使用者用自己的 Email/名稱 + 私密倉庫代碼開啟獨立股票倉，並寫入每日持股快照；也可設定超級管理員總覽所有股票倉，供之後績效追蹤與回測。
* **🧭 量化控制中心**：獨立的 Next.js 儀表板整合正式候選、回測證據、AI 資料管線與 GitHub Actions 維運狀態，線上版位於 [stock-ai-control.vercel.app](https://stock-ai-control.vercel.app)。
* **🧠 AI 影子決策閉環**：候選會轉成具 `known_at` 與資料血緣的時間點特徵，由擴展視窗 walk-forward 模型預測 T+3 成功率、超額報酬與回撤；Claude 另對正式候選的近期新聞抽取催化劑與風險。AI 先平行觀察，不直接改寫正式規則名單。
* **🌐 全球市場情報層**：以點時資料整合美股期貨、費半、VIX、韓股、匯率、利率與原物料，並在 Next.js 儀表板呈現來源品質、資料延遲與台股風險傳導；完整規格見 [全球市場情報文件](docs/global_market_intelligence.md)。
* **🔐 可驗證雲端證據層**：排程會把一致性 SQLite 快照保存到私有 Supabase Storage，下載後比對壓縮檔與資料庫 SHA-256，再把 manifest 與同步狀態顯示在控制中心；目前採雲端與 Git 雙寫，切換條件見 [Cloud Evidence Store v1](docs/cloud_evidence_store.md)。

## 🎯 內建三大策略

1. **🚀 順勢突破 (Trend)**
   * SuperTrend 三線多頭 + 突破 20MA + 攻擊量 + RSI > 50。
2. **↩️ 逆勢抄底 / 低檔爆量 (Reversal)**
   * 位階於近 60 日的底部 35% + 跌破支撐後站回的「破底翻」型態。
3. **🌊 波段蓄勢 (Wave / VCP)**
   * 長線多頭排列 + 20 日波動率小於 15% (VCP 壓縮) + 均量急縮。

## 📦 安裝與設定

1. **安裝依賴套件:**
   請確保已安裝 Python 3.10+，並執行以下指令安裝所需套件：
   ```bash
   pip install -r requirements.txt
   ```

2. **設定環境變數 (.env):**
   本專案採用環境變數保護敏感資訊 (如 API Key)。
   請在專案根目錄將 `.env.example` 複製一份並改名為 `.env`，接著在裡面填入你的 Telegram 機器人資訊：
   ```env
   TELEGRAM_BOT_TOKEN=123456789:ABCDefg...
   TELEGRAM_CHAT_ID=@your_channel_or_chat_id
   ANTHROPIC_API_KEY=your_anthropic_api_key
   ANTHROPIC_MODEL=claude-sonnet-4-6
   ```

3. **設定雲端股票倉 (Supabase，可選但建議):**
   - 到 Supabase 建立專案。
   - 打開 Supabase SQL Editor，執行專案內的 `supabase_schema.sql`。
   - 在本機建立 `.streamlit/secrets.toml`，或在 Streamlit Cloud 的 Secrets 加入：
   ```toml
   [supabase]
   url = "https://your-project.supabase.co"
   service_role_key = "your-service-role-key"
   ```
   若尚未設定 Supabase，Portfolio 頁會自動退回本機 `data/portfolio_holdings.db`，重新整理本機頁面仍可載回，但無法跨主機多人共用。

4. **設定超級管理員 (可選):**
   管理員憑證只放在 `.streamlit/secrets.toml` 或 Streamlit Cloud Secrets，不要提交到 GitHub。設定後 Portfolio 頁會出現「超級管理員總覽」，可查看所有使用者儲存的股票倉。
   ```toml
   [admin]
   emails = "your-admin@example.com"
   access_code_sha256 = "your-sha256-hash"
   ```
   建議使用 hash 版本保存管理員代碼：
   ```bash
   python3 - <<'PY'
   import getpass, hashlib
   code = getpass.getpass("Admin code: ")
   print(hashlib.sha256(code.encode("utf-8")).hexdigest())
   PY
   ```
   若只是本機測試，也可用 `access_code = "your-admin-code"`；正式部署建議改成 `access_code_sha256`。如果管理員 email 同時也是 OIDC 登入 email，系統會自動開啟管理員模式；未設定登入時則用管理員 Email + 管理員代碼開啟。

5. **設定登入 (公開部署建議):**
   Streamlit 支援 OIDC 登入。設定完成後，Portfolio 頁會自動用登入者 email 區分股票倉。
   ```toml
   [auth]
   redirect_uri = "http://localhost:8501/oauth2callback"
   cookie_secret = "replace-with-a-random-secret"
   client_id = "your-oauth-client-id"
   client_secret = "your-oauth-client-secret"
   server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
   ```
   未設定登入時，也可以先用「Email / 股票倉名稱」加「自訂私密倉庫代碼」手動區分不同股票倉；第一次使用時自己設定這組代碼，之後用同一組資料會開啟同一個倉庫，不同組合則是不同倉庫。

## 🚀 如何使用

* **執行盤中狙擊掃描 (限平日 `09:00~13:30`):**
  ```bash
  venv/bin/python3 intraday_scanner.py
  ```
  *(透過 twstock 抓取即時報價，自動等比例換算為當日「預估總量」，即時預測突破名單並傳送 Telegram)*

* **執行盤後選股總結 (高防禦版):**
  ```bash
  venv/bin/python3 scanner.py
  ```
  *(每天下午 14:00 收盤結算後使用，產生包含所有技術指標穩定的最終選股日報與 Excel，並同步寫入 SQLite 訊號資料庫)*

* **回測已累積的選股訊號:**
  ```bash
  venv/bin/python3 backtest.py
  ```
  全候選反事實回測會把正式入選與落選候選分開保存，避免模型只看入選者而產生選擇偏誤：
  ```bash
  venv/bin/python3 candidate_backtest.py --limit 400
  ```
  目前候選執行版本為 `mode_aligned_after_costs_t3_v2`：盤中候選使用當時保存的訊號價，盤後候選使用下一交易日開盤價；超過禁止追價線不成交，盤後若跳空跌破觀察價也不假設成交。進場後三個交易日內若收盤跌破觀察價則退出，否則於 T+3 收盤退出。舊版 `next_day_open_defense_close_t3_v1` 保留作為凍結基準，不覆寫歷史結果。

  模擬資金帳戶會讓正式規則與真正前瞻的 AI 影子入選使用相同資金限制競賽：
  ```bash
  venv/bin/python3 paper_trading.py
  ```
  預設各有新台幣 100 萬元、單筆風險預算 1%、每檔市值上限 20%、最多同時持有 5 檔、保留 5% 現金，並執行禁止追價、交易成本及 T+3／防守價退出。規則帳戶標記為歷史點時訊號重播，AI 帳戶只接受當時真正產生的前瞻預測，不會把事後回算結果偽裝成模擬實績。

  每日盤後會把成熟結果轉成版本化研究週期、失敗切片、漏選機會與下一輪 challenger 假設：
  ```bash
  venv/bin/python3 learning_cycle.py
  ```
  報告寫入 `Reports/research_cycles/` 並同步顯示在儀表板「AI 與資料」。假設預設只會登記為 `proposed`；只有版本控制內 `config/shadow_challenger_approvals.json` 明確核准的項目，才會轉成可稽核的 shadow 實驗，仍不能自動改動正式規則、資金權重或下單設定。完整契約見 [`docs/automated_learning_cycle.md`](docs/automated_learning_cycle.md)。

  每日流程會先擷取 TWSE／TPEx 官方估值、月營收與季度 EPS，保存來源雜湊、發布時間及系統首次得知時間：
  ```bash
  venv/bin/python3 fundamental_ingestion.py --fail-on-empty
  venv/bin/python3 challenger_factory.py
  ```
  第一個版本化實驗為 `point_in_time_fundamentals_v1`。它只比較同一批樣本與相同 walk-forward 分折下的基礎模型和基本面增強模型；至少累積 300 筆成熟樣本、30 個獨立交易日與 60% 完整時間點覆蓋（估值、月營收、EPS 均具備）前，狀態固定為 `collecting_data`。完整資料契約見 [`docs/point_in_time_fundamentals.md`](docs/point_in_time_fundamentals.md)。

  自 `2026-07-20` 起，盤後流程另以同一批不可變的前瞻 EOD 預測建立 Top 3 等權、Top 5 分散與 Top 10 分數加權三個獨立資金帳戶。競賽不回填舊結果；挑戰者至少需要 120 個新決策日、100 筆結案、正的成本後報酬與大盤超額，並同時勝過 Top 3 的報酬及回撤效率，才會進入人工審查，永不自動接管正式策略。完整凍結契約見 [`docs/prospective_capital_tournament.md`](docs/prospective_capital_tournament.md)。

  評估所有已登錄策略實驗：
  ```bash
  venv/bin/python3 research_evaluation.py
  ```
  策略競賽以成本後超額報酬、Probabilistic Sharpe、最大回撤及時間順序分折穩定度作為升級門檻，結果會出現在 Next.js 儀表板「AI 與資料」頁。完整契約見 [`docs/research_execution_v2.md`](docs/research_execution_v2.md)。

  新版橫斷面研究不再只學習舊規則的 T+3 成功標籤。`execution_research.py` 會對每個歷史 EOD 候選比較隔日開盤、隔日 OHLC4 執行代理、隔日收盤與三日內回檔 2% 限價四種進場方式，分別保存 T+1/T+3/T+5/T+10/T+20 成本後報酬、大盤超額報酬與持有期回撤。`cross_sectional_research.py` 保留預測成本後報酬與直接預測大盤超額報酬的 40 組凍結基準，另以 20 組預先登記的同儕相對實驗學習同日排名：同產業至少三檔時先扣除產業中位數，否則扣除當日候選中位數，再把殘差轉為 `-1～1` 的同日排序目標；模型輸入同時包含原始特徵與同日百分位。棄權模型只使用各階段訓練資料中最後 20% 作時間校準，先隔離至少持有期長度，再鎖定 Q80 且不得低於零的門檻。未達門檻的日期維持現金，空手日也會納入 PSR、回撤及分折穩定度；第三族群依累積 60 次研究採用約 99.9167% PSR 門檻。所有結果維持 shadow，不會自動改寫正式選股。

  建立不污染正式訊號表的歷史逐日重播證據：
  ```bash
  venv/bin/python3 historical_universe.py --start 2022-01-01 --end 2025-12-31 --output data/universe_history.csv
  venv/bin/python3 historical_replay.py --start 2022-01-01 --end 2025-12-31 --universe-file data/universe_history.csv --resume
  venv/bin/python3 replay_attribution.py
  ```
  重播會逐日限制技術指標與市場廣度只能看到決策日以前的資料，盤後訊號採下一交易日開盤成交，並保存禁止追價、成本、T+3 超額報酬與最大回撤。v2 支援 TWSE／TPEx 官方上市區間、轉板與終止交易資料、SHA-256 完整性檢查、Yahoo 行情快取、每月檢查點與 `--resume`；`replay_attribution.py` 會產生策略、分數、量比、成交值、防守距離、市場／產業廣度及年度切片的 T+1/T+3/T+5、超額、回撤與 95% 信賴區間。可用 `--codes 2330,2454` 做小範圍驗證，或到 GitHub Actions 手動執行 `Historical Point-in-Time Replay`；未指定自訂股票池時，Action 會優先恢復已封存的官方 point-in-time universe，缺少時才重建。官方目前名單與預定終止名單若描述同一掛牌區間會留下可稽核的合併紀錄；歷史掛牌日不足的區間則標記為 `partial`，不會用推測日期偽裝成完整資料。完整事件庫會壓縮保存於 `research-replay-data-v1` GitHub Release，主資料庫只合併摘要與因子歸因；失敗工作只保留 30 天診斷 artifact，不能覆蓋 Release、主庫或儀表板。版本化的 `data/replay_training_samples.csv.gz` 可供 AI shadow challenger 使用，但重播資料仍不會偽裝成即時前瞻預測或模擬交易。AI 必須通過樣本外報酬、超額報酬、回撤與跨期穩定性門檻才可能進入升級審查。

  檢查前瞻預測是否依交易日正確成熟，以及歷史重播資料是否可用：
  ```bash
  venv/bin/python3 research_monitor.py
  ```

  `research_evaluation.py` 會額外對五年正式入選樣本執行固定風險覆蓋層競賽，分別檢驗市場廣度、產業廣度、量能放大、平衡量能、漲幅延伸、廣度共識與綜合品質。資料依時間切成 60% 開發、20% 驗證、20% 最終 holdout，兩個邊界各排除三個交易日；儀表板只顯示最終 holdout 指標，而且成本後淨報酬、超額報酬、樣本數、回撤、PSR、跨折穩定度及前兩階段必須全部通過，才可能進入人工升級審查。
  每次盤中與盤後自動化都會執行這項檢查，再把 cohort 數量、理應成熟 T+3、實際成熟、逾期標註與歷史重播覆蓋輸出到控制中心。完整資料契約與限制見 [`docs/historical_replay.md`](docs/historical_replay.md)。

  每日盤後也會執行 `candidate_execution_research.py`，只追蹤與歷史回放同分布的 EOD 候選，將四種執行情境從 `pending`、`partial` 持續補到 T+20 `complete`。這批資料是未來 prospective 驗證資料，不會回填成歷史訓練績效。

  回測會計算 1 / 3 / 5 / 10 / 20 個交易日後的毛報酬、成本後報酬、大盤報酬與超額報酬，並保存 3 / 20 日最大漲幅、最大回撤及防守價觸發狀態。預設採標準手續費、交易稅與每邊 0.1% 滑價，合計保守成本約 78.5 bps。

  舊盤中訊號缺乏歷史分鐘線，因此會明確標記為 `legacy_next_day_open_intraday` 並使用隔日開盤回測；盤後訊號標記為 `next_day_open_eod`。結果會由 `partial` 持續更新到 20 個交易日資料成熟後的 `complete`，不會因先寫入 T+1 就停止補齊後續資料。

  目前主要成功標籤 `success_t3` 定義為：扣除交易成本後，T+3 相對加權指數超額報酬至少 2%，而且三日內最大回撤不低於 -4%。

  查看已完成回測統計：
  ```bash
  venv/bin/python3 backtest.py --summary
  ```

  也可以只回測特定模式或策略：
  ```bash
  venv/bin/python3 backtest.py --mode eod --strategy trend --limit 20
  ```

  正式策略驗證與研究訊號分開執行：
  ```bash
  # 優先驗證系統實際推薦過的正式入選
  venv/bin/python3 backtest.py --selection-scope formal --limit 200

  # 再補首次合格候選與其餘未正式入選的研究訊號
  venv/bin/python3 backtest.py --selection-scope nonformal --limit 200
  ```
  `formal` 是判斷目前選股政策是否有效的主要樣本；`nonformal` 只用於研究各項技術訊號，不應與正式勝率混在一起。每日自動化會先更新正式入選，再補 200 筆非正式研究訊號。

  強制重算已完成結果，或調整成本與成功門檻：
  ```bash
  venv/bin/python3 backtest.py --refresh --limit 20
  venv/bin/python3 backtest.py --buy-fee-rate 0.001425 --sell-fee-rate 0.001425 \
    --sell-tax-rate 0.003 --slippage-rate 0.001 \
    --success-excess-return 2.0 --success-max-drawdown -4.0
  ```

  執行離線測試：
  ```bash
  venv/bin/python3 -m unittest discover -s tests -v
  ```

* **全市場即時盯盤監控:**
  ```bash
  venv/bin/python3 market_monitor.py
  ```
  會抓取全台股即時報價與近期歷史量價，輸出 `Reports/市場監控_*.xlsx`，包含市場總覽、產業熱度、資金焦點、漲跌幅排行、成交值排行、量能異常與全市場明細。

  若要同步推送 Telegram 全市場摘要：
  ```bash
  venv/bin/python3 market_monitor.py --send-telegram
  ```

* **產生盤中精簡分析報告並同步 Telegram:**
  ```bash
  venv/bin/python3 intraday_analysis_report.py --run-scanner --run-market-monitor --run-ai --send-telegram
  ```
  這個流程會依序執行盤中掃描、同步發送原本三策略標的清單、全市場監控，接著產出不含個人持股提醒的續漲分析報告與 AI 影子研究，並同步發送到 Telegram。分析目標改以 T+1/T+3 續漲為主，買進當天不做賣出判斷，防守價在報告中會轉為隔日收盤觀察價。若只想把最新已存在的報表整理成訊息：
  ```bash
  venv/bin/python3 intraday_analysis_report.py --send-telegram
  ```

  交叉排名會另外套用版本化的正式入選政策：成交值至少 5 億、排除接近漲跌停/爆量過熱/日內回落與防守距離過遠，綜合分數至少 50 分；每日最多正式入選 3 檔且同產業最多 1 檔。同股當日只會在第一次合格時被考慮，後續盤中批次仍保存研究快照但不重複模擬開倉。既有報表可依時間順序回填：
  ```bash
  venv/bin/python3 backfill_candidate_events.py
  ```

* **單獨執行 AI 影子管線:**
  ```bash
  venv/bin/python3 ai_pipeline.py
  ```
  這會補齊 `feature_snapshots`、同步成熟結果到 `prediction_outcomes`、重新訓練版本化模型，並對最新候選產生不可覆寫的影子預測。新聞 AI 只分析正式入選；每則證據同時保存發布時間與系統得知時間，決策後取得的新聞不會回填污染既有特徵。未設定 `ANTHROPIC_API_KEY` 時仍會完成量化模型，不會阻斷掃描。

  若只更新量化模型、不呼叫新聞 API：
  ```bash
  venv/bin/python3 ai_pipeline.py --no-news --no-predict
  ```
  AI 目前不改變 `tradability_v1` 正式名單。控制中心會顯示擴展視窗 OOF 指標，並在相同候選、日期與成本口徑下比較 AI 挑戰者與規則冠軍。只有成本後淨報酬、超額報酬、回撤與跨折穩定性全部通過，才會進入人工升級審查。完整治理契約見 [`docs/model_governance_v2.md`](docs/model_governance_v2.md)。

* **測試 Telegram 連線：**
  快速確認 Token 與 Chat ID 是否設定正確，直接發送一則推播：
  ```bash
  venv/bin/python3 test_telegram.py
  ```

* **設定免開機雲端自動化 (Vercel Cron + GitHub Actions)：**
  專案內建兩套由 Vercel 觸發的 GitHub Actions worker：
  - `.github/workflows/intraday_scan.yml`: 台北時間 `09:00-13:30` 每 30 分鐘執行，同時發送三策略標的清單與精簡盤中分析報告到 Telegram。
  - `.github/workflows/daily_scan.yml`: 平日 `14:17` 結算每日盤後高防禦名單。
  只要將程式碼推送至 GitHub，並在專案的（Settings > Secrets and variables > Actions）中新增 `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID` 與 `ANTHROPIC_API_KEY`，就能達成全自動監控與新聞 AI；`ANTHROPIC_MODEL` 可選擇放在 Actions Variables，未設定時使用程式預設值。

  Vercel Cron 會在台灣交易日 09:00 至 13:30 每 30 分鐘呼叫受保護的站內 API，並在 14:17 觸發盤後工作。API 只負責 dispatch；GitHub Actions 作為 Python worker 執行掃描。`dual_write` 驗證期會同時上傳經雜湊與還原驗證的 Supabase 快照，並把 `data/stock_scanner.db` commit 回 `main`；只有 Cloud Primary 稽核全部通過並切換模式後，才停止提交 SQLite。GitHub workflow 本身不再使用不穩定的 `schedule` 事件。

  所有會改寫 SQLite 的工作共用 concurrency，避免不同 worker 互相覆蓋資料。獨立全球市場排程會避開台股 `09:00-14:17`，因每次盤中掃描與盤後工作本身已經刷新同一份市場資料，避免重複任務占用下一個半小時時段。非交易時段會安全略過；開盤報價不足時最多重試 3 次且只重抓缺漏股票，不會重載歷史行情。最終當日報價或全市場覆蓋率仍低於 65%、或盤中日報與市場快照日期/時間不一致時，流程會拒絕產生分析。失敗仍會上傳已取得的 artifacts，並透過 Telegram 發送維運警報。

  每日盤後流程也會增量更新最多 200 筆成熟回測結果。行情來源暫時失敗時不會阻斷當日掃描資料保存，未完成的 `partial` 結果會在後續交易日繼續補齊。

  每次盤中或盤後掃描完成後，流程也會執行 `export_dashboard_snapshot.py`，將不含持股、Email 或私密憑證的公開快照同步更新到 `data/dashboard_snapshot.json` 與 Next.js 儀表板。

  雲端證據層的設定、還原演練、保留政策與安全切換流程見 [`docs/cloud_evidence_store.md`](docs/cloud_evidence_store.md)。操作中心會分別顯示最近同步狀態與 Cloud Primary 切換資格；只有顯示 `READY` 才能移除 Git 資料庫備援。

  控制中心程式碼由 `.github/workflows/vercel_deploy.yml` 部署。`web` 程式碼或 Vercel cron 設定推上 `main` 後會自動建立並發布 production；單純更新 `web/public/dashboard_snapshot.json` 不會觸發昂貴的重建，因 production 會每分鐘直接讀取 GitHub 上最新的 `data/dashboard_snapshot.json`。

* **開啟量化控制中心 (Next.js)：**
  ```bash
  cd web
  npm install
  npm run dev
  ```
  本機預設網址為 `http://localhost:3000`。線上正式版為 [https://stock-ai-control.vercel.app](https://stock-ai-control.vercel.app)，包含決策總覽、回測績效、資料管線與操作中心。操作中心的盤中按鈕會透過站內 API 直接觸發 GitHub Actions，並在原頁追蹤執行狀態；不會把 GitHub Token 傳到瀏覽器。

  若要啟用站內盤中按鈕，請在 Vercel 專案的 Production Environment Variables 設定：
  - `GITHUB_ACTIONS_TOKEN`：只授權此 repository，且具備 GitHub Actions 讀寫權限的 fine-grained token。
  - `SCAN_TRIGGER_SECRET`：自訂的掃描控制碼。瀏覽器第一次執行時輸入，僅保留在該分頁的 session storage。
  - `CRON_SECRET`：至少 16 字元的隨機值。Vercel 會自動以 `Authorization: Bearer ...` 傳給 Cron API，未通過驗證的請求一律拒絕。

  本機開發可參考 `web/.env.example`。未設定相關伺服器端變數時，儀表板仍可讀取資料與工作流狀態，但會拒絕站內觸發掃描。

* **使用固定 T+10 執行策略：**
  每日盤後會將全市場 Alpha 評分轉成單一明確動作：次日開盤買進 1 檔或維持現金。固定規則為市場上漲家數至少 50%、模型信心高於訓練期第 70 百分位、單檔 8%、開盤跳空不得超過 3%、T+10 收盤退出。決策工作台會直接顯示標的、最高接受價、部位與阻擋原因；券商自動送單維持關閉，紙上帳戶同步使用完全相同規則。完整歷史證據、限制與重跑方式見 [`docs/deployable_strategy.md`](docs/deployable_strategy.md)。

* **開啟戰情室 (Dashboard)：**
  ```bash
  streamlit run app.py
  ```
  啟動後可透過瀏覽器 (預設: `http://localhost:8501`) 查詢欲分析的股票技術線圖與指標狀態。
  Dashboard 目前包含四個主要頁面：
  - 歷史報表預覽：檢視每日/盤中掃描產出的 Excel。
  - 個股高階圖表分析：輸入代號查看策略診斷與技術圖。
  - 持股可視化分析：每個使用者可開啟自己的股票倉，輸入代號、成本、股數、停損/目標價，系統會自動帶入股票名稱，結合最新市場監控與策略訊號檢查損益、風險、續抱分數與 AI 分析摘要；成本可輸入到小數點後三位，並可儲存到 Supabase 雲端股票倉與每日持股快照。若設定 `[admin]` Secrets，超級管理員可在同頁查看所有股票倉。
  - 精選動態新聞：依最新選股名單快速抓取近期新聞。

## ⚠️ 免責聲明
本專案的程式碼、選股邏輯與分析結果僅供學習與研究技術指標參考，**不構成任何投資建議**。投資必定伴隨風險，買賣前應自行謹慎評估。
