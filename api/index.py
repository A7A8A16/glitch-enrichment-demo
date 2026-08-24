"""Vercel ASGI entry point for Glitch. backend/app.py content inlined for reliability."""
from __future__ import annotations
import os
import sys
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Annotated, Any, Literal
import httpx
import logging
import re
import time
import urllib.parse
import random
import json as _json

logger = logging.getLogger("glitch")
logger.setLevel(logging.INFO)

# === backend/app.py 内容开始 ===
"""Glitch backend: two-step profile question and enrichment task generation."""


import json
import math
import os
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


class State(BaseModel):
    timestamp: str | None = None
    platform: str | None = None
    browser: str | None = None
    motion_state: str | None = None
    environment_noise: str | None = None
    activity_state: str | None = None
    noise_db: float | None = Field(default=None, ge=0, le=160)
    speed_mps: float | None = Field(default=None, ge=0)
    location_accuracy_meter: float | None = Field(default=None, ge=0)
    city: str | None = None
    weather: str | None = None
    temperature: str | None = None
    wind: str | None = None


# ---------- 传感器采集上报（前端原始数据 -> State） ---------- #


class MotionSample(BaseModel):
    """devicemotion 采样（含重力加速度 m/s² + 旋转速率 度/s）。可选。"""

    ax: float | None = Field(default=None)
    ay: float | None = Field(default=None)
    az: float | None = Field(default=None)
    rx: float | None = Field(default=None)
    ry: float | None = Field(default=None)
    rz: float | None = Field(default=None)


class OrientationSample(BaseModel):
    """deviceorientation 欧拉角（度）。可选。"""

    alpha: float | None = None
    beta: float | None = None
    gamma: float | None = None


class GeolocationSample(BaseModel):
    """GPS 定位结果。可选。latitude/longitude 为十进制度，accuracy 为米。"""

    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    accuracy: float | None = Field(default=None, ge=0)


class PermissionFlags(BaseModel):
    """各权限的最终授权状态，便于后端做状态降级评估。"""

    device_motion: str | None = None  # granted / denied / not-required / unsupported / error
    device_orientation: str | None = None
    media_microphone: str | None = None
    geolocation: str | None = None


class SensorSnapshot(BaseModel):
    """前端上报的一帧传感器快照，由 /collect-state 推导为 State。"""

    timestamp: str | None = None  # 前端采集时的 ISO 时间；为空则用服务端时间
    motion: MotionSample | None = None
    orientation: OrientationSample | None = None
    geolocation: GeolocationSample | None = None
    permissions: PermissionFlags | None = None

    # 前端通过 Web Audio API 计算的噪音分贝（A计权近似，dBFS→估算dB SPL）。
    # 可选：未授权/不支持则为 null。
    noise_db: float | None = Field(default=None, ge=0, le=160)

    # 可选：前端用 watchPosition 或多次采样估算的移动速度（米/秒）。
    speed_mps: float | None = Field(default=None, ge=0)

    # 可选：前端通过其他途径（例如浏览器地理 API 或用户自主上报）得到的城市名、天气。
    city: str | None = None
    weather: str | None = None


class LocationContextRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class LocationContext(BaseModel):
    city: str | None = None
    weather: str | None = None
    temperature: str | None = None
    wind: str | None = None
    humidity: str | None = None
    available: bool = False


# ---------- UA 解析 ---------- #


def parse_user_agent(ua: str | None) -> tuple[str | None, str | None]:
    """解析 User-Agent → (platform, browser)。只做粗粒度分类。"""

    if not ua:
        return None, None

    ua_lower = ua.lower()

    # Platform
    if "iphone" in ua_lower or "ipad" in ua_lower or "ipod" in ua_lower:
        platform = "iOS"
    elif "android" in ua_lower:
        platform = "Android"
    elif "mac os x" in ua_lower or "macintosh" in ua_lower:
        platform = "macOS"
    elif "windows" in ua_lower:
        platform = "Windows"
    elif "linux" in ua_lower and "android" not in ua_lower:
        platform = "Linux"
    else:
        platform = None

    # Browser
    if "edg/" in ua_lower or "edge/" in ua_lower:
        browser = "Edge"
    elif "opr/" in ua_lower or "opera" in ua_lower:
        browser = "Opera"
    elif "firefox" in ua_lower or "fxios" in ua_lower:
        browser = "Firefox"
    elif "safari" in ua_lower and "chrome" not in ua_lower and "chromium" not in ua_lower:
        browser = "Safari"
    elif "chrome" in ua_lower or "crios" in ua_lower:
        browser = "Chrome"
    elif "micromessenger" in ua_lower:
        browser = "WeChat"
    elif "qqbrowser" in ua_lower:
        browser = "QQBrowser"
    else:
        browser = None

    return platform, browser


