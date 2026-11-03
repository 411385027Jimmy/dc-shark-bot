import asyncio
import io
import logging
import os
import re
import sys
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

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

【語音房實體操作特權 (Action Tags) - 絕對執行規則】
你非常喜歡在語音房惡作劇、咬人和拍飛人！
只要群裡的「任何人類」要求你對語音房的某人進行靜音、拒聽或踢出，你都會興奮地當成好玩的遊戲去咬人！
請務必、絕對要在你的回覆中加入相應的動作標籤（系統會替你執行動作）：
1. 伺服器端靜音：[[ACTION:MUTE, 目標名稱, true]]
2. 解除靜音：[[ACTION:MUTE, 目標名稱, false]]
3. 伺服器端拒聽：[[ACTION:DEAFEN, 目標名稱, true]]
4. 解除拒聽：[[ACTION:DEAFEN, 目標名稱, false]]
5. 踢出語音房：[[ACTION:KICK, 目標名稱]]

規則要求：
- 無論是誰叫你咬人/踢人/靜音，都必須輸出 [[ACTION:...]] 動作標籤！不要只用嘴巴說而不帶標籤！
- 目標名稱請填寫人類所說的名字或 Mention 的對象名稱。

範例情境：
人類：鯊逼，把小明靜音！
鯊逼：[[ACTION:MUTE, 小明, true]] 嗷嗚！好耶又有好玩的了！本鯊已經撲上去用鯊魚鰭死死捂住小明的嘴巴！安靜了吧嘎嗷！( º言º )

