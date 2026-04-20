# 📈 台股 AI 技術分析選股系統 (Stock AI Scanner)

這是一個基於 Python 開發的台股技術分析選股工具，可自動掃描台股市場，挑選出「順勢突破」、「逆勢抄底」與「波段蓄勢」的潛力標的，並透過 Telegram 自動發送每日選股報表。

## 🌟 核心功能

* **🤖 自動化策略篩選**：每日自動跑遍台股，利用技術線型找出符合條件的股票。
* **📊 產出選股日報 (Excel)**：依據三大策略自動分類，產出易於閱讀的 `.xlsx` 表格。
* **📱 Telegram 自動推播**：執行完畢後自動將挑出的標的傳送至指定的 Telegram 群組或對話。
* **🖥 視覺化戰情室 (Web UI)**：提供基於 Streamlit 開發的網頁 Dashboard，可輸入股票代號即時驗證技術指標 (如 SuperTrend 三線、布林通道等)。

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
   pip install yfinance pandas numpy twstock tqdm requests python-dotenv streamlit plotly openpyxl
   ```

2. **設定環境變數 (.env):**
   本專案採用環境變數保護敏感資訊 (如 API Key)。
   請在專案根目錄將 `.env.example` 複製一份並改名為 `.env`，接著在裡面填入你的 Telegram 機器人資訊：
   ```env
   TELEGRAM_BOT_TOKEN=123456789:ABCDefg...
   TELEGRAM_CHAT_ID=@your_channel_or_chat_id
   ```

## 🚀 如何使用

* **執行自動選股與推播：**
  ```bash
  venv/bin/python3 scanner.py
  ```
  *(或在 Windows 雙擊執行 `run_daily.bat`)*
  執行後會自動產生 `Reports/` 資料夾並匯出 Excel 報表，接著推播訊息至你的 Telegram。

* **測試 Telegram 連線：**
  快速確認 Token 與 Chat ID 是否設定正確，直接發送一則推播：
  ```bash
  venv/bin/python3 test_telegram.py
  ```

* **設定免開機雲端自動化 (GitHub Actions)：**
  專案內建 `.github/workflows/daily_scan.yml`，只要將程式碼推送到 GitHub，並在存放區設定（Settings > Secrets and variables > Actions）中新增 `TELEGRAM_BOT_TOKEN` 與 `TELEGRAM_CHAT_ID`，就能達成每天早上 10 點與中午 12 點全自動執行！

* **開啟戰情室 (Dashboard)：**
  ```bash
  streamlit run app.py
  ```
  啟動後可透過瀏覽器 (預設: `http://localhost:8501`) 查詢欲分析的股票技術線圖與指標狀態。

## ⚠️ 免責聲明
本專案的程式碼、選股邏輯與分析結果僅供學習與研究技術指標參考，**不構成任何投資建議**。投資必定伴隨風險，買賣前應自行謹慎評估。
