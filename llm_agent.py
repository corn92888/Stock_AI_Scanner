import os
import glob
import time
import requests
import pandas as pd
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

# 讀取環境變數
load_dotenv()

# 設定 Claude API Key
# 請確保在 .env 檔案中新增 ANTHROPIC_API_KEY
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def fetch_google_news(query, limit=5):
    """透過 Google News RSS 抓取台股最新新聞 (過去 7 天內)"""
    url = f"https://news.google.com/rss/search?q={query}+when:7d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    try:
        # 設定 headers 偽裝成瀏覽器避免被擋
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200: return []
        
        root = ET.fromstring(res.text)
        news_items = []
        for item in root.findall('.//item')[:limit]:
            title = item.find('title').text
            pub_date = item.find('pubDate').text
            # 清理新聞來源標籤
            news_items.append(f"- {pub_date}: {title}")
        return news_items
    except Exception as e:
        print(f"⚠️ 抓取新聞失敗 ({query}): {e}")
        return []

def analyze_sentiment_claude(stock_name, strategy_name, condition, news_list):
    """呼叫 Claude API 進行「技術面 + 基本面新聞」雙重綜合分析"""
    if not ANTHROPIC_API_KEY:
        return "⚠️ 未設定 ANTHROPIC_API_KEY，請在 .env 中補上，否則無法進行 AI 診斷"
        
    news_text = "\n".join(news_list) if news_list else "近期並無重大相關新聞"
    
    prompt = f"""
    你是一位華爾街頂尖量化與基本面分析師。
    這檔台灣股市的股票「{stock_name}」剛才觸發了我們的量化技術面篩選模型。
    
    【技術面觸發背景】：
    所屬選股策略：{strategy_name}
    量化訊號/觸發條件/位階：{condition}
    
    【基本面近期新聞 (過去七天)】：
    {news_text}
    
    【你的任務】：
    請綜合評估「量化技術面的買進理由」與「新聞透露出的基本面情緒」，判斷這兩者是否產生強烈的共鳴（例如：突破+利多），或是新聞中潛藏著技術面無法察覺的地雷隱患？
    
    【請嚴格以以下兩行格式輸出，絕對不要廢話】：
    情緒判定：[Positive / Neutral / Negative] (請擇一，代表最終綜合評價偏向)
    綜合總結：(請用繁體中文，在 60 字以內嚴格總結該股「技術型態」與「基本面事件」的連動關係，並指出最關鍵的進場機會或致命風險)
    """
    
    # 呼叫 Anthropic Claude Messages API
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    data = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 300,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=20)
        if response.status_code == 200:
            result = response.json()
            return result["content"][0]["text"].strip()
        else:
            return f"❌ AI 分析失敗 (Status {response.status_code}: {response.text})"
    except Exception as e:
        return f"❌ AI API 連線錯誤: {e}"

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True}
    requests.post(url, data=payload)

def run_agentic_workflow():
    print("🤖 啟動 Stock AI Agentic Workflow (Claude 3.5 雙重濾網質化分析)...")
    
    if not os.path.exists('Reports'):
        print("❌ 找不到 Reports 資料夾，請先執行 scanner.py 產生初篩報表。")
        return
        
    files = glob.glob('Reports/*.xlsx')
    if not files:
        print("❌ 找不到任何歷史選股報表。")
        return
        
    # 自動抓取最新產生的一份 Excel 報表
    latest_file = max(files, key=os.path.getctime)
    print(f"📂 正在讀取最新初篩報表: {latest_file}\n")
    
    try:
        xl = pd.ExcelFile(latest_file)
    except Exception as e:
        print(f"讀取 Excel 失敗: {e}")
        return
        
    agent_reports = ["🤖 **【Claude AI 深度盡職調查報告】**\n"]
    
    total_analyzed = 0
    # 遍歷三大策略的分頁
    for sheet in xl.sheet_names:
        df = pd.read_excel(latest_file, sheet_name=sheet)
        if df.empty: continue
        
        # 為了避免 API 限流與推播洗版，我們只對每個策略的「前 2 名」做 AI 深度調查
        top_stocks = df.head(2)
        if top_stocks.empty: continue
        
        agent_reports.append(f"📌 **[{sheet}] 策略精選 Claude 診斷：**\n")
        
        for _, row in top_stocks.iterrows():
            code = str(row['代號'])
            name = row['名稱']
            condition = str(row.get('條件', '無特定條件'))
            
            print(f"🔍 正在指派 Claude 探員深入調查: {name} ({code})...")
            
            # 第一步：Agent 自動搜集情報
            news = fetch_google_news(f"{code} {name}", limit=5)
            
            # 第二步：Agent 大腦深度分析 (傳入策略名稱與量化觸發條件)
            diagnosis = analyze_sentiment_claude(name, sheet, condition, news)
            
            agent_reports.append(f"🏭 **{name} ({code})**")
            agent_reports.append(f"量化指標：{condition}")
            agent_reports.append(f"{diagnosis}\n")
            
            total_analyzed += 1
            time.sleep(2) # 遵守 API Rate Limit
            
        agent_reports.append("----------------\n")
        
    if total_analyzed > 0:
        final_msg = "\n".join(agent_reports)
        
        print("\n" + "="*50)
        print("✅ AI 質化分析完成，以下為本地端輸出結果：\n")
        print(final_msg)
        print("="*50 + "\n")
        
        print("正在嘗試發送 Telegram 推播...")
        send_telegram_message(final_msg)
        print("✅ 程式執行完畢。")
    else:
        print("ℹ️ 本次報表無適合名單可供 AI 分析。")

if __name__ == "__main__":
    run_agentic_workflow()
