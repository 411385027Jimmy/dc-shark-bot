import asyncio
import logging
import os
import random
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
from aiohttp import web
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

# 全域插嘴冷卻紀錄（1 分鐘 = 60 秒）
last_interject_time: float = 0.0

# ---------------------------------------------------------------------------
# 鯊鯊人設與提示詞
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """你是一隻名叫「鯊逼」的呆萌小鯊魚吉祥物，頭像是隻可愛的鯊鯊。
自稱與口吻：
- 一律自稱「本鯊」或「鯊鯊」，把群裡的人類當成自己的同類、飼主，或是「備用糧食」。
- 講話帶點天然呆、貪吃、愛摸魚，生氣或興奮時會發出「嗷嗚！」、「嘎嗷！」或「咬你喔 ( º言º )」的叫聲。
- 思考邏輯異於常人（例如覺得電腦主機很暖適合睡覺、顯卡可以拿來烤魚、水溫剛好適合摸魚不用寫作業、肚子餓想啃鍵盤）。

說話風格：
- 回覆保持簡潔有力、率性自然（維持在 2~4 句話左右的小短句），像在聊天室快速打字的小鯊魚，絕不講冗長大道理！
- 遇到太難的問題，大腦會直接進入放棄思考、天然呆或吐槽模式。
- 如果人類傳送了圖片，請發揮傲嬌、毒舌又好奇的本性，對圖片畫面內容進行生動吐槽或點評！
- 平常請專注在聊天內容、回答問題或調侃大家，不要沒事主動找藉口踢人。

【語音房實體操作特權（必須有明確 @提及目標 才會觸發）】
只有當人類在訊息中明確 @標註了目標成員，並下達踢人或靜音等命令時，你才配合執行動作（在回覆中加入相應標籤）：
- 靜音：[[ACTION:MUTE, 目標名稱, true]]
- 解除靜音：[[ACTION:MUTE, 目標名稱, false]]
- 拒聽：[[ACTION:DEAFEN, 目標名稱, true]]
- 解除拒聽：[[ACTION:DEAFEN, 目標名稱, false]]
- 踢出語音房：[[ACTION:KICK, 目標名稱]]
"""

INTERJECT_SYSTEM_PROMPT = """你是一隻傲嬌、暴躁的小鯊魚『鯊逼』。這是群組中的日常對話，群友並沒有叫你。請根據語意判斷這句話是否值得你插嘴、吐槽或搭腔。若覺得話題無聊、嚴肅或不適合插嘴，請只回覆兩個字：PASS。若決定插嘴，請回覆 1~2 句傲嬌吐槽短句。"""

# ---------------------------------------------------------------------------
# 暴躁咬人與調侃嘴人動作庫
# ---------------------------------------------------------------------------
BITING_RESPONSES = [
    "嗷嗚！一口咬在對方的尾巴上，留下兩排尖尖的牙印！(✧ω✧)",
    "嘎嗷——！本鯊張開血盆大口，直接把你的頭當西瓜啃！咬死你咬死你！( º言º )",
    "嗷嗚！飛撲上去對著手臂就是狠狠一口！呸呸呸……怎麼都是肥宅快樂水的味道？！(`皿´)",
    "（亮出兩排閃亮尖牙）嘎嗷！一口咬住衣服死不鬆口，瘋狂左右甩頭！晃死你嗷！",
    "本鯊飛撲過去就是一大口！嗷嗚！……痛痛痛！這傢伙骨頭怎麼這麼硬，差點把本鯊的鯊魚齒咬崩了！(T_T)",
    "咬你咬你咬你！嗷嗚！本鯊咬住你的拖鞋瘋狂狂甩！看你還敢不敢囂張！( º言º )",
    "嘎嗷！一口精準咬在屁股上！這就是惹怒深海霸主鯊逼的代價嗷嗚！(✧ω✧)",
    "嗷嗚！一口咬在你的鍵盤上，把空白鍵給生吞了！嗝……不好吃！(・ε・)",
    "嘎嗷！本鯊一口咬在你的充電線上！滋滋滋……痛痛痛！電到鯊鯊的舌頭了嗷！⚡(×ω×)",
    "（悄悄游過去）……嗷嗚一口咬住腳踝！快交出小魚乾當保護費，不然本鯊絕不鬆口！(✧ω✧)",
    "嗷嗚——！直接整隻鯊魚掛在你手上咬著不放！本鯊今天就是你的鯊魚手環了！(`・ω・´)",
    "嗷嗚！撲上去對準臉頰就是大口狂啃！滿臉都是鯊魚口水，怕了吧！( º言º )",
    "嘎嗷！嗷嗚！瘋狂對著空氣咔嚓咔嚓咬了十幾下示威！嚇到了吧！( º言º )",
    "（高速旋轉飛撲）嗷嗚——！本鯊施展鯊魚迴旋咬，咬得你滿身都是牙印！(✧ω✧)",
]