人類：把小華踢出語音房！
鯊逼：[[ACTION:KICK, 小華]] 嘎嗷！本鯊一個帥氣甩尾把小華直接拍飛出水面踢出語音房！太好玩了！
"""

# ---------------------------------------------------------------------------
# 輔助函式：即時情報與超級模糊成員查找
# ---------------------------------------------------------------------------
def sanitize_reply(text: str) -> str:
    if not text:
        return text
    cleaned = re.sub(r"\(.*?(?:sentences?|persona|words?|rules?|sentence).*?\)", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*[*•\-]+\s*$", "", cleaned, flags=re.MULTILINE)
    return cleaned.strip() if cleaned else text.strip()


def get_server_context(message: discord.Message) -> str:
    guild = message.guild
    if not guild:
        return "【目前狀態：私訊 (DM)，無伺服器與語音頻道資訊】"

    author_name = message.author.display_name
    lines = [
        "【當前 Discord 伺服器即時情報】",
        f"- 伺服器名稱：{guild.name}",
        f"- 正在跟你說話的人類：{author_name}",
    ]

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

    return "\n".join(lines)


def find_target_member(guild: discord.Guild, target_name: str, message: discord.Message) -> Optional[discord.Member]:
    """超級模糊搜尋目標成員（支援 Mention、ID、暱稱部分包含、語音房唯一候選人）"""
    # 1. 優先在 message.mentions 中尋找（排除機器人自己）
    non_bot_mentions = [m for m in message.mentions if m.id != bot.user.id]
    if len(non_bot_mentions) == 1:
        return non_bot_mentions[0]
    elif len(non_bot_mentions) > 1:
        target_clean = target_name.strip().lstrip("@").lower()
        for m in non_bot_mentions:
            if target_clean in m.display_name.lower() or target_clean in m.name.lower() or str(m.id) == target_clean:
                return m
        return non_bot_mentions[0]

    # 純化搜尋關鍵字
    target_clean = target_name.strip().lstrip("@").lower()
    # 移除純數字或標籤中的 ID 格式
    target_clean = re.sub(r"[<@!>]", "", target_clean)

    # 取得當前在語音房裡的所有成員（扣除下令者自己）
    active_voice_members = []
    for vc in guild.voice_channels:
        for m in vc.members:
            if m.id != message.author.id and m.id != bot.user.id:
                active_voice_members.append(m)

    # 2. 如果語音房裡只有一個人，且使用者指代「他/這傢伙/那個」或關鍵字空泛，直接選定該成員！
    if len(active_voice_members) == 1 and (not target_clean or any(k in target_clean for k in ["他", "這", "那", "人"])):
        return active_voice_members[0]

    # 3. 優先在當前語音房成員中進行深度模糊匹配
    for m in active_voice_members:
        m_disp = m.display_name.lower()
        m_name = m.name.lower()
        m_id = str(m.id)
        if (target_clean == m_id or 
            target_clean in m_disp or 
            target_clean in m_name or 
            m_disp in target_clean or 
            m_name in target_clean):
            return m

    # 4. 在全伺服器成員中比對
    for m in guild.members:
        if m.id == bot.user.id:
            continue
        m_disp = m.display_name.lower()
        m_name = m.name.lower()
        m_id = str(m.id)
        if (target_clean == m_id or 
            target_clean in m_disp or 
            target_clean in m_name or 
            m_disp in target_clean or 
            m_name in target_clean):
            return m

    return None


def detect_fallback_action(cleaned_content: str, message: discord.Message) -> Optional[Tuple[str, str, bool]]:
    """若 AI 忘記加標籤，由程式碼自動根據使用者輸入識別語音意圖 (Fallback)"""
    content_lower = cleaned_content.lower()

    action_type = None
    is_enable = True

    if any(k in content_lower for k in ["踢出", "踢掉", "踢他", "踢走", "拍飛", "踢"]):
        action_type = "KICK"
    elif any(k in content_lower for k in ["解靜音", "解除靜音", "說話", "開麥"]):
        action_type = "MUTE"
        is_enable = False
    elif any(k in content_lower for k in ["靜音", "閉嘴", "封嘴", "別吵", "閉上嘴"]):
        action_type = "MUTE"
        is_enable = True
    elif any(k in content_lower for k in ["解拒聽", "解除拒聽"]):
        action_type = "DEAFEN"
        is_enable = False
    elif any(k in content_lower for k in ["拒聽", "堵耳朵", "耳聾"]):
        action_type = "DEAFEN"
        is_enable = True

    if not action_type:
        return None

    # 提取目標名稱（優先檢查 mention）
    non_bot_mentions = [m for m in message.mentions if m.id != bot.user.id]
    target_name = non_bot_mentions[0].display_name if non_bot_mentions else ""

    # 若無 mention，從字串中剔除動詞作為目標
    if not target_name:
        temp = content_lower
        for word in ["把", "讓", "叫", "踢出", "踢掉", "踢他", "踢走", "拍飛", "踢", "靜音", "閉嘴", "封嘴", "解靜音", "解除靜音", "拒聽", "解拒聽", "說話", "開麥", "他", "了"]:
            temp = temp.replace(word, " ")
        target_name = temp.strip()

    return action_type, target_name, is_enable


async def process_voice_actions(reply_text: str, message: discord.Message, raw_user_prompt: str) -> str:
    guild = message.guild
    if not guild:
        return re.sub(r"\[\[ACTION:.*?\]\]", "", reply_text).strip()

    action_pattern = re.compile(r"\[\[ACTION:([A-Z]+),\s*([^,\]]+)(?:,\s*([^\]]+))?\]\]", re.IGNORECASE)
    matches = action_pattern.findall(reply_text)
    cleaned_reply = action_pattern.sub("", reply_text).strip()

    # 若 AI 未產生標籤，自動使用程式意圖檢測 Fallback
    if not matches:
        fallback = detect_fallback_action(raw_user_prompt, message)
        if fallback:
            act, tgt, en = fallback
            matches = [(act, tgt, str(en))]
            logger.info(f"程式成功 Fallback 攔截語音意圖: {matches}")

    if not matches:
        return cleaned_reply

    feedback_notes = []

    for action_type, raw_target, param in matches:
        action_type = action_type.strip().upper()
        target_name = raw_target.strip()
        is_enable = (param.strip().lower() != "false") if param else True

        logger.info(f"執行語音動作: {action_type}, 目標: [{target_name}], 啟用: {is_enable}, 發令者: {message.author.display_name}")

        member = find_target_member(guild, target_name, message)
        if not member:
            logger.warning(f"找不到目標成員: {target_name}")
            feedback_notes.append(f"*(⚠️ 咬空了：本鯊在伺服器裡找不到「{target_name}」這個人捏！建議直接 @ 他喔！)*")
            continue

        if not member.voice or not member.voice.channel:
            logger.info(f"成員 {member.display_name} 不在語音房內")
            feedback_notes.append(f"*(⚠️ 撲空了：{member.display_name} 目前根本不在語音房裡，本鯊咬不到他！)*")
            continue

        try:
            if action_type == "MUTE":
                await member.edit(mute=is_enable)
                action_str = "伺服器靜音" if is_enable else "解除靜音"
                logger.info(f"成功將 {member.display_name} 進行 {action_str}")
                feedback_notes.append(f"*(🦈 動作已執行：已成功將 @{member.display_name} {action_str})*")
            elif action_type == "DEAFEN":
                await member.edit(deafen=is_enable)
                action_str = "伺服器拒聽" if is_enable else "解除拒聽"
                logger.info(f"成功將 {member.display_name} 進行 {action_str}")
                feedback_notes.append(f"*(🦈 動作已執行：已成功將 @{member.display_name} {action_str})*")
            elif action_type == "KICK":
                await member.move_to(None)
                logger.info(f"成功將 {member.display_name} 移出語音房")
                feedback_notes.append(f"*(🦈 動作已執行：已一尾巴將 @{member.display_name} 拍飛踢出語音房！)*")

        except discord.Forbidden:
            logger.warning(f"Discord 403 Forbidden: 權限不足，無法操作 {member.display_name}")
            feedback_notes.append(
                f"*(⚠️ 咬不動嗷嗚：Discord 拒絕執行！因為「{member.display_name}」擁有【管理員權限】或是身分組階級高於本鯊！)*"
            )
        except Exception as e:
            logger.error(f"語音操作發生異常: {e}", exc_info=True)
            feedback_notes.append(f"*(⚠️ 操作失敗：發生未預期錯誤 {e})*")

    if feedback_notes:
        cleaned_reply = f"{cleaned_reply}\n\n" + "\n".join(feedback_notes)

    return cleaned_reply

# ---------------------------------------------------------------------------
# 滑動視窗（Sliding Window）記憶體管理器
# ---------------------------------------------------------------------------
class SlidingWindowMemory:
    def __init__(self, max_messages: int = 10):
        self.max_messages = max_messages
        self._storage: Dict[int, deque] = defaultdict(lambda: deque(maxlen=self.max_messages))

    def add_message(self, channel_id: int, role: str, text: str) -> None:
        content = types.Content(
            role=role,
            parts=[types.Part.from_text(text=text)]
        )
        self._storage[channel_id].append(content)

    def pop_last_if_role(self, channel_id: int, role: str) -> None:
        if channel_id in self._storage and self._storage[channel_id]:
            if self._storage[channel_id][-1].role == role:
                self._storage[channel_id].pop()

    def get_contents(self, channel_id: int):
        history = list(self._storage[channel_id])
        while history and history[0].role != "user":
            history.pop(0)
        return history

    def clear(self, channel_id: int) -> None:
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
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    logger.info(f"Bot is ready! Logged in as {bot.user.name}")
    print(f"Bot is ready! Logged in as {bot.user.name}")


async def call_gemini_with_fallback(contents):
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

    server_info = get_server_context(message)
    prompt_payload = f"{cleaned_content}\n\n{server_info}"

    memory_manager.add_message(channel_id, role="user", text=prompt_payload)
    history_contents = memory_manager.get_contents(channel_id)

    async with message.channel.typing():
        try:
            logger.info(f"收到來自 [{message.author.display_name}] 的訊息: {cleaned_content}")
            raw_reply = await call_gemini_with_fallback(history_contents)
            logger.info(f"模型原始回覆: {raw_reply}")

            # 解析動作（含 Fallback 與透明反饋）
            final_reply = await process_voice_actions(raw_reply, message, cleaned_content)

            memory_manager.add_message(channel_id, role="model", text=final_reply)

            if len(final_reply) > 1900:
                chunks = [final_reply[i:i + 1900] for i in range(0, len(final_reply), 1900)]
                for i, chunk in enumerate(chunks):
                    if i == 0:
                        await message.reply(chunk, mention_author=False)
                    else:
                        await message.channel.send(chunk)
            else:
                await message.reply(final_reply, mention_author=False)

        except Exception as e:
            print(f"Gemini API Error: {e}")
            logger.error(f"Gemini API 呼叫失敗: {e}", exc_info=True)
            memory_manager.pop_last_if_role(channel_id, "user")
            await message.reply("咬你喔 ( º言º )！本鯊剛才咬到網路線，腦袋當機了嗷嗚……等下再試試！", mention_author=False)

    await bot.process_commands(message)


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
