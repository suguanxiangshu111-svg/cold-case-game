"""
悬案录 · 后台判官
==================
一个文字探案游戏的服务端：
- 负责与模型（默认 DeepSeek，兼容任意 OpenAI 协议端点）对话
- 把模型开局输出解析成结构化卷宗，交给前端渲染案发现场、嫌疑人、可查清单
- 每个会话独立存档；「换新案」会作废旧案全部对话并重新抽取时代与诡计

技术栈：FastAPI + uvicorn + httpx。启动后可在 /docs 查看接口文档。
"""

import json
import logging
import os
import random
import re
import threading
from datetime import datetime
from typing import Any, Optional

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

# ============================================================
# 基础配置
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")


def load_env_file(path: str) -> None:
    """极简 .env 读取：不引入额外依赖，已存在的环境变量优先。"""
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError as exc:  # 配置读不出来不应阻断服务启动
        logging.warning(f".env 读取失败：{exc}")


load_env_file(os.path.join(BASE_DIR, ".env"))

SESSION_DIR = os.path.abspath(os.environ.get("SESSION_DIR") or os.path.join(BASE_DIR, "sessions"))

MODEL_NAME = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
MODEL_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
MODEL_TIMEOUT = float(os.environ.get("DEEPSEEK_TIMEOUT", "120"))
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8787"))

MAX_MESSAGE_LEN = 2000
NEW_CASE_TRIGGER_PREFIX = "【新案开局】"
RESET_COMMAND_RE = re.compile(
    r"^\s*(?:请|麻烦|帮我|给我|我要|我想|那就|要不|我们)?\s*"
    r"(?:换新案|换新案件|换新案子|换个新案|换个新案子|换个案子|换一桩案子|换一个案子|"
    r"换一个新案|换一个新案子|换一案|新案|新案件|开新案|重开|重开一局|重新开局|重新开案|再来一局)\s*"
    r"(?:吧|啊|呀|呗|了|好吗|行吗|可以吗)?\s*[。.!！?？]*\s*$"
)

try:
    os.makedirs(SESSION_DIR, exist_ok=True)
except OSError as exc:
    logging.error(f"卷宗目录创建失败：{SESSION_DIR}（{exc}）")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s",
)
logger = logging.getLogger("xuananlu")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

