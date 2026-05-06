# 📈 台股 AI 技術分析選股系統 (Stock AI Scanner)

這是一個基於 Python 開發的台股技術分析選股工具，可自動掃描台股市場，挑選出「順勢突破」、「逆勢抄底」與「波段蓄勢」的潛力標的，並透過 Telegram 自動發送每日選股報表。

## 🌟 核心功能

* **🤖 雙核心掃描引擎**：提供「盤中即時動能狙擊 (Intraday)」與「盤後嚴格條件篩選 (EOD)」兩套各自獨立的選股系統。
* **📊 產出選股日報 (Excel)**：依據三大策略自動分類，產出易於閱讀的 `.xlsx` 表格。
* **🗃️ 累積選股訊號 (SQLite)**：每次掃描會同步寫入 `data/stock_scanner.db`，保留日後回測所需的原始訊號。
* **📱 Telegram 自動推播**：執行完畢後自動將挑出的標的傳送至指定的 Telegram 群組或對話。
* **🖥 視覺化戰情室 (Web UI)**：提供基於 Streamlit 開發的網頁 Dashboard，可輸入股票代號即時驗證技術指標 (如 SuperTrend 三線、布林通道等)，也可輸入目前持股，結合最新市場監控與策略訊號做部位分析。
* **☁️ 雲端股票倉**：持股頁可接 Supabase/Postgres 儲存每個使用者的股票倉，並寫入每日持股快照，供之後績效追蹤與回測。

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

4. **設定登入 (公開部署建議):**
   Streamlit 支援 OIDC 登入。設定完成後，Portfolio 頁會自動用登入者 email 區分股票倉。
   ```toml
   [auth]
   redirect_uri = "http://localhost:8501/oauth2callback"
   cookie_secret = "replace-with-a-random-secret"
   client_id = "your-oauth-client-id"
   client_secret = "your-oauth-client-secret"
   server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
   ```
   未設定登入時，也可以先用「股票倉識別碼 / Email」手動區分不同股票倉。

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
  第一版回測會用「訊號隔一個交易日開盤價」作為進場價，計算 1 / 3 / 5 / 10 / 20 個交易日後報酬、20 日內最大漲幅、最大回撤，以及是否跌破防守價。

  查看已完成回測統計：
  ```bash
  venv/bin/python3 backtest.py --summary
  ```

  也可以只回測特定模式或策略：
  ```bash
  venv/bin/python3 backtest.py --mode eod --strategy trend --limit 20
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

* **測試 Telegram 連線：**
  快速確認 Token 與 Chat ID 是否設定正確，直接發送一則推播：
  ```bash
  venv/bin/python3 test_telegram.py
  ```

* **設定免開機雲端自動化 (GitHub Actions)：**
  專案內建兩套 GitHub Actions 自動排程：
  - `.github/workflows/intraday_scan.yml`: 平日 `10:00`, `11:30`, `13:00` 追蹤盤中名單。
  - `.github/workflows/daily_scan.yml`: 平日 `14:00` 結算每日盤後高防禦名單。
  只要將程式碼推送至 GitHub，並在專案的（Settings > Secrets and variables > Actions）中新增 `TELEGRAM_BOT_TOKEN` 與 `TELEGRAM_CHAT_ID`，就能達成全自動監控！

  GitHub Actions 每次掃描後會把 `data/stock_scanner.db` commit 回 `main`，讓歷史選股訊號能跨排程持續累積，日後可直接用 `backtest.py` 驗證策略表現。

* **開啟戰情室 (Dashboard)：**
  ```bash
  streamlit run app.py
  ```
  啟動後可透過瀏覽器 (預設: `http://localhost:8501`) 查詢欲分析的股票技術線圖與指標狀態。
  Dashboard 目前包含四個主要頁面：
  - 歷史報表預覽：檢視每日/盤中掃描產出的 Excel。
  - 個股高階圖表分析：輸入代號查看策略診斷與技術圖。
  - 持股可視化分析：輸入代號、成本、股數、停損/目標價，系統會自動帶入股票名稱，結合最新市場監控與策略訊號檢查損益、風險、續抱分數與 AI 分析摘要；成本可輸入到小數點後三位，並可儲存到 Supabase 雲端股票倉與每日持股快照。
  - 精選動態新聞：依最新選股名單快速抓取近期新聞。

## ⚠️ 免責聲明
本專案的程式碼、選股邏輯與分析結果僅供學習與研究技術指標參考，**不構成任何投資建議**。投資必定伴隨風險，買賣前應自行謹慎評估。