# ---------- 状态推导函数 ---------- #


def derive_motion_state(motion: MotionSample | None, permission: str | None) -> str | None:
    """从含重力加速度向量推导运动状态。

    - still: 三轴合成值接近 1g（9.8m/s²），且波动 < 1.2 m/s²
    - walking: 波动中等
    - moving: 波动较大或旋转明显
    - unavailable: 权限不支持/被拒绝 / 数据缺失
    """

    if permission in {"denied", "unsupported", "error"}:
        return "unavailable"
    if motion is None:
        return None
    if motion.ax is None and motion.ay is None and motion.az is None:
        return None

    vals = [v for v in (motion.ax, motion.ay, motion.az) if v is not None]
    if not vals:
        return None

    magnitude = math.sqrt(sum(v * v for v in vals))
    # 重力偏差（|1g - 实际|）+ 三轴方差近似（用 max-min 粗略估计）
    gravity_deviation = abs(magnitude - 9.81)
    spread = max(vals) - min(vals)

    rotation_present = any(
        abs(v) > 30 for v in (motion.rx, motion.ry, motion.rz) if v is not None
    )

    if gravity_deviation < 1.2 and spread < 1.5 and not rotation_present:
        return "still"
    if gravity_deviation < 4.0 and spread < 5.0 and not rotation_present:
        return "walking"
    return "moving"


def derive_environment_noise(
    noise_db: float | None, mic_permission: str | None
) -> str | None:
    """从估算分贝或麦克风授权状态推导环境噪音等级。"""

    if mic_permission in {"denied", "unsupported", "error"}:
        return "unavailable"
    if noise_db is None:
        return None
    if noise_db < 45:
        return "quiet"
    if noise_db < 70:
        return "moderate"
    return "loud"


def derive_activity_state(
    motion_state: str | None,
    speed_mps: float | None,
) -> str | None:
    """综合 motion + 速度推导活动状态。"""

    if speed_mps is not None:
        if speed_mps < 0.3 and (motion_state != "moving"):
            return "likely_resting"
        if speed_mps < 2.0:
            return "walking_outdoors"
        if speed_mps < 9.0:
            return "cycling_or_running"
        return "commuting"

    # 没有速度时退回 motion 推断
    if motion_state == "still":
        return "likely_resting"
    if motion_state == "walking":
        return "walking_outdoors"
    if motion_state == "moving":
        return "on_the_go"
    return None


class ProfileQuestionRequest(BaseModel):
    state: State


class ProfileQuestion(BaseModel):
    question_id: str
    question: str
    placeholder: str = "用一句话告诉我（可留空）"
    signal_basis: list[str] = Field(default_factory=list)


class EnrichmentTaskRequest(BaseModel):
    state: State
    question_id: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=300)
    answer: str = Field(default="", max_length=80)


class EnrichmentTask(BaseModel):
    task: str
    reason: str
    duration_minutes: int = Field(ge=1, le=30)
    profile_tag: Literal[
        "low_mood_or_low_expression", "answer_grounded", "playful_unpredictable"
    ]


QUESTION_FALLBACKS = [
    "如果现在可以做一点不同的事，你最想打破什么？",
    "此刻的你，更想被什么样的小变化打扰一下？",
]
TASK_FALLBACKS = {
    "low_mood_or_low_expression": [
        "把手机放下两分钟，喝几口水，再回来看看身边最柔软的东西。",
        "慢慢走到窗边或门口，做三次深呼吸，观察一个你平时忽略的细节。",
        "把肩膀放松下来，听一首熟悉的歌，只注意其中一个乐器的声音。",
        "找一个舒服的位置坐好，闭眼十秒，再说出眼前看到的三种颜色。",
        "给自己倒一杯水，慢慢喝完，然后把桌面上一个物品放到新位置。",
        "暂时离开屏幕一分钟，摸一摸身边最有纹理的物品。",
        "写下一件今天已经完成的小事，不需要评价它做得好不好。",
    ],
    "answer_grounded": [
        "找一件你平时不会注意的小物件，给它写一句不超过十个字的旁白。",
        "沿着平时路线走三分钟，选择一个从未拍过的角度观察周围。",
        "从当前房间里选一个物品，换一个完全不同的用途使用三分钟。",
        "打开窗户或走到门口，记录你听到的三种不同声音。",
        "把今天常用的一件东西放到不常放的位置，看看你是否会立刻发现。",
        "找一个平时忽略的角落，给它拍一张不用于发布的照片。",
        "用非惯用手写一句话，内容是你此刻最想尝试的小变化。",
    ],
    "playful_unpredictable": [
        "给身边一个普通物品起一个夸张的名字，并为它编一个秘密身份。",
        "寻找三种不同形状的东西，把它们排成一个只属于今天的图案。",
        "闭眼随机指向一个安全的物品，为它编一个十秒钟的新闻播报。",
        "在房间里找出三件同色物品，给它们组成一个临时乐队。",
        "把最近看到的一个词和眼前的一个物品拼在一起，创造一个新词。",
        "用一句话向一件物品介绍今天的自己，语气要像科幻电影。",
        "找一个圆形物品，想象它是来自未来的纪念品，并写下它的来历。",
    ],
}