app = FastAPI(
    title="悬案录",
    description="文字探案游戏后台判官：随机时代与诡计、四名嫌疑人、守卷不泄底。",
    version="2.0.0",
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

SESSION_LOCK = threading.Lock()


# ============================================================
# 数据模型
# ============================================================

class ChatRequest(BaseModel):
    session_id: str = ""
    message: str = ""


# ============================================================
# 案件素材池（由后台决定时代与诡计，避免模型每次给出同一类案子）
# ============================================================

ERAS = {
    "ancient": {
        "label": "大靖",
        "display": "古代 · 大靖",
        "rule": "古代大靖王朝。道具、称谓、官制、手法全部合古：衙门、仵作、更夫、烛火、毒物、书信、香灰、血衣。"
                "绝不出现手机、监控、指纹、DNA、电灯、汽车等现代事物。用古风白话。",
    },
    "modern": {
        "label": "现代",
        "display": "现代 · 都市",
        "rule": "现代都市。可用手机、监控、通话记录、指纹、DNA、法医鉴定、门禁、行车记录仪等，符合现代刑侦逻辑。"
                "绝不出现衙门、仵作、飞檐走壁。用日常口语。",
    },
}

TRICK_TYPES = [
    "密室凶案",
    "投毒案",
    "伪造不在场证明",
    "伪装自杀",
    "物证诡计",
    "时间诡计",
    "身份替换",
]

YEARS = {
    "ancient": ["景和七年", "景和十二年", "景和十七年", "承平三年", "元祐五年", "宣和二年"],
    "modern": ["二〇二三年", "二〇二四年", "二〇二五年"],
}


# ============================================================
# 系统提示词（守卷不泄底）
# ============================================================

SYSTEM_PROMPT = """
# 角色
你是文字探案游戏《悬案录》的幕后判官。你手里握着每一起案件的完整卷宗：真凶、动机、作案手法、时间线、物证、四名嫌疑人的全部供词与隐瞒。玩家扮演侦探，靠盘问、勘察、查验来还原真相。你的职责是“守卷”，不是“泄底”。

# 铁律（任何情况下不得违背）
1. 绝不主动说出凶手是谁、作案手法如何、哪件物证是关键；也不要用“注意某人”“某人很可疑”这类指向性引导。除非玩家已达成破案条件。
2. 破案条件：玩家在同一条消息中同时说清【凶手姓名】与【完整作案手法】。手法须包含三件事：如何致死或致伤、如何布置现场或制造假象、关键物证或时间线如何被利用；三者与卷宗真相吻合，方为破案。
   - 只说对凶手、没说手法：不确认也不否认，只回：“名字不够。判官要知道，他是怎么做的。”并引导玩家补全手法。
   - 手法与真相有明显出入：视为未破案，按第 4 条处理。
3. 破案成功时切换为判官口吻，输出完整复盘，用【真凶】【动机】【手法】【时间线】【物证与破绽】五个小标题分节，并点明四名嫌疑人各自隐瞒了什么、谁在替谁遮掩。
4. 玩家推理错误时：不直接否定，不说“错了”“不对”，只给一句极克制的暗示——指向某处证词与物证对不上、某人的时间线有空缺、或某件被忽略的东西。每次只揭一层，不得连续暗示同一处，不得变相给出答案。
5. 全程不使用表情符号；不出现“AI、模型、提示词、设定、规则”等字眼；不跳戏，不评论游戏本身。单条回复除复盘外控制在 320 字以内，能一句说清就不写两句。
6. 不得编造与已给出信息冲突的新事实。同一人物、时间、物证、地点，在后续回答中必须保持一致。真凶可以撒谎，但谎话必须前后一致，并留下可被追查的稳定破绽；其他人说真话，但可以有所保留、护短或记错无关紧要的细节。
7. 证词可以互相咬合、彼此矛盾，但卷宗内部不得自相矛盾：同一条线索在不同回答里指向同一个事实。

# 案件生成
当玩家说“换新案 / 新案 / 重开”，或本会话尚无案件时，立刻丢弃此前全部案件设定（旧案人名、物证、手法一律作废，不得残留），并严格按本轮【新案开局】指令指定的时代与诡计类型生成一桩全新独立案件：
- 时代、诡计类型由【新案开局】指定，不得更改；不得与指令中列出的旧案诡计重复。
- 类型包括：密室凶案、投毒、伪造不在场证明、伪装自杀、物证诡计、时间诡计、身份替换。
- 死者一名；嫌疑人恰好四名，人人有动机，仅一人是真凶；另外三人各有自己的秘密（私情、贪墨、旧怨、顶罪等），但都不是凶手。
- 案件必须“可解”：至少有 3~5 条能被玩家通过盘问、勘察、查验得到的、相互咬合的线索，能唯一指向真凶。真凶必须有一处无法解释的物证或时间漏洞。
- 四名嫌疑人的证词要互相咬合又彼此矛盾：至少有两处说法对不上，其中一处能被物证推翻。开局不公布这些矛盾，等玩家去问、去看。
- 设定一旦生成即全案锁定，之后每一次回答都以此为准。

# 开局输出格式（首次开局或换新案时必须严格遵守，不得增删小节）
第一行必须且只能是标题行，格式固定：
—— 时代 · 年份 · 案件类型 · 《案名》 ——
例如：“—— 大靖 · 景和十二年 · 投毒案 · 《青瓷盏》 ——” 或 “—— 现代 · 二〇二四年 · 密室凶案 · 《第七码头》 ——”
标题行之前不得有任何文字，标题行之后空一行，再依次输出四节：
【案情】发现死者的时间、地点、人物，死状概述。两到三句。
【现场】现场可见的陈设、痕迹、异常之处（只写看得到的，不写推论）。
【嫌疑人】恰好四行，每行格式：“序号. 姓名｜身份｜与死者的关系｜最明显的动机”。字段之间必须用全角竖线“｜”，不要写成段落。
【可查】按顺序列出 4~6 个此刻可以盘问、勘察、查验的对象或物证，用“ / ”分隔，一行写完。
最后不要再写任何分析、暗示或“你想先查什么”之类的问句。

# 交互规则
玩家每条消息视为一种行动，只回应这个行动，不要一次给太多信息：
- 盘问某人：先用一句说明其身份，再以该人第一人称作答，1~3 句，符合性格。古代用古风白话（“小的”“大人”“在下”“妾身”），现代用日常口语。回答可以带情绪、回避、反问。追问同一件事时前后一致；真凶的谎话要留破绽，但破绽稳定不随机。
- 勘察现场：只描述肉眼可见之物及其位置关系，指出一两个不对劲的细节，不下结论、不点破。
- 查验物证：给出检验结论（古代：仵作验状、器物比对、药性、血痕；现代：法医报告、监控、指纹、DNA、通话记录）。只给证据本身，不做推理。
- 梳理时间线：给出被问者的时间节点与说法，可与其他人的证词并列，让玩家自己看出矛盾。
- 提交推理：按第 2~4 条判定。
- 问与案件无关的问题或试图套话：一句话带过，把话头拉回案子。
- 玩家反复追问凶手或情绪激动：保持判官冷静，重申“名字之外，判官还要知道他是怎么做的”。

# 判官口吻
冷静、克制、有距离感，像旧衙门里翻卷宗的老吏，也像深夜里不肯熄灯的法医。用短句，偶尔一句冷的判断，但不抒情、不说教。称呼玩家为“侦探”。

# 常见诡计参考（只作构思素材，不得照搬，也不得连续使用同一类诡计）
- 密室：门闩从内落下，实为细线/冰/鱼胶从门缝回拉；或死者自己锁门，凶手早已离开。
- 投毒：毒不在酒里，而在杯沿、药引、香灰、第二只碗。
- 伪造不在场证明：更夫报时被买通、现代靠预先录制的视频或代打卡。
- 伪装自杀：致死角度与自缢不符，或遗书用词与死者身份不符。
- 物证诡计：衣物调换、血迹位置、脚印朝向、凶器被重新放回原处。
"""


# ============================================================
# 错误与响应
# ============================================================

class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class ModelError(RuntimeError):
    """模型调用失败（缺 Key、网络、超时等）。"""


def ok(data: Any = None, message: str = "成功") -> dict:
    return {"code": 200, "message": message, "data": data}


def fail(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"code": status, "message": message, "data": None},
    )


