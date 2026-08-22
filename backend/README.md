# Glitch 后端

## 两次模型调用

- `POST /profile-question`：接收前端脱敏状态，生成第 4 页的一句话问题。
- `POST /enrichment-task`：接收状态、问题和用户回答，生成第 5 页任务。
- `POST /location-context`：临时调用高德逆地理编码和天气接口，返回城市、天气、温度和风力；不保存坐标。

未配置 `DEEPSEEK_API_KEY` 或模型请求失败时，服务自动使用本地备用问题/任务，便于前端联调和现场演示。

## 请求示例

```json
POST /profile-question
{
  "state": {
    "timestamp": "2026-08-21T21:30:56+08:00",
    "platform": "Android",
    "browser": "Chrome",
    "motion_state": "still",
    "environment_noise": "quiet",
    "activity_state": "likely_resting",
    "city": "南京",
    "weather": "clear"
  }
}
```

```json
POST /enrichment-task
{
  "state": {"motion_state": "still", "environment_noise": "quiet", "city": "南京"},
  "question_id": "break_routine",
  "question": "如果现在可以做一点不同的事，你最想打破什么？",
  "answer": "重复刷信息"
}
```

## 启动

```powershell
python -m pip install -r backend/requirements.txt
$env:DEEPSEEK_API_KEY = "你的 Key"
python -m uvicorn backend.app:app --reload
```

## 运行测试

测试不会调用 DeepSeek，而是模拟模型不可用，验证本地降级和回答分类逻辑：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend -p "test_*.py" -v
```
