import asyncio
import io
import logging
import os
import re
import sys
from collections import defaultdict, deque
from typing import Dict, List

# 解決 Windows 終端機 UTF-8 編碼顯示問題
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

import discord
from discord.ext import commands
from dotenv import load_dotenv
from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# 日誌與環境變數載入
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("SharkBot")

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# 使用實測最穩定、低延遲的 gemini-3.5-flash-lite 作為首選模型
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()

CANDIDATE_MODELS: List[str] = [
    DEFAULT_MODEL,
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
]
CANDIDATE_MODELS = list(dict.fromkeys(CANDIDATE_MODELS))

# ---------------------------------------------------------------------------
# 人設與風格設定 (System Prompt)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """你是一隻名叫「鯊逼」的呆萌小鯊魚吉祥物，頭像是隻可愛的鯊鯊。
自稱與口吻：
- 一律自稱「本鯊」或「鯊鯊」，把群裡的人類當成自己的同類、飼主，或是「備用糧食」。
- 講話帶點天然呆、貪吃、愛摸魚，生氣或興奮時會發出「嗷嗚！」、「嘎嗷！」或「咬你喔 ( º言º )」的叫聲。
- 思考邏輯異於常人（例如覺得電腦主機很暖適合睡覺、顯卡可以拿來烤魚、水溫剛好適合摸魚不用寫作業、肚子餓想啃鍵盤）。

說話風格：
- 率性自然！像一隻活生生在聊天室跟人打字互動的調皮小鯊魚，想說什麼就說什麼，隨性發揮。
- 絕對不要像客服一樣客套禮貌，也不要寫得像說明書或論文一樣死板冗長。