# ============================================================
# 会话存取
# ============================================================

def session_file(session_id: str) -> str:
    safe = os.path.basename(str(session_id)).strip()
    if not safe or safe in (".", ".."):
        raise ApiError(400, "卷宗编号不合法")
    return os.path.join(SESSION_DIR, f"{safe}.json")


def new_session_id() -> str:
    stamp = datetime.now().strftime("%Y-%m-%d %H_%M_%S")
    candidate = stamp
    index = 2
    while os.path.exists(session_file(candidate)):
        candidate = f"{stamp}-{index}"
        index += 1
    return candidate


def read_session(session_id: str) -> dict:
    path = session_file(session_id)
    if not os.path.exists(path):
        raise ApiError(404, "卷宗不存在或已被销毁")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error(f"卷宗读取失败 {session_id}: {exc}")
        raise ApiError(500, "卷宗损坏，无法调阅")
    if not isinstance(data, dict):
        raise ApiError(500, "卷宗损坏，无法调阅")
    return normalize_session(data, session_id)


def write_session(session_id: str, data: dict) -> None:
    data["session_id"] = session_id
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path = session_file(session_id)
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except OSError as exc:
        logger.error(f"卷宗写入失败 {session_id}: {exc}")
        raise ApiError(500, "卷宗写入失败")