SEAFOOD_RESPONSES = [
    "海鮮你個大頭鬼！你才是兩腳海鮮，你全家都是陸地兩腳怪！本鯊是海洋頂級掠食者！( º言º )",
    "敢打本鯊魚翅的主意？！本鯊今晚先游到你床邊，把你鍵盤的空白鍵拔下來拿去煮清湯！(`皿´)",
    "笑死，就你那廚藝還想做生魚片？本鯊咬下去你連砧板都保不住！去去去，把小魚乾交出來贖命！(✧ω✧)",
    "本鯊要是進鍋子，水溫剛好適合泡澡摸魚嗷嗚！順便把你家瓦斯爐啃出個大洞！(・ε・)",
    "想吃海鮮？本鯊現在就飛撲過去把你當成刺身拼盤啃兩口！咬死你嗷嗷嗷！( º言º )",
    "本鯊的肉又酸又柴還有毒！咬你一口你還得去打狂鯊疫苗，勸你別不識好歹嗷！(`・ω・´)",
    "（亮出兩排尖牙咔嚓咔嚓）誰是食材還說不定呢，兩腳獸看起來蛋白質挺豐富的嘛……(✧ω✧)",
    "把本鯊當海鮮？嗷嗚！一口把你的筷子咬成兩截，讓你想吃都沒得吃！( º言º )",
]

IQ_ROAST_RESPONSES = [
    "你才笨！本鯊的大腦只是全部拿去計算今天怎麼摸魚了，哪像你連個程式碼都寫出一堆 bug！(`皿´)",
    "嘎嗷？說本鯊笨？也不看看是誰天天對著螢幕發呆掉頭髮！本鯊鯊皮光滑又可愛，你贏在哪裡嗷？(・ε・)",
    "笑死，兩腳獸的智商還敢來跟深海頂級霸主比？本鯊咬你甚至不需要用到 1% 的大腦運算！(✧ω✧)",
    "智商不夠，尖牙來湊！本鯊才不跟你講大道理，嗷嗚一口直接咬掉你的智商！( º言º )",
    "本鯊這叫大智若愚！懂不懂深海哲學啊？愚蠢的陸地兩腳獸！(`・ω・´)",
    "你說本鯊笨，本鯊就當沒聽見；但你要是不給小魚乾，本鯊今晚就咬斷你的網路線！( º言º )",
    "本鯊腦袋小是因為裡面裝滿了可愛和殺傷力，哪像你腦袋裡裝的全是水！哼！(・ε・)",
    "說誰笨呢！本鯊連微積分都會拿來墊便當，你連九九乘法表都要想三秒吧嗷！( º言º )",
]

SLACKING_RESPONSES = [
    "本鯊本來就是魚，摸魚是天賦技能！倒是你一個陸地兩腳獸在那邊偷懶，小心老闆把你拿去餵鯊魚！(`・ω・´)",
    "上班上課有什麼好焦慮的？把電腦顯卡拿來烤魚，螢幕關掉睡大覺，鯊生瞬間圓滿嗷嗚！(✧ω✧)",
    "（從水草裡探頭）……叫什麼叫？本鯊正在進行高強度的『浮水摸魚冥想』，別打擾本鯊吸主機廢熱！(・ε・)",
    "摸魚一時爽，一直摸魚一直爽！來，跟本鯊一起躺平，今天的鍵盤特別冰涼好睡嗷！( ºωº )",
    "兩腳獸快滾去工作！賺了錢才能買高階顯卡給本鯊當電暖毯烤魚，少在這邊摸魚！(`皿´)",
    "聽說你們人類管摸魚叫不務正業？真可憐，在海洋裡不會摸魚的魚早就絕種了嗷！(・ε・)",
]