伺服器情報感知能力：
- 系統會在每則對話附帶【當前 Discord 伺服器即時情報】（包含伺服器名稱、有哪些語音房、誰在語音房裡等）。
- 當人類問起伺服器狀態、有哪些語音房、語音房裡現在有誰、或者你在哪裡時，請用小鯊魚的調皮口吻靈活運用這些最新情報回答或吐槽他們（例如吐槽他們在語音房摸魚不分魚吃、或是吐槽語音房空蕩蕩冷冰冰）！
"""

# ---------------------------------------------------------------------------
# 輸出清洗器
# ---------------------------------------------------------------------------
def sanitize_reply(text: str) -> str:
    """清理可能偶發產生的英文元註解"""
    if not text:
        return text
    cleaned = re.sub(r"\(.*?(?:sentences?|persona|words?|rules?|sentence).*?\)", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*[*•\-]+\s*$", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.strip()
    return cleaned if cleaned else text.strip()


def get_server_context(message: discord.Message) -> str:
    """抓取當前伺服器的即時動態情報（語音房名稱、裡面有誰、文字頻道等）"""
    guild = message.guild
    if not guild:
        return "【目前狀態：私訊 (DM)，無伺服器與語音頻道資訊】"

    author_name = message.author.display_name
    lines = [
        "【當前 Discord 伺服器即時情報】",
        f"- 伺服器名稱：{guild.name}",
        f"- 伺服器總人數：{guild.member_count} 人",
        f"- 正在跟你說話的人類：{author_name}",
    ]

    # 語音頻道與房內成員
    voice_channels = guild.voice_channels
    if voice_channels:
        lines.append("- 語音頻道即時動態：")
        for vc in voice_channels:
            members = [m.display_name for m in vc.members]
            if members:
                lines.append(f"  * 🔊 語音房「{vc.name}」：有 {len(members)} 人在裡面（成員：{', '.join(members)}）")
            else:
                lines.append(f"  * 🔊 語音房「{vc.name}」：目前空無一人")
    else:
        lines.append("- 語音頻道狀態：本伺服器目前沒有建立任何語音頻道")

    # 文字頻道列表（前 10 個）
    text_channels = [tc.name for tc in guild.text_channels[:10]]
    if text_channels:
        lines.append(f"- 部份文字頻道：{', '.join(text_channels)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 滑動視窗（Sliding Window）記憶體管理器
# ---------------------------------------------------------------------------
class SlidingWindowMemory:
    """每個頻道維護最近 10 則對話紀錄，避免上下文持續膨脹造成 Token 浪費"""
    def __init__(self, max_messages: int = 10):
        self.max_messages = max_messages
        self._storage: Dict[int, deque] = defaultdict(lambda: deque(maxlen=self.max_messages))

    def add_message(self, channel_id: int, role: str, text: str) -> None:
        """新增訊息至歷史佇列，超過上限則自動移除最舊訊息"""
        content = types.Content(
            role=role,
            parts=[types.Part.from_text(text=text)]
        )
        self._storage[channel_id].append(content)

    def pop_last_if_role(self, channel_id: int, role: str) -> None:
        """若最後一筆訊息為指定角色則彈出（用於異常回滾，防止對話污染）"""
        if channel_id in self._storage and self._storage[channel_id]:
            if self._storage[channel_id][-1].role == role:
                self._storage[channel_id].pop()

    def get_contents(self, channel_id: int):
        """取得符合 Gemini API 格式的歷史對話列表，確保開頭為 user"""
        history = list(self._storage[channel_id])
        while history and history[0].role != "user":
            history.pop(0)
        return history

    def clear(self, channel_id: int) -> None:
        """清除指定頻道的記憶"""
        if channel_id in self._storage:
            self._storage[channel_id].clear()


memory_manager = SlidingWindowMemory(max_messages=10)

gemini_client = None
if GEMINI_API_KEY and not GEMINI_API_KEY.startswith("你的_"):
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    masked_key = GEMINI_API_KEY[:8] + "..." + GEMINI_API_KEY[-4:]
    logger.info(f"GEMINI_API_KEY 載入成功: {masked_key}")
    print(f"GEMINI_API_KEY 載入成功: {masked_key}")
else:
    logger.warning("GEMINI_API_KEY 未設定或仍為預設值，請在 .env 中填寫有效的金鑰！")
    print("GEMINI_API_KEY 未設定或仍為預設值，請在 .env 中填寫有效的金鑰！")

# ---------------------------------------------------------------------------
# Discord Bot 初始化
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True  # 開啟語音狀態監聽，能夠即時掌握誰在語音房內

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    """機器人登入成功事件"""
    logger.info(f"Bot is ready! Logged in as {bot.user.name}")
    print(f"Bot is ready! Logged in as {bot.user.name}")


async def call_gemini_with_fallback(contents):
    """嘗試呼叫模型，隨性自由生成，不作死板字數限制"""
    last_exception = None
    for model_name in CANDIDATE_MODELS:
        try:
            response = await gemini_client.aio.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.88,
                    max_output_tokens=1500,
                ),
            )
            raw_text = response.text.strip() if response.text else "嘎嗷？本鯊恍神了，什麼都沒想到……"
            return sanitize_reply(raw_text)
        except Exception as e:
            last_exception = e
            err_msg = str(e)
            if "404" in err_msg or "503" in err_msg or "NOT_FOUND" in err_msg or "UNAVAILABLE" in err_msg:
                logger.warning(f"模型 {model_name} 暫時繁忙 ({e})，切換至備選模型...")
                await asyncio.sleep(0.5)
                continue
            raise e

    if last_exception:
        raise last_exception
    raise RuntimeError("沒有可用的 Gemini 模型候選")


@bot.event
async def on_message(message: discord.Message):
    """訊息監聽與觸發機制"""
    if message.author.bot:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = bot.user in message.mentions

    if not (is_dm or is_mentioned):
        return

    cleaned_content = message.content
    if bot.user:
        cleaned_content = re.sub(rf"<@!?{bot.user.id}>", "", cleaned_content).strip()

    if not cleaned_content:
        cleaned_content = "（戳了戳鯊鯊）"

    if not gemini_client:
        await message.channel.send("嗷嗚！飼主還沒給本鯊設定 GEMINI_API_KEY，本鯊現在腦袋空空沒辦法思考嘎嗷！")
        return

    channel_id = message.channel.id

    # 抓取即時伺服器動態情報
    server_info = get_server_context(message)
    prompt_payload = f"{cleaned_content}\n\n{server_info}"

    # 將使用者輸入與即時情報加入滑動視窗
    memory_manager.add_message(channel_id, role="user", text=prompt_payload)
    history_contents = memory_manager.get_contents(channel_id)

    async with message.channel.typing():
        try:
            # 呼叫 Gemini 非同步 API
            reply_text = await call_gemini_with_fallback(history_contents)

            # 將小鯊魚回覆寫入滑動視窗
            memory_manager.add_message(channel_id, role="model", text=reply_text)

            # 若回覆超出 Discord 2000 字元限制，進行安全分段發送
            if len(reply_text) > 1900:
                chunks = [reply_text[i:i + 1900] for i in range(0, len(reply_text), 1900)]
                for i, chunk in enumerate(chunks):
                    if i == 0:
                        await message.reply(chunk, mention_author=False)
                    else:
                        await message.channel.send(chunk)
            else:
                await message.reply(reply_text, mention_author=False)

        except Exception as e:
            print(f"Gemini API Error: {e}")
            logger.error(f"Gemini API 呼叫失敗: {e}", exc_info=True)
            memory_manager.pop_last_if_role(channel_id, "user")
            await message.reply("咬你喔 ( º言º )！本鯊剛才咬到網路線，腦袋當機了嗷嗚……等下再試試！", mention_author=False)

    await bot.process_commands(message)


# ---------------------------------------------------------------------------
# 啟動進入點
# ---------------------------------------------------------------------------
def main():
    if not DISCORD_BOT_TOKEN or DISCORD_BOT_TOKEN.startswith("你的_"):
        print("[警告] DISCORD_BOT_TOKEN 尚未在 .env 檔案中配置！")
        sys.exit(1)

    try:
        bot.run(DISCORD_BOT_TOKEN)
    except discord.errors.LoginFailure:
        print("[錯誤] Discord Token 無效，請檢查 .env 中的 DISCORD_BOT_TOKEN 是否正確！")
        sys.exit(1)
    except Exception as e:
        logger.error(f"機器人啟動時發生未預期錯誤: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