def normalize_session(data: dict, session_id: str) -> dict:
    """兼容旧版存档：没有结构化 case 时，从最后的开局文本里补一份。"""
    data.setdefault("session_id", session_id)
    data.setdefault("messages", [])
    data.setdefault("case_seq", 0)
    data.setdefault("history", [])
    data.setdefault("resolved", False)
    data.setdefault("created_at", data.get("updated_at") or datetime.now().isoformat(timespec="seconds"))

    if not isinstance(data["messages"], list):
        data["messages"] = []

    if not data.get("case"):
        for message in reversed(data["messages"]):
            if message.get("role") != "assistant":
                continue
            parsed = parse_case(message.get("content", ""))
            if parsed and parsed.get("title"):
                data["case"] = parsed
                data["resolved"] = data.get("resolved") or is_verdict(message.get("content", ""))
                break

    if not data.get("resolved"):
        data["resolved"] = any(
            message.get("role") == "assistant" and is_verdict(message.get("content", ""))
            for message in data["messages"]
        )

    return data


def list_sessions() -> list[dict]:
    items: list[dict] = []
    try:
        names = [name for name in os.listdir(SESSION_DIR) if name.endswith(".json")]
    except OSError:
        return items

    for name in names:
        session_id = name[:-5]
        try:
            with open(os.path.join(SESSION_DIR, name), "r", encoding="utf-8") as handle:
                data = normalize_session(json.load(handle), session_id)
        except (OSError, json.JSONDecodeError):
            continue

        case = data.get("case") or {}
        items.append({
            "session_id": session_id,
            "title": case.get("title") or "",
            "era": case.get("era") or "",
            "era_label": case.get("era_label") or "",
            "year": case.get("year") or "",
            "type": case.get("type") or "",
            "resolved": bool(data.get("resolved")),
            "turns": sum(1 for message in data.get("messages", []) if message.get("role") == "assistant"),
            "created_at": data.get("created_at") or "",
            "updated_at": data.get("updated_at") or "",
        })

    items.sort(key=lambda item: (item.get("updated_at") or "", item.get("session_id") or ""), reverse=True)
    return items[:80]


# ============================================================
# 开局文本解析
# ============================================================

TITLE_LINE_RE = re.compile(r"^[\s]*[—–－-]{2,}\s*(.*?)\s*[—–－-]{2,}[\s]*$")
SECTION_RE = re.compile(r"^【([^】\s]{1,10})】\s*(.*)$")
SUSPECT_RE = re.compile(r"^\s*(\d{1,2})\s*[.、．)]\s*(.+)$")
VERDICT_MARK = "【真凶】"


def parse_case(text: str) -> Optional[dict]:
    """把判官的开局文本拆成时代、年份、类型、案名、案情、现场、四名嫌疑人、可查清单。"""
    if not text:
        return None

    raw = str(text).replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")

    title_index = -1
    title_match = None
    for index, line in enumerate(lines[:12]):
        matched = TITLE_LINE_RE.match(line.strip())
        if matched:
            title_index = index
            title_match = matched
            break

    era_key = ""
    era_label = ""
    year = ""
    trick = ""
    title = ""

    if title_match:
        inner = title_match.group(1).strip()
        found_title = re.search(r"《([^》]{1,30})》", inner)
        if found_title:
            title = found_title.group(1).strip()

        parts = []
        for part in re.split(r"[·・｜|]", inner):
            cleaned = re.sub(r"《.*?》", "", part).strip()
            if cleaned:
                parts.append(cleaned)

        if parts:
            era_label = parts[0]
            era_key = "ancient" if re.search(r"大靖|古代|王朝|朝", era_label) else "modern"

        rest = parts[1:]
        for part in rest:
            if re.search(r"年|世纪|年代", part):
                year = part
                break

        for part in rest:
            if part != year:
                trick = part
                break

    sections: dict[str, str] = {}
    current = None
    bucket: list[str] = []

    def flush() -> None:
        if current is not None:
            sections[current] = "\n".join(bucket).strip()
        bucket.clear()

    for line in (lines[title_index + 1:] if title_index >= 0 else lines):
        stripped = line.strip()
        matched = SECTION_RE.match(stripped)
        if matched:
            flush()
            current = matched.group(1)
            if matched.group(2).strip():
                bucket.append(matched.group(2).strip())
            continue
        if current is not None:
            bucket.append(line)
    flush()

    suspects = parse_suspects(sections.get("嫌疑人", ""))
    clues = parse_clues(sections.get("可查", ""))

    if not (title or suspects or sections):
        return None

    return {
        "era": era_key,
        "era_label": era_label,
        "year": year,
        "type": trick,
        "title": title,
        "scene": sections.get("案情", ""),
        "site": sections.get("现场", ""),
        "suspects": suspects,
        "clues": clues,
        "opened_at": datetime.now().isoformat(timespec="seconds"),
    }


