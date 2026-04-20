import os
import requests
from dotenv import load_dotenv

# 讀取 .env 檔案
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

print("🔍 檢查環境變數...")
print(f"- TELEGRAM_BOT_TOKEN: {'已設定' if TELEGRAM_BOT_TOKEN else '未設定 (空白)'}")
print(f"- TELEGRAM_CHAT_ID: {TELEGRAM_CHAT_ID if TELEGRAM_CHAT_ID else '未設定 (空白)'}")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("❌ 錯誤：請確認 .env 檔案中已經填寫 TELEGRAM_BOT_TOKEN 與 TELEGRAM_CHAT_ID")
else:
    print("\n🚀 嘗試發送測試訊息到 Telegram...")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "✅ 這是一則來自陳逸茗的自動測試訊息！\n如果你看到這則訊息，代表你是尤米爾的子民！",
        "disable_web_page_preview": True 
    }
    
    try:
        response = requests.post(url, data=payload)
        if response.status_code == 200:
            print("🎉 測試成功！Telegram 訊息已送達！")
        else:
            print(f"❌ 測試失敗！Telegram 回傳錯誤碼 {response.status_code}")
            print(f"詳細錯誤訊息：{response.text}")
    except Exception as e:
        print(f"❌ 連線失敗：{e}")