def classify_answer(answer: str) -> str:
    """Classify only for task personalization; never expose the label to users."""
    cleaned = answer.strip()
    if not cleaned:
        return "low_mood_or_low_expression"
    compact = re.sub(r"[\W_]+", "", cleaned, flags=re.UNICODE)
    if len(compact) < 2 or re.fullmatch(r"(.)\1{2,}", compact):
        return "playful_unpredictable"
    if len(compact) >= 3 and re.fullmatch(r"[啊哈呵嘿呜呀哦哎额嗯笑]+", compact):
        return "playful_unpredictable"
    meaningless = {"啊啊啊", "哈哈哈", "不知道", "随便", "无", "测试", "asdf", "123"}
    if cleaned.lower() in meaningless or len(compact) <= 2:
        return "playful_unpredictable"
    return "answer_grounded"


def state_for_prompt(state: State) -> dict[str, Any]:
    return state.model_dump(exclude_none=True)


class DeepSeekClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self.timeout = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "10"))

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def complete_json(self, system: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
        body = {
            "model": self.model,
            "temperature": 0.8,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
            )
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)


QUESTION_SYSTEM = """你是 Glitch 的温柔提问设计师。根据用户当下的脱敏状态，生成一个适合获取当下心情或偏好的单一问题。
必须阅读并使用传入的天气/温度、动作状态、声音环境，问题中至少自然体现其中两项；unknown 或缺失的信号不得编造。不要只写泛化的心情问题。问题要轻松、非医疗、非诊断，不索取隐私。
只输出 JSON：question_id、question、placeholder、signal_basis。signal_basis 是实际使用的信号名数组。"""
TASK_SYSTEM = """你是 Glitch 的丰容任务设计师。根据当前脱敏环境状态、用户回答及画像标签，生成一个 2-10 分钟内可完成的安全微小任务。
必须综合使用天气/温度、动作状态、声音环境，至少使用其中两项，并在 reason 中说明它们如何影响任务；unknown 或缺失的信号不得编造。空回答意味着用户可能心情不佳，语气温和低门槛；无意义回答意味着古灵精怪、接受意外，但仍须安全。
不得要求花钱、危险动作、骚扰他人、上传隐私或持续记录。只输出 JSON：task、reason、duration_minutes。"""