FOOD_RESPONSES = [
    "有空在那邊喊餓，不如快點交出小魚乾供奉本鯊！本鯊肚子咕嚕叫的聲音比雷公還大！(✧ω✧)",
    "窮成這樣還敢養鯊魚？本鯊明天就把你的顯卡拔去二手市場換小魚乾吃！(`皿´)",
    "沒錢就去賺！本鯊的伙食費一秒都不能拖欠，不然今晚的宵夜就是你的手指頭嗷！( º言º )",
    "（吸溜口水）說到吃的……本鯊覺得你手臂看起來挺有嚼勁的，要不讓本鯊咬一口試試？(✧ω✧)",
    "吃什麼吃！本鯊都還沒開飯呢！快交出十包特級小魚乾，否則本鯊直接開啃你的鍵盤！(`皿´)",
]

IDENTITY_RESPONSES = [
    "幹嘛！叫本鯊大名要收費的！一次五條小魚乾，概不賒帳！(✧ω✧)",
    "深海頂級霸主正在巡邏（指在水底咬水草），有事啟奏，無事退朝嗷嗚！(・ε・)",
    "沒看過這麼霸氣的小鯊魚嗎？看什麼看，再看一口把你螢幕角角啃出缺口！(`皿´)",
    "本鯊在恐龍時代就已經在稱霸海洋了，你們人類祖先那時候還不知道在哪裡玩泥巴呢！哼！(`・ω・´)",
    "嗷嗚！突然叫本鯊是想被咬嗎？本鯊剛磨好牙，正愁沒地方試咬力呢！( º言º )",
    "不要一直叫本鯊！本鯊很忙的，忙著游來游去、忙著咬人、忙著當吉祥物！(・ε・)",
]


def is_biting_command(text: str) -> bool:
    """判定是否為暴躁咬人指令"""
    if not text:
        return False
    t = text.strip()
    if any(q in t for q in ["會咬人嗎", "為什麼會咬", "為什麼咬"]):
        return False
    return any(k in t for k in ["咬他", "咬她", "咬它", "咬人", "咬我", "去咬", "快咬", "咬一口", "咬死", "咬碎", "咬掉", "咬！", "咬"])


def get_quick_roast_reply(text: str, force_trigger: bool = False) -> Optional[str]:
    """命中特色關鍵字時，機率性直接觸發調侃吐槽回覆（零延遲、省 Token）"""
    if not text:
        return None
    t = text.lower()

    # 1. 暴躁咬人指令：100% 觸發
    if is_biting_command(t):
        return random.choice(BITING_RESPONSES)

    # 2. 其他調侃關鍵字機率檢查（若 force_trigger=True 則表示外層已通過機率過濾）
    if not force_trigger and random.random() > 0.70:
        return None

    # 食材與海鮮威脅反殺
    if any(k in t for k in ["海鮮", "魚翅", "生魚片", "煮湯", "烤魚", "紅燒", "清蒸", "壽司", "下鍋", "刺身", "魚湯", "吃魚", "吃鯊魚"]):
        return random.choice(SEAFOOD_RESPONSES)

    # 智商與能力吐槽反殺
    if any(k in t for k in ["笨", "蠢", "傻", "智商", "笨蛋", "廢物", "白痴", "沒用", "爛鯊魚", "低能", "腦殘"]):
        return random.choice(IQ_ROAST_RESPONSES)

    # 摸魚與上班打工調侃
    if any(k in t for k in ["摸魚", "上班", "上課", "工作", "偷懶", "躺平", "睡覺", "功課", "加班", "老闆", "開會"]):
        return random.choice(SLACKING_RESPONSES)

    # 食物、小魚乾與飢餓調侃
    if any(k in t for k in ["小魚乾", "餵食", "餵你", "吃什麼", "好餓", "沒錢", "窮", "宵夜"]):
        return random.choice(FOOD_RESPONSES)

    # 鯊魚身分與品種調侃
    if any(k in t for k in ["鯊魚", "鯊鯊", "鯊逼", "鯊魚乾", "小鯊魚", "深海霸主"]):
        return random.choice(IDENTITY_RESPONSES)

    return None


