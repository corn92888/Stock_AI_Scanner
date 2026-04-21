import os
import requests
from dotenv import load_dotenv

# 讀取 .env 檔案內的 Token 與 Chat ID
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_custom_msg():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ 錯誤：找不到 Telegram Token 或 Chat ID，請確認 .env 檔案設定是否正確。")
        return

    print("📢 【Telegram 廣播器】")
    print("請輸入你想發送的訊息 (支援多行文字)。")
    print("輸入完畢後，請輸入 'SEND' 並按下 Enter 來發送，或輸入 'CANCEL' 取消。")
    print("-" * 40)

    lines = []
    while True:
        try:
            line = input()
            if line.strip().upper() == 'SEND':
                break
            elif line.strip().upper() == 'CANCEL':
                print("🚫 已取消發送。")
                return
            lines.append(line)
        except (EOFError, KeyboardInterrupt):
            print("\n🚫 已取消發送。")
            return

    msg = "\n".join(lines).strip()
    
    if not msg:
        print("⚠️ 訊息為空，已取消發送。")
        return

    print("-" * 40)
    print("🚀 正在發送中...")
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "disable_web_page_preview": True
    }
    
    response = requests.post(url, data=payload)
    if response.status_code == 200:
        print("✅ Telegram 訊息發送成功！")
    else:
        print(f"❌ 發送失敗: {response.text}")

if __name__ == "__main__":
    send_custom_msg()