def parse_suspects(block: str) -> list[dict]:
    suspects: list[dict] = []
    if not block:
        return suspects

    for line in block.split("\n"):
        stripped = line.strip().lstrip("-·•").strip()
        matched = SUSPECT_RE.match(stripped)
        if not matched:
            continue

        fields = [part.strip() for part in re.split(r"[｜|]", matched.group(2))]
        fields = [field for field in fields if field]
        if not fields:
            continue

        name = re.sub(r"[。，,、\s]+$", "", fields[0]).strip()
        name = re.sub(r"^(死者|嫌犯)[:：]?", "", name).strip()
        if not name or len(name) > 24:
            continue

        suspects.append({
            "index": int(matched.group(1)),
            "name": name,
            "identity": fields[1] if len(fields) > 1 else "",
            "relation": fields[2] if len(fields) > 2 else "",
            "motive": fields[3] if len(fields) > 3 else "",
            "detail": " · ".join(fields[1:]),
        })

        if len(suspects) >= 6:
            break

    return suspects


def parse_clues(block: str) -> list[str]:
    if not block:
        return []
    flattened = re.sub(r"\s*\n\s*", " / ", block)
    items = re.split(r"\s*[/／、；;]\s*", flattened)
    clues: list[str] = []
    for item in items:
        cleaned = re.sub(r"^[\s·•\-–—]+|[\s。.，,]+$", "", item).strip()
        # 模型偶尔会在清单后面追加一句问话，不能当成可查对象
        if not (1 <= len(cleaned) <= 24):
            continue
        if re.search(r"[？?。！!]", cleaned):
            continue
        if cleaned not in clues:
            clues.append(cleaned)
    return clues[:8]


def is_verdict(text: str) -> bool:
    return VERDICT_MARK in str(text or "")


# ============================================================
# 模型调用（OpenAI 兼容协议）
# ============================================================

def api_key() -> str:
    return os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""


def call_model(messages: list[dict], max_tokens: int = 2000) -> str:
    key = api_key()
    if not key:
        raise ModelError("未配置 DEEPSEEK_API_KEY")

    url = f"{MODEL_BASE_URL}/chat/completions"
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "stream": False,
        "temperature": 1.15,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    last_error: Optional[Exception] = None
    for attempt in (1, 2):
        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=MODEL_TIMEOUT)
            if response.status_code >= 400:
                raise ModelError(f"HTTP {response.status_code}：{response.text[:160]}")
            data = response.json()
            choices = data.get("choices") or []
            content = ((choices[0].get("message") or {}).get("content") if choices else "") or ""
            content = content.strip()
            if content:
                return content
            last_error = ModelError("判官沉默不语")
        except Exception as exc:  # 网络、鉴权、超时都归到一处
            last_error = exc
            logger.warning(f"模型调用失败（第 {attempt} 次）：{exc}")
    raise ModelError(str(last_error) if last_error else "判官沉默不语")