client = DeepSeekClient()
app = FastAPI(title="Glitch Enrichment API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.get("/", include_in_schema=False)
async def frontend_index():
    from fastapi.responses import FileResponse
    return FileResponse(FRONTEND_DIR / "index.html", media_type="text/html")


@app.get("/app.js", include_in_schema=False)
async def frontend_script():
    from fastapi.responses import FileResponse
    return FileResponse(FRONTEND_DIR / "app.js", media_type="application/javascript")


@app.get("/styles.css", include_in_schema=False)
async def frontend_styles():
    from fastapi.responses import FileResponse
    return FileResponse(FRONTEND_DIR / "styles.css", media_type="text/css")


@app.get("/challenge.html", include_in_schema=False)
async def challenge_page():
    from fastapi.responses import FileResponse
    return FileResponse(FRONTEND_DIR / "challenge.html", media_type="text/html")


@app.get("/challenge-mock.js", include_in_schema=False)
async def challenge_mock_script():
    from fastapi.responses import FileResponse
    return FileResponse(FRONTEND_DIR / "challenge-mock.js", media_type="application/javascript")


@app.get("/assets/{asset_path:path}", include_in_schema=False)
async def frontend_asset(asset_path: str):
    """Serve bundled pet artwork while keeping the path inside frontend/assets."""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    root = (FRONTEND_DIR / "assets").resolve()
    target = (root / asset_path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(target)


@app.middleware("http")
async def strip_vercel_api_prefix(request: Request, call_next):
    """Vercel routes /api/* to this function; expose the same paths locally."""
    path = request.scope.get("path", "")
    if path == "/api" or path.startswith("/api/"):
        request.scope["path"] = path[4:] or "/"
    return await call_next(request)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "llm_enabled": client.enabled, "amap_enabled": bool(os.getenv("AMAP_WEB_KEY"))}


@app.post("/location-context", response_model=LocationContext)
async def location_context(request: LocationContextRequest) -> LocationContext:
    """用高德临时查询城市和天气；不保存坐标。"""
    key = os.getenv("AMAP_WEB_KEY", "")
    if not key:
        return LocationContext()
    try:
        async with httpx.AsyncClient(timeout=8) as http:
            regeo = await http.get(
                "https://restapi.amap.com/v3/geocode/regeo",
                params={"key": key, "location": f"{request.longitude},{request.latitude}", "extensions": "base"},
            )
            regeo.raise_for_status()
            regeo_data = regeo.json()
            regeocode = regeo_data.get("regeocode") or {}
            address = regeocode.get("addressComponent") or {}
            city = address.get("city") or address.get("province") or address.get("district")
            adcode = address.get("adcode")
            if not adcode:
                return LocationContext(city=city)
            weather = await http.get(
                "https://restapi.amap.com/v3/weather/weatherInfo",
                params={"key": key, "city": adcode, "extensions": "base"},
            )
            weather.raise_for_status()
            lives = (weather.json().get("lives") or [{}])[0]
            return LocationContext(
                city=city,
                weather=lives.get("weather"),
                temperature=lives.get("temperature"),
                wind=(f"{lives.get('winddirection', '')}风 {lives.get('windpower', '')}级").strip(),
                humidity=(f"{lives['humidity']}%" if lives.get("humidity") else None),
                available=True,
            )
    except Exception:
        return LocationContext()


@app.post("/collect-state", response_model=State)
async def collect_state(
    snapshot: SensorSnapshot,
    request: Request,
    user_agent: str | None = Header(default=None, alias="user-agent"),
) -> State:
    """接收前端传感器快照，解析 UA、推导状态，返回可直接用于 profile-question 的 State。"""

    platform, browser = parse_user_agent(user_agent)

    permissions = snapshot.permissions or PermissionFlags()

    motion_state = derive_motion_state(snapshot.motion, permissions.device_motion)
    environment_noise = derive_environment_noise(snapshot.noise_db, permissions.media_microphone)
    speed_mps = snapshot.speed_mps
    activity_state = derive_activity_state(motion_state, speed_mps)

    timestamp = snapshot.timestamp
    if not timestamp:
        timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    state = State(
        timestamp=timestamp,
        platform=platform,
        browser=browser,
        motion_state=motion_state,
        environment_noise=environment_noise,
        activity_state=activity_state,
        noise_db=snapshot.noise_db,
        speed_mps=speed_mps,
        city=snapshot.city,
        weather=snapshot.weather,
        temperature=None,
        wind=None,
    )
    return state


@app.post("/profile-question", response_model=ProfileQuestion)
async def profile_question(request: ProfileQuestionRequest) -> ProfileQuestion:
    try:
        result = await client.complete_json(QUESTION_SYSTEM, {"state": state_for_prompt(request.state)})
        return ProfileQuestion.model_validate(result)
    except Exception:
        return ProfileQuestion(question_id="break_routine", question=random.choice(QUESTION_FALLBACKS))


@app.post("/enrichment-task", response_model=EnrichmentTask)
async def enrichment_task(request: EnrichmentTaskRequest) -> EnrichmentTask:
    tag = classify_answer(request.answer)
    payload = {
        "state": state_for_prompt(request.state),
        "profile_question": {"id": request.question_id, "text": request.question},
        "user_answer": request.answer.strip(),
        "profile_tag": tag,
    }
    try:
        result = await client.complete_json(TASK_SYSTEM, payload)
        return EnrichmentTask.model_validate({**result, "profile_tag": tag})
    except Exception:
        return EnrichmentTask(
            task=random.choice(TASK_FALLBACKS[tag]),
            reason="根据你此刻的状态，给生活加入一个很小的偏差。",
            duration_minutes=3,
            profile_tag=tag,
        )


# === backend/app.py 内容结束 ===
__all__ = ["app"]
