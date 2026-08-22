# Glitch · 人类丰容计划

黑客松项目：用算法对抗算法，给重复的日常加一点"野生的小意外"。

## 跑起来

- **纯前端演示**：双击 `frontend/index.html`（无后端时自动进入静态演示模式）
- **真实数据**（LLM 问题 + 高德实时天气）：

```powershell
python -m pip install -r backend/requirements.txt
$env:DEEPSEEK_API_KEY = "你的 Key"
$env:AMAP_WEB_KEY = "你的高德 Key"   # 可选
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

然后打开 `frontend/index.html`，启动页显示「数据在线」= 后端已接通。

## 结构

- `frontend/` 单文件手帐风 App（启动/授权/感知/首页/挑战/宠物/我的/分享页）
- `backend/` FastAPI（DeepSeek 生成问题+任务、高德天气、传感器状态推导）
- `api/index.py` Vercel ASGI 入口
- 详细说明见 `交付说明.txt`