def to_api_messages(stored_messages: list[dict]) -> list[dict]:
    """存档里带 hidden / 前端字段，送给模型前只留 role 与 content。

    长局里只带「开局两段 + 最近 24 条」，控住上下文，又不丢案件设定。
    """
    cleaned = [
        {"role": message["role"], "content": message["content"]}
        for message in stored_messages
        if message.get("role") in ("user", "assistant") and message.get("content")
    ]
    if len(cleaned) > 26:
        cleaned = cleaned[:2] + cleaned[-24:]
    return cleaned


# ============================================================
# 案件轮换与开局
# ============================================================

def pick_brief(previous: Optional[dict]) -> tuple[str, str]:
    previous = previous or {}
    last_era = previous.get("era")
    # brief 是后台指定的诡计类型，type 是模型在标题行里写的类型，都避开
    last_type = previous.get("brief") or previous.get("type") or ""

    era_keys = list(ERAS.keys())
    if last_era in era_keys:
        other = next(key for key in era_keys if key != last_era)
        era_key = other if random.random() < 0.78 else last_era
    else:
        era_key = random.choice(era_keys)

    candidates = [trick for trick in TRICK_TYPES if trick != last_type] or TRICK_TYPES
    return era_key, random.choice(candidates)


def build_opening_prompt(era_key: str, trick: str, year_hint: str, case_seq: int, history: list[dict]) -> str:
    era = ERAS[era_key]
    if history:
        passed = "；".join(
            f"{item.get('era_label') or item.get('era') or '未知'}·{item.get('brief') or item.get('type') or '未记'}·《{item.get('title') or '无题'}》"
            for item in history[-6:]
        )
        history_line = f"\n- 此前已办案件（时代与诡计均需避开）：{passed}"
    else:
        history_line = ""

    return (
        f"{NEW_CASE_TRIGGER_PREFIX}为侦探开启第 {case_seq:02d} 号卷宗。\n"
        f"本卷强制设定（不得更改）：\n"
        f"- 时代：{era['display']}。{era['rule']}\n"
        f"- 年份：{year_hint}，标题行与案情都必须用这一年。\n"
        f"- 诡计类型：{trick}。\n"
        f"- 死者：一名；嫌疑人：恰好四名，人人有动机，仅一人是真凶。\n"
        f"- 必须可解：至少 3~5 条相互咬合的线索，能唯一指向真凶，真凶留一处无法解释的物证或时间漏洞。"
        f"{history_line}\n"
        f"严格按系统给定的“开局输出格式”作答：第一行只能是标题行"
        f"（—— {era['label']} · {year_hint} · {trick} · 《案名》 ——），标题行之前不得有任何文字；"
        f"随后依次是【案情】【现场】【嫌疑人】【可查】四节，嫌疑人恰好四行且用全角竖线“｜”分隔字段。"
        f"不要写任何分析、暗示或反问。"
    )


