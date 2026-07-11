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

## 🎯 內建三大策略

1. **🚀 順勢突破 (Trend)**
   * SuperTrend 三線多頭 + 突破 20MA + 攻擊量 + RSI > 50。
2. **↩️ 逆勢抄底 / 低檔爆量 (Reversal)**
   * 位階於近 60 日的底部 35% + 跌破支撐後站回的「破底翻」型態。
3. **🌊 波段蓄勢 (Wave / VCP)**
   * 長線多頭排列 + 20 日波動率小於 15% (VCP 壓縮) + 均量急縮。

## 📦 安裝與設定

1. **安裝依賴套件:**
   請確保已安裝 Python 3.8+，並執行以下指令安裝所需套件：
   ```bash
   pip install yfinance pandas numpy twstock tqdm requests python-dotenv streamlit supabase plotly openpyxl
   ```

2. **設定環境變數 (.env):**
   本專案採用環境變數保護敏感資訊 (如 API Key)。
   請在專案根目錄將 `.env.example` 複製一份並改名為 `.env`，接著在裡面填入你的 Telegram 機器人資訊：
   ```env
   TELEGRAM_BOT_TOKEN=123456789:ABCDefg...
   TELEGRAM_CHAT_ID=@your_channel_or_chat_id
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
  venv/bin/python3 intraday_analysis_report.py --run-scanner --run-market-monitor --send-telegram
  ```
  這個流程會依序執行盤中掃描、同步發送原本三策略標的清單、全市場監控，接著產出不含個人持股提醒的續漲分析報告，並同步發送到 Telegram。分析目標改以 T+1/T+3 續漲為主，買進當天不做賣出判斷，防守價在報告中會轉為隔日收盤觀察價。若只想把最新已存在的報表整理成訊息：
  ```bash
  venv/bin/python3 intraday_analysis_report.py --send-telegram
  ```

  交叉排名會另外套用版本化的正式入選政策：成交值至少 5 億、排除接近漲跌停/爆量過熱/日內回落與防守距離過遠，綜合分數至少 50 分；每日最多正式入選 3 檔且同產業最多 1 檔。同股當日只會在第一次合格時被考慮，後續盤中批次仍保存研究快照但不重複模擬開倉。既有報表可依時間順序回填：
  ```bash
  venv/bin/python3 backfill_candidate_events.py
  ```

* **測試 Telegram 連線：**
  快速確認 Token 與 Chat ID 是否設定正確，直接發送一則推播：
  ```bash
  venv/bin/python3 test_telegram.py
  ```

* **設定免開機雲端自動化 (GitHub Actions)：**
  專案內建兩套 GitHub Actions 自動排程：
  - `.github/workflows/intraday_scan.yml`: 每 30 分鐘探測一次，但只會在台北時間早盤 `09:35-10:35`、午盤 `11:10-12:10`、尾盤 `12:40-13:20` 各執行一次，降低 GitHub cron 延遲造成整天漏跑的機率；同時發送三策略標的清單與精簡盤中分析報告到 Telegram。
  - `.github/workflows/daily_scan.yml`: 平日 `14:00` 結算每日盤後高防禦名單。
  只要將程式碼推送至 GitHub，並在專案的（Settings > Secrets and variables > Actions）中新增 `TELEGRAM_BOT_TOKEN` 與 `TELEGRAM_CHAT_ID`，就能達成全自動監控！

  GitHub Actions 每次掃描後會把 `data/stock_scanner.db` commit 回 `main`，讓歷史選股訊號能跨排程持續累積，日後可直接用 `backtest.py` 驗證策略表現。

  盤中與盤後工作共用 concurrency，不會同時改寫 SQLite。非交易時段會安全略過；當日報價或全市場覆蓋率低於 65%、或盤中日報與市場快照日期/時間不一致時，流程會拒絕產生分析。失敗仍會上傳已取得的 artifacts，並透過 Telegram 發送維運警報。

  每日盤後流程也會增量更新最多 200 筆成熟回測結果。行情來源暫時失敗時不會阻斷當日掃描資料保存，未完成的 `partial` 結果會在後續交易日繼續補齊。

  每次盤中或盤後掃描完成後，流程也會執行 `export_dashboard_snapshot.py`，將不含持股、Email 或私密憑證的公開快照同步更新到 `data/dashboard_snapshot.json` 與 Next.js 儀表板。

* **開啟量化控制中心 (Next.js)：**
  ```bash
  cd web
  npm install
  npm run dev
  ```
  本機預設網址為 `http://localhost:3000`。線上正式版為 [https://stock-ai-control.vercel.app](https://stock-ai-control.vercel.app)，包含決策總覽、回測績效、資料管線與操作中心；操作中心目前以 GitHub Actions 登入權限保護手動執行，不會將管理員 Token 放在瀏覽器。

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
