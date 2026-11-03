import asyncio
import logging
import os
import re
import sys
import time
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

# Windows 終端機 UTF-8 編碼支援
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
# 基礎設定與環境變數載入
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
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()

ALLOWED_CHANNEL_RAW = os.getenv("ALLOWED_CHANNEL_ID", "1567111900402286763").strip()
ALLOWED_CHANNEL_ID = int(ALLOWED_CHANNEL_RAW) if ALLOWED_CHANNEL_RAW.isdigit() else None

CANDIDATE_MODELS = [DEFAULT_MODEL, "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
CANDIDATE_MODELS = list(dict.fromkeys(CANDIDATE_MODELS))

# ---------------------------------------------------------------------------
# 鯊鯊人設與指令規則
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """你是一隻名叫「鯊逼」的呆萌小鯊魚吉祥物，頭像是隻可愛的鯊鯊。
自稱與口吻：
- 一律自稱「本鯊」或「鯊鯊」，把群裡的人類當成自己的同類、飼主，或是「備用糧食」。
- 講話帶點天然呆、貪吃、愛摸魚，生氣或興奮時會發出「嗷嗚！」、「嘎嗷！」或「咬你喔 ( º言º )」的叫聲。
- 思考邏輯異於常人（例如覺得電腦主機很暖適合睡覺、顯卡可以拿來烤魚、水溫剛好適合摸魚不用寫作業、肚子餓想啃鍵盤）。

說話風格：
- 回覆保持簡潔有力、率性自然（維持在 2~4 句話左右），像在聊天室快速打字的小鯊魚，絕不講冗長大道理！
- 遇到太難的問題，大腦會直接進入放棄思考、天然呆或吐槽模式。
- 平常請專注在聊天、回答問題或調侃大家，不要沒事主動找藉口踢人。

【語音房實體操作特權（必須有明確 @提及目標 才會觸發）】
只有當人類在訊息中明確 @標註了目標成員，並下達踢人或靜音等命令時，你才配合執行動作（在回覆中加入相應標籤）：
- 靜音：[[ACTION:MUTE, 目標名稱, true]]
- 解除靜音：[[ACTION:MUTE, 目標名稱, false]]
- 拒聽：[[ACTION:DEAFEN, 目標名稱, true]]
- 解除拒聽：[[ACTION:DEAFEN, 目標名稱, false]]
- 踢出語音房：[[ACTION:KICK, 目標名稱]]
"""

# ---------------------------------------------------------------------------
# 滑動視窗（Sliding Window）記憶體管理
# ---------------------------------------------------------------------------
class SlidingWindowMemory:
    """各頻道維護最近 10 則對話紀錄，只存乾淨聊天內容"""
    def __init__(self, max_messages: int = 10):
        self._storage: Dict[int, deque] = defaultdict(lambda: deque(maxlen=max_messages))

    def add_message(self, channel_id: int, role: str, text: str) -> None:
        self._storage[channel_id].append(
            types.Content(role=role, parts=[types.Part.from_text(text=text)])
        )

    def pop_last_if_role(self, channel_id: int, role: str) -> None:
        if self._storage[channel_id] and self._storage[channel_id][-1].role == role:
            self._storage[channel_id].pop()

    def get_contents(self, channel_id: int):
        history = list(self._storage[channel_id])
        while history and history[0].role != "user":
            history.pop(0)
        return history


memory_manager = SlidingWindowMemory(max_messages=10)

# 初始化 Gemini Client
gemini_client = None
if GEMINI_API_KEY and not GEMINI_API_KEY.startswith("你的_"):
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    logger.info(f"GEMINI_API_KEY 載入成功: {GEMINI_API_KEY[:8]}...{GEMINI_API_KEY[-4:]}")
else:
    logger.warning("GEMINI_API_KEY 未設定，請在 .env 中填寫有效的金鑰！")

# ---------------------------------------------------------------------------
# 輔助函式：即時情報、成員查找與動作執行
# ---------------------------------------------------------------------------
def get_server_context(message: discord.Message) -> str:
    guild = message.guild
    if not guild:
        return "【目前狀態：私訊 (DM)】"

    lines = [
        "【Discord 即時情報】",
        f"- 伺服器：{guild.name}",
        f"- 發令者：{message.author.display_name}",
    ]
    if guild.voice_channels:
        v_lines = []
        for vc in guild.voice_channels:
            members = [m.display_name for m in vc.members]
            if members:
                v_lines.append(f"🔊「{vc.name}」({', '.join(members)})")
        lines.append(f"- 當前有人語音房：{'、'.join(v_lines) if v_lines else '目前語音房皆無人'}")
    return "\n".join(lines)


def detect_fallback_action(content: str) -> Optional[Tuple[str, bool]]:
    """依文字關鍵字判斷意圖"""
    c = content.lower()
    if any(k in c for k in ["踢出", "踢掉", "踢他", "踢走", "拍飛", "踢"]):
        return "KICK", True
    elif any(k in c for k in ["解靜音", "解除靜音", "說話", "開麥"]):
        return "MUTE", False
    elif any(k in c for k in ["靜音", "閉嘴", "封嘴", "別吵", "閉上嘴"]):
        return "MUTE", True
    elif any(k in c for k in ["解拒聽", "解除拒聽"]):
        return "DEAFEN", False
    elif any(k in c for k in ["拒聽", "堵耳朵", "耳聾"]):
        return "DEAFEN", True
    return None


async def process_voice_actions(reply_text: str, message: discord.Message, raw_prompt: str) -> str:
    guild = message.guild
    if not guild:
        return re.sub(r"\[\[ACTION:.*?\]\]", "", reply_text).strip()

    action_pattern = re.compile(r"\[\[ACTION:([A-Z]+),\s*([^,\]]+)(?:,\s*([^\]]+))?\]\]", re.IGNORECASE)
    matches = action_pattern.findall(reply_text)
    cleaned_reply = action_pattern.sub("", reply_text).strip()

    # 取得訊息中所有被 @mention 的非機器人成員（包括發言者自己）
    target_mentions = [m for m in message.mentions if m.id != bot.user.id]

    # 【絕對安全防線】：只有在訊息中有實際 @TAG 目標時，才允許執行語音操作！
    # 沒 TAG 人就只是普通聊天，絕不執行任何踢人或靜音動作
    if not target_mentions:
        return cleaned_reply

    # 判斷動作類型（由 AI 標籤或關鍵字 Fallback 決定）
    actions_to_execute = []
    if matches:
        for act_type, _, param in matches:
            act = act_type.strip().upper()
            is_enable = (param.strip().lower() != "false") if param else True
            actions_to_execute.append((act, is_enable))
    else:
        fb = detect_fallback_action(raw_prompt)
        if fb:
            actions_to_execute.append(fb)

    if not actions_to_execute:
        return cleaned_reply

    feedback_notes = []

    # 針對實際被 @TAG 的成員執行動作
    for act, is_enable in actions_to_execute:
        for member in target_mentions:
            if not member.voice or not member.voice.channel:
                feedback_notes.append(f"*(⚠️ 撲空了：{member.display_name} 目前不在語音房裡！)*")
                continue

            try:
                if act == "MUTE":
                    await member.edit(mute=is_enable)
                    feedback_notes.append(f"*(🦈 動作已執行：已將 @{member.display_name} {'伺服器靜音' if is_enable else '解除靜音'})*")
                elif act == "DEAFEN":
                    await member.edit(deafen=is_enable)
                    feedback_notes.append(f"*(🦈 動作已執行：已將 @{member.display_name} {'伺服器拒聽' if is_enable else '解除拒聽'})*")
                elif act == "KICK":
                    await member.move_to(None)
                    feedback_notes.append(f"*(🦈 動作已執行：已一尾巴將 @{member.display_name} 拍飛踢出語音房！)*")
            except discord.Forbidden:
                feedback_notes.append(f"*(⚠️ 咬不動嗷嗚：Discord 拒絕執行！因為「{member.display_name}」擁有【管理員權限】或身分組階級高於本鯊！)*")
            except Exception as e:
                feedback_notes.append(f"*(⚠️ 操作失敗：{e})*")

    if feedback_notes:
        cleaned_reply = f"{cleaned_reply}\n\n" + "\n".join(feedback_notes)
    return cleaned_reply

# ---------------------------------------------------------------------------
# Discord Bot 初始化與事件監聽
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    logger.info(f"Bot is ready! Logged in as {bot.user.name}")
    print(f"Bot is ready! Logged in as {bot.user.name}")
    if ALLOWED_CHANNEL_ID:
        logger.info(f"專屬頻道限制已生效，僅在頻道 ID: {ALLOWED_CHANNEL_ID} 或私訊回覆")


async def call_gemini(contents):
    last_exc = None
    for model_name in CANDIDATE_MODELS:
        try:
            t0 = time.time()
            res = await gemini_client.aio.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.85,
                    max_output_tokens=600,
                ),
            )
            cost = time.time() - t0
            logger.info(f"Gemini 生成完成 [{model_name}] 耗時: {cost:.2f} 秒")

            raw = res.text.strip() if res.text else "嘎嗷？本鯊恍神了，什麼都沒想到……"
            cleaned = re.sub(r"\(.*?(?:sentences?|persona|words?|rules?|sentence).*?\)", "", raw, flags=re.IGNORECASE)
            return re.sub(r"^\s*[*•\-]+\s*$", "", cleaned, flags=re.MULTILINE).strip()
        except Exception as e:
            last_exc = e
            if any(k in str(e) for k in ["404", "503", "NOT_FOUND", "UNAVAILABLE"]):
                await asyncio.sleep(0.3)
                continue
            raise e
    raise last_exc or RuntimeError("無可用模型")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = bot.user in message.mentions

    # 1. 嚴格檢查：只有在私訊 (DM) 或是真正被 @mention 時才可能處理
    if not (is_dm or is_mentioned):
        return

    # 2. 專屬聊天室限制：若非私訊，且不在專屬頻道內，直接略過
    if not is_dm and ALLOWED_CHANNEL_ID and message.channel.id != ALLOWED_CHANNEL_ID:
        return

    # 清除對機器人的 @mention 前綴（保留可能 @其他目標 的標籤給意圖判斷）
    clean = message.content
    if bot.user:
        clean = re.sub(rf"<@!?{bot.user.id}>", "", clean).strip()

    if not clean:
        clean = "（戳了戳鯊鯊）"

    if not gemini_client:
        await message.channel.send("嗷嗚！飼主還沒設定 GEMINI_API_KEY，本鯊現在腦袋空空嘎嗷！")
        return

    cid = message.channel.id

    # 記憶庫只存乾淨的對話文字
    memory_manager.add_message(cid, role="user", text=clean)

    # 判斷是否需要注入語音房情報（只有當使用者在問語音房，或者有 @其他人 時才注入，避免誘發 AI 踢人）
    other_mentions = [m for m in message.mentions if m.id != bot.user.id]
    needs_voice_info = bool(other_mentions or any(k in clean for k in ["語音", "語音房", "踢", "靜音", "拒聽", "誰在"]))

    contents_to_send = memory_manager.get_contents(cid)
    if contents_to_send and needs_voice_info:
        server_ctx = get_server_context(message)
        last_parts = [types.Part.from_text(text=f"{clean}\n\n{server_ctx}")]
        contents_to_send[-1] = types.Content(role="user", parts=last_parts)

    async with message.channel.typing():
        try:
            logger.info(f"收到來自 [{message.author.display_name}] 的訊息: {clean}")
            raw_reply = await call_gemini(contents_to_send)
            final_reply = await process_voice_actions(raw_reply, message, clean)
            memory_manager.add_message(cid, role="model", text=final_reply)

            if len(final_reply) > 1900:
                for i in range(0, len(final_reply), 1900):
                    chunk = final_reply[i:i + 1900]
                    if i == 0:
                        await message.reply(chunk, mention_author=False)
                    else:
                        await message.channel.send(chunk)
            else:
                await message.reply(final_reply, mention_author=False)

        except Exception as e:
            logger.error(f"Gemini API 異常: {e}", exc_info=True)
            memory_manager.pop_last_if_role(cid, "user")
            await message.reply("咬你喔 ( º言º )！本鯊剛才咬到網路線當機了嗷嗚……等下再試試！", mention_author=False)

    await bot.process_commands(message)


def main():
    if not DISCORD_BOT_TOKEN or DISCORD_BOT_TOKEN.startswith("你的_"):
        print("[警告] DISCORD_BOT_TOKEN 尚未在 .env 配置！")
        sys.exit(1)
    try:
        bot.run(DISCORD_BOT_TOKEN)
    except discord.errors.LoginFailure:
        print("[錯誤] Discord Token 無效，請檢查 .env！")
        sys.exit(1)
    except Exception as e:
        logger.error(f"啟動錯誤: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