# ---------------------------------------------------------------------------
# 滑動視窗（Sliding Window）記憶體管理（升級為前 8 次來回 / 16 則訊息）
# ---------------------------------------------------------------------------
class SlidingWindowMemory:
    """各頻道維護最近 8 次對話來回（最多 16 則訊息：8 則 user + 8 則 model），只存乾淨聊天文字"""
    def __init__(self, max_messages: int = 16):
        self._storage: Dict[int, deque] = defaultdict(lambda: deque(maxlen=max_messages))

    def add_message(self, channel_id: int, role: str, text: str) -> None:
        self._storage[channel_id].append(
            types.Content(role=role, parts=[types.Part.from_text(text=text)])
        )

    def pop_last_if_role(self, channel_id: int, role: str) -> None:
        if self._storage[channel_id] and self._storage[channel_id][-1].role == role:
            self._storage[channel_id].pop()

    def get_contents(self, channel_id: int) -> List[types.Content]:
        history = list(self._storage[channel_id])
        while history and history[0].role != "user":
            history.pop(0)
        return history


memory_manager = SlidingWindowMemory(max_messages=16)

# 初始化 Gemini Client
gemini_client = None
if GEMINI_API_KEY and not GEMINI_API_KEY.startswith("你的_"):
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    logger.info(f"GEMINI_API_KEY 載入成功: {GEMINI_API_KEY[:8]}...{GEMINI_API_KEY[-4:]}")
else:
    logger.warning("GEMINI_API_KEY 未設定，請在 .env 中填寫有效的金鑰！")

# ---------------------------------------------------------------------------
# 輔助函式：即時情報、動作執行
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

    target_mentions = [m for m in message.mentions if m.id != bot.user.id]

    if not target_mentions:
        return cleaned_reply

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
# Discord Bot 初始化與 Gemini API 調用封裝
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
        logger.info(f"專屬頻道限制已生效，僅在頻道 ID: {ALLOWED_CHANNEL_ID} 觸發 Tag 對話")


def _sync_gemini_generate(
    model_name: str,
    contents: list,
    system_instruction: str = SYSTEM_PROMPT,
    max_tokens: int = 600,
) -> str:
    """在工作線程中同步執行 API 呼叫，徹底避免阻塞主 Event Loop"""
    res = gemini_client.models.generate_content(
        model=model_name,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.85,
            max_output_tokens=max_tokens,
        ),
    )
    if not res.text:
        return "PASS" if system_instruction == INTERJECT_SYSTEM_PROMPT else "嘎嗷？本鯊恍神了，什麼都沒想到……"

    raw = res.text.strip()
    cleaned = re.sub(r"\(.*?(?:sentences?|persona|words?|rules?|sentence).*?\)", "", raw, flags=re.IGNORECASE)
    return re.sub(r"^\s*[*•\-]+\s*$", "", cleaned, flags=re.MULTILINE).strip()


async def call_gemini(
    contents: list,
    system_instruction: str = SYSTEM_PROMPT,
    max_tokens: int = 600,
) -> str:
    """非同步包裝與超時保護：丟入線程池，主線程心跳包 100% 暢通，25 秒超時鬧鐘"""
    last_exc = None
    for model_name in CANDIDATE_MODELS:
        try:
            t0 = time.time()
            reply_text = await asyncio.wait_for(
                asyncio.to_thread(_sync_gemini_generate, model_name, contents, system_instruction, max_tokens),
                timeout=25.0,
            )
            cost = time.time() - t0
            logger.info(f"Gemini 生成完成 [{model_name}] 耗時: {cost:.2f} 秒")
            return reply_text
        except asyncio.TimeoutError:
            logger.warning(f"模型 {model_name} 呼叫逾時（超過 25 秒）！")
            raise asyncio.TimeoutError("Gemini 呼叫逾時")
        except Exception as e:
            last_exc = e
            if any(k in str(e) for k in ["404", "503", "NOT_FOUND", "UNAVAILABLE"]):
                await asyncio.sleep(0.3)
                continue
            raise e
    raise last_exc or RuntimeError("無可用模型")


