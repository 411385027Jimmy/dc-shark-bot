# 🦈 鯊逼 (Shark Bot) - 現代 Discord AI 聊天機器人

基於 `discord.py` 與最新 Google 官方 `google-genai` SDK（使用 `gemini-2.5-flash` 模型）開發的現代化 Discord 聊天機器人，具備「滑動視窗防爆機制」，打造呆萌小鯊魚吉祥物「鯊逼」！

---

## 🌟 特色亮點

- **🦈 獨特鯊鯊人設**：
  - 自稱「本鯊」或「鯊鯊」，把人類當成同類或「備用糧食」。
  - 講話天然呆、愛摸魚、常發出「嗷嗚！」、「嘎嗷！」或「咬你喔 ( º言º )」等叫聲。
  - 回覆精簡有力（維持在 2~3 句話內），拒絕機械式客服腔調。
- **🛡️ 滑動視窗（Sliding Window）記憶體管理**：
  - 獨立維護每個頻道的對話歷史佇列（預設最近 10 則訊息）。
  - 自動淘汰舊訊息，有效杜絕 Token 浪費與上下文爆表問題。
  - 保證 Gemini 多輪對話規格，自動校準對話結構順序。
- **⚡ 高效非同步體驗**：
  - 採用 `google-genai` 非同步介面（`client.aio.models.generate_content`），絕不卡死 Discord 事件迴圈。
  - 呼叫期間觸發 `message.channel.typing()` 顯示「正在輸入中...」，模擬真實打字體驗。
  - 自動剔除 @mention 前綴，回覆自然乾淨。

---

## 📁 專案結構

```text
dc_shark_bot/
├── .env                # 環境變數金鑰（Token 與 API Key，已被 .gitignore 排除）
├── .gitignore          # Git 忽略設定
├── requirements.txt    # 相依套件清單
├── bot.py              # 機器人核心主程式
└── README.md           # 專案說明文件
```

---

## 🚀 安裝與啟動教學

### 1. 安裝套件
請確認已安裝 Python 3.10+，並在專案根目錄執行：
```bash
pip install -r requirements.txt
```

### 2. 環境變數設定 (`.env`)
在 `.env` 中填入你的 Discord Bot Token 與 Gemini API Key：
```env
DISCORD_BOT_TOKEN=你的_DISCORD_BOT_TOKEN
GEMINI_API_KEY=你的_GEMINI_API_KEY
```

> **重要提醒**：請務必在 [Discord Developer Portal](https://discord.com/developers/applications) 的 **Bot** 頁面中開啟 **`MESSAGE CONTENT INTENT`**（特權意圖），否則機器人將無法讀取使用者傳送的訊息內容！

### 3. 啟動機器人
```bash
python bot.py
```
啟動成功後，終端機將輸出：
```
Bot is ready! Logged in as 鯊逼
```

---

## 🎮 使用方式

1. **私訊 (DM)**：直接發送任何訊息給鯊逼，牠會立即回覆。
2. **文字頻道**：在任何群組頻道中 `@鯊逼` 並輸入你想說的話，鯊鯊就會帶著呆萌口吻和你聊天！