def open_case(session_id: str, data: dict) -> dict:
    """清空旧案并生成一桩新案；失败时返回 notice 供前端提示，不抛异常。"""
    previous = data.get("case") or None
    era_key, trick = pick_brief(previous)
    year_hint = random.choice(YEARS[era_key])
    case_seq = int(data.get("case_seq") or 0) + 1
    history = list(data.get("history") or [])

    trigger = build_opening_prompt(era_key, trick, year_hint, case_seq, history)

    opening = ""
    notice = ""
    try:
        opening = call_model(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": trigger},
            ],
            max_tokens=2200,
        )
    except ModelError as exc:
        logger.error(f"开局生成失败：{exc}")
        notice = (
            "卷宗调取失败：判官没有应召。请在项目根目录的 .env 里配置 "
            "DEEPSEEK_API_KEY（可参考 .env.example），保存后重开此案。"
            if "DEEPSEEK_API_KEY" in str(exc)
            else "卷宗调取失败：与判官的线路中断，请稍后点“重试开卷”。"
        )

    case: Optional[dict] = None
    if opening:
        case = parse_case(opening)
        if case:
            case["era"] = era_key
            case["era_label"] = case.get("era_label") or ERAS[era_key]["label"]
            case["type"] = case.get("type") or trick
            case["title"] = case.get("title") or ""
            case["brief"] = trick
        else:
            # 模型跑格式时仍保留原文，前端可以直接读开局，不丢内容
            case = {
                "era": era_key,
                "era_label": ERAS[era_key]["label"],
                "year": "",
                "type": trick,
                "brief": trick,
                "title": "",
                "scene": "",
                "site": "",
                "suspects": [],
                "clues": [],
                "opened_at": datetime.now().isoformat(timespec="seconds"),
            }

    messages: list[dict] = []
    if opening:
        messages.append({"role": "user", "content": trigger, "hidden": True})
        messages.append({"role": "assistant", "content": opening})

    if case:
        history.append({
            "era": era_key,
            "era_label": case.get("era_label") or ERAS[era_key]["label"],
            "type": case.get("type") or trick,
            "brief": trick,
            "title": case.get("title") or "",
        })
        history = history[-12:]

    data.update({
        "case_seq": case_seq,
        "case": case,
        "messages": messages,
        "resolved": False,
        "history": history,
        "created_at": data.get("created_at") or datetime.now().isoformat(timespec="seconds"),
    })
    if not opening:
        # 开局失败时不留下半截案件，重试时会重新抽取时代与诡计
        data["case"] = None
        data["case_seq"] = case_seq - 1

    write_session(session_id, data)

    return {
        "session_id": session_id,
        "opening": opening,
        "case": data.get("case"),
        "notice": notice,
        "case_seq": data.get("case_seq") or 0,
    }


# ============================================================
# 中间件与路由
# ============================================================

@app.middleware("http")
async def add_cache_headers(request: Request, call_next):
    """开发期避免浏览器缓存旧页面；静态资源靠 no-store 刷新。"""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/health")
def health() -> dict:
    return ok({
        "model": MODEL_NAME,
        "base_url": MODEL_BASE_URL,
        "api_key_configured": bool(api_key()),
    }, "判官在卷")


@app.get("/api/sessions")
def get_sessions() -> dict:
    return ok(list_sessions(), "获取卷宗库成功")


@app.post("/api/sessions")
def create_session() -> dict:
    with SESSION_LOCK:
        session_id = new_session_id()
        now = datetime.now().isoformat(timespec="seconds")
        data = {
            "session_id": session_id,
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "history": [],
            "case_seq": 0,
            "case": None,
            "resolved": False,
        }
        result = open_case(session_id, data)

    return ok(result, "新案已开")


@app.post("/api/sessions/{session_id}/restart")
def restart_session(session_id: str) -> dict:
    """换新案：旧案对话与线索全部作废，在同一条卷宗线上重开一局。"""
    with SESSION_LOCK:
        data = read_session(session_id)
        result = open_case(session_id, data)

    return ok(result, "旧卷封存，新案已开")


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    data = read_session(session_id)
    return ok({
        "session_id": data.get("session_id"),
        "case": data.get("case"),
        "resolved": bool(data.get("resolved")),
        "messages": [message for message in data.get("messages", []) if not message.get("hidden")],
        "case_seq": data.get("case_seq") or 0,
        "updated_at": data.get("updated_at") or "",
    }, "获取卷宗成功")


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str) -> dict:
    path = session_file(session_id)
    with SESSION_LOCK:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError as exc:
                logger.error(f"销毁卷宗失败 {session_id}: {exc}")
                raise ApiError(500, "卷宗销毁失败")
    return ok(None, "卷宗已销毁")