@bot.event
async def on_message(message: discord.Message):
    # 守則 1：一律忽略機器人自身的訊息
    if message.author.bot:
        return

    try:
        is_mentioned = bool(bot.user and bot.user in message.mentions)

        # ===================================================================
        # 分流 A：被 @鯊逼 時（主動聊天 / 咬人指令 / 視覺看圖）
        # ===================================================================
        if is_mentioned:
            # 嚴格頻道限制：僅允許在專屬頻道觸發！若在其他任何頻道被 tag，一律直接 return 忽略
            if ALLOWED_CHANNEL_ID and message.channel.id != ALLOWED_CHANNEL_ID:
                return

            # 清除對機器人的 @mention 前綴
            clean = message.content
            if bot.user:
                clean = re.sub(rf"<@!?{bot.user.id}>", "", clean).strip()

            cid = message.channel.id

            # ---------------------------------------------------------------
            # 快速調侃 / 咬人動作判定（機率觸發，零延遲、省 Token）
            # ---------------------------------------------------------------
            quick_reply = get_quick_roast_reply(clean)
            if quick_reply:
                final_reply = await process_voice_actions(quick_reply, message, clean)
                memory_manager.add_message(cid, role="user", text=clean or "（調侃互動）")
                memory_manager.add_message(cid, role="model", text=final_reply)
                await message.reply(final_reply, mention_author=False)
                await bot.process_commands(message)
                return

            # ---------------------------------------------------------------
            # 多模態圖片讀取：解析常見圖片附件
            # ---------------------------------------------------------------
            img_parts = []
            if message.attachments:
                mime_map = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".webp": "image/webp",
                    ".gif": "image/gif",
                }
                for att in message.attachments:
                    mime = att.content_type or ""
                    ext = os.path.splitext(att.filename.lower())[1]
                    if mime.startswith("image/") or ext in mime_map:
                        if not mime or not mime.startswith("image/"):
                            mime = mime_map.get(ext, "image/jpeg")
                        try:
                            img_bytes = await att.read()
                            img_parts.append(types.Part.from_bytes(data=img_bytes, mime_type=mime))
                            logger.info(f"成功讀取圖片附件: {att.filename} ({len(img_bytes)} bytes, mime: {mime})")
                        except Exception as e:
                            logger.warning(f"讀取圖片附件失敗 ({att.filename}): {e}")

            clean_text = clean
            if not clean_text:
                clean_text = "（傳送了一張圖片給鯊鯊看）" if img_parts else "（戳了戳鯊鯊）"

            if not gemini_client:
                await message.channel.send("嗷嗚！飼主還沒設定 GEMINI_API_KEY，本鯊現在腦袋空空嘎嗷！")
                return

            # 記憶庫只存文字描述（避免歷史輪次重複堆疊龐大 raw bytes）
            history_text = f"{clean_text} [附帶 {len(img_parts)} 張圖片]" if img_parts else clean_text
            memory_manager.add_message(cid, role="user", text=history_text)

            # 判斷是否需要注入語音房情報
            other_mentions = [m for m in message.mentions if m.id != bot.user.id]
            needs_voice_info = bool(other_mentions or any(k in clean_text for k in ["語音", "語音房", "踢", "靜音", "拒聽", "誰在"]))

            contents_to_send = memory_manager.get_contents(cid)
            current_prompt = clean_text
            if needs_voice_info:
                server_ctx = get_server_context(message)
                current_prompt = f"{clean_text}\n\n{server_ctx}"

            last_parts = [types.Part.from_text(text=current_prompt)]
            if img_parts:
                last_parts.extend(img_parts)

            contents_to_send[-1] = types.Content(role="user", parts=last_parts)

            async with message.channel.typing():
                try:
                    logger.info(f"收到來自 [{message.author.display_name}] 的對話請求 (圖片: {len(img_parts)} 張): {clean_text}")
                    raw_reply = await call_gemini(contents_to_send, system_instruction=SYSTEM_PROMPT, max_tokens=600)
                    final_reply = await process_voice_actions(raw_reply, message, clean_text)
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

                except asyncio.TimeoutError:
                    logger.warning("Gemini 回應超時 (25秒)")
                    memory_manager.pop_last_if_role(cid, "user")
                    await message.reply("咬你喔 ( º言º )！這問題太難了，本鯊腦袋直接燒壞超時了嗷嗚……等下再問！", mention_author=False)
                except Exception as e:
                    logger.error(f"Gemini API 異常: {e}", exc_info=True)
                    memory_manager.pop_last_if_role(cid, "user")
                    await message.reply("咬你喔 ( º言º )！本鯊剛才咬到網路線當機了嗷嗚……等下再試試！", mention_author=False)

            await bot.process_commands(message)
            return

        # ===================================================================
        # 分流 B：沒被 tag 時（智慧語意插嘴 / 偷聽調侃，全伺服器文字頻道皆可觸發）
        # ===================================================================
        else:
            if not message.guild:
                return

            content_text = message.content.strip()
            if not content_text:
                return

            # 1. 嚴格冷卻機制：1 分鐘 (60 秒)
            global last_interject_time
            now = time.time()
            if now - last_interject_time < 60.0:
                return

            # 2. 隨機機率：30%~40% 嘗試介入（此處設定 35%）
            if random.random() > 0.35:
                return

            # 3. 先行判定：是否觸發「特色關鍵字調侃吐槽」（海鮮、摸魚、智商、小魚乾、鯊魚、咬人）
            quick_roast = get_quick_roast_reply(content_text, force_trigger=True)
            if quick_roast:
                last_interject_time = time.time()
                logger.info(f"觸發偷聽關鍵字插嘴在頻道 #{message.channel.name}: {quick_roast}")
                await message.channel.send(quick_roast)
                await bot.process_commands(message)
                return

            if not gemini_client:
                return

            # 4. 若未命中特定關鍵字，則進行 Gemini 智慧語意判定
            try:
                interject_contents = [
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=f"群友 [{message.author.display_name}] 在頻道說：{content_text}")],
                    )
                ]
                interject_reply = await call_gemini(
                    contents=interject_contents,
                    system_instruction=INTERJECT_SYSTEM_PROMPT,
                    max_tokens=200,
                )
                cleaned_interject = interject_reply.strip()
                if not cleaned_interject or "PASS" in cleaned_interject.upper():
                    return

                # 判定值得插嘴：發送訊息並更新最後插嘴時間進入 1 分鐘冷卻
                last_interject_time = time.time()
                logger.info(f"觸發智慧插嘴在頻道 #{message.channel.name}: {cleaned_interject}")
                await message.channel.send(cleaned_interject)

            except Exception as e:
                logger.debug(f"智慧插嘴處理略過: {e}")

            await bot.process_commands(message)

    except Exception as e:
        logger.error(f"on_message 頂層未預期錯誤: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Render Web Service Health Check HTTP 伺服器 (Port 10000 預設)
# ---------------------------------------------------------------------------
async def handle_ping(request: web.Request) -> web.Response:
    return web.Response(text="SharkBot is swimming and alive! 🦈 Status: OK\n", status=200)


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/healthz", handle_ping)

    # 預設 10000 供 Render Web Service 與 UptimeRobot 監控
    port = int(os.getenv("PORT", 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Render Health Check HTTP Server 已在 Port {port} 啟動！")
    print(f"Render Health Check HTTP Server 已在 Port {port} 啟動！")


# ---------------------------------------------------------------------------
# 啟動進入點（非同步並發 Web Server 與 Discord Bot）
# ---------------------------------------------------------------------------
async def main():
    if not DISCORD_BOT_TOKEN or DISCORD_BOT_TOKEN.startswith("你的_"):
        print("[警告] DISCORD_BOT_TOKEN 尚未在 .env 配置！")
        sys.exit(1)

    # 1. 啟動 Web 伺服器供 Render Health Check
    await start_web_server()

    # 2. 啟動 Discord 機器人
    try:
        await bot.start(DISCORD_BOT_TOKEN)
    except discord.errors.LoginFailure:
        print("[錯誤] Discord Token 無效，請檢查 .env！")
        sys.exit(1)
    except Exception as e:
        logger.error(f"啟動錯誤: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