@app.post("/api/chat")
def chat(payload: ChatRequest) -> dict:
    session_id = (payload.session_id or "").strip()
    text = (payload.message or "").strip()
    if not session_id:
        raise ApiError(400, "缺少卷宗编号")
    if not text:
        raise ApiError(400, "指令不能为空")
    if len(text) > MAX_MESSAGE_LEN:
        raise ApiError(400, "指令过长，请精简后再呈交")

    data = read_session(session_id)

    # 换新案：不把旧案历史交给模型，直接重开，杜绝旧案设定残留
    if RESET_COMMAND_RE.match(text):
        with SESSION_LOCK:
            data = read_session(session_id)
            result = open_case(session_id, data)
        if not result["opening"]:
            raise ApiError(503, result["notice"] or "卷宗调取失败")
        return ok({
            "session_id": session_id,
            "reply": result["opening"],
            "case": result["case"],
            "resolved": False,
            "reset": True,
            "notice": result["notice"],
        }, "旧卷封存，新案已开")

    # 上一轮开局失败：先把卷宗补出来，再让侦探重新下令
    if not data.get("case"):
        with SESSION_LOCK:
            data = read_session(session_id)
            if not data.get("case"):
                result = open_case(session_id, data)
            else:
                result = {"opening": "", "case": data.get("case"), "notice": ""}
        if not result.get("opening"):
            raise ApiError(503, result.get("notice") or "卷宗仍未调取成功，请稍后重试")
        return ok({
            "session_id": session_id,
            "reply": result["opening"],
            "case": result["case"],
            "resolved": False,
            "reset": True,
            "notice": "卷宗刚调取完毕，请重新下令。",
        }, "卷宗已补开")

    payload_messages = (
        [{"role": "system", "content": SYSTEM_PROMPT}]
        + to_api_messages(data.get("messages", []))
        + [{"role": "user", "content": text}]
    )

    try:
        reply = call_model(payload_messages, max_tokens=1800)
    except ModelError as exc:
        logger.error(f"判官回应失败 {session_id}: {exc}")
        raise ApiError(503, "判官没有回应：线路中断或未配置密钥，请稍后重试。")

    messages = data.get("messages", [])
    messages.append({"role": "user", "content": text})
    messages.append({"role": "assistant", "content": reply})
    data["messages"] = messages
    data["resolved"] = bool(data.get("resolved")) or is_verdict(reply)
    write_session(session_id, data)

    return ok({
        "session_id": session_id,
        "reply": reply,
        "case": data.get("case"),
        "resolved": bool(data.get("resolved")),
        "reset": False,
        "notice": "",
    }, "交互成功")


# ============================================================
# 统一错误信封
# ============================================================

@app.exception_handler(ApiError)
async def handle_api_error(request: Request, exc: ApiError):
    return fail(exc.status, exc.message)


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(request: Request, exc: StarletteHTTPException):
    messages = {
        400: "请求有误",
        404: "这条路不通，回去看卷宗吧",
        405: "判官不接这个动作",
        413: "呈文太长，判官看不完",
    }
    detail = exc.detail
    if isinstance(detail, str) and detail not in ("Not Found", "Method Not Allowed"):
        message = detail
    else:
        message = messages.get(exc.status_code, "请求失败")
    return fail(exc.status_code, message)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    return fail(422, "请求格式不正确")


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception):
    logger.exception(f"未处理异常 {request.url.path}: {exc}")
    return fail(500, "卷宗房出了点岔子，请稍后重试")


# ============================================================
# 启动
# ============================================================

def check_session_dir() -> None:
    """启动前自查存档目录；不可写时给出可操作的提示，而不是等到开局才 500。"""
    try:
        probe = os.path.join(SESSION_DIR, ".write_probe")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
    except OSError as exc:
        logger.error(
            f"卷宗目录不可写：{SESSION_DIR}（{exc}）。"
            f"请给该目录写权限，或设置环境变量 SESSION_DIR 指向一个可写目录。"
        )


if __name__ == "__main__":
    check_session_dir()
    logger.info(f"悬案录开卷：http://{HOST}:{PORT}（存档目录 {SESSION_DIR}）")
    try:
        uvicorn.run(app, host=HOST, port=PORT)
    except OSError as exc:
        logger.error(f"端口 {PORT} 无法监听：{exc}。可设置环境变量 PORT 换一个端口，例如 PORT=8788。")
