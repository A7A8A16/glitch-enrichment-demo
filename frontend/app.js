// Same-origin in Vercel; local development keeps using the local API server.
const API_BASE = window.location.hostname === "127.0.0.1" || window.location.hostname === "localhost"
  ? "http://127.0.0.1:8000"
  : "/api";

const screens = {
  start: document.querySelector("#screen-start"),
  question: document.querySelector("#screen-question"),
  sensing: document.querySelector("#screen-sensing"),
  task: document.querySelector("#screen-task"),
};
const statusText = document.querySelector("#statusText");
const apiStatus = document.querySelector("#apiStatus");
const progressSteps = [...document.querySelectorAll(".progress-step")];
let currentQuestion = null;
let currentState = null;

function setScreen(name, step) {
  Object.entries(screens).forEach(([key, element]) => {
    const visible = key === name;
    element.hidden = !visible;
    element.classList.toggle("is-visible", visible);
  });
  progressSteps.forEach((element) => element.classList.toggle("is-active", element.dataset.step === String(step)));
}

function setStatus(text, busy = false) {
  statusText.textContent = text;
  document.querySelector(".live-status i").style.background = busy ? "#c9ff4a" : "#ff8c65";
}

function buildFallbackState() {
  const now = new Date();
  return {
    timestamp: now.toISOString(),
    platform: /Android/i.test(navigator.userAgent) ? "Android" : "Web",
    browser: navigator.userAgent.includes("Chrome") ? "Chrome" : "Browser",
    motion_state: "still",
    environment_noise: "quiet",
    activity_state: "likely_resting",
    city: "",
  };
}

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function setSensorStatus(id, text, type = "") {
  const element = document.querySelector(id);
  element.textContent = text;
  element.className = type;
}

function displayMotion(value) {
  return ({ still: "静止", moving: "移动中", active: "活动明显", unknown: "未知" })[value] || value || "未知";
}

function displayNoise(value) {
  return ({ quiet: "安静", normal: "普通", noisy: "嘈杂", unknown: "未知" })[value] || value || "未知";
}

function requestMotionPermission() {
  const Motion = window.DeviceMotionEvent;
  const Orientation = window.DeviceOrientationEvent;
  const promises = [];
  if (Motion && typeof Motion.requestPermission === "function") promises.push(Motion.requestPermission());
  if (Orientation && typeof Orientation.requestPermission === "function") promises.push(Orientation.requestPermission());
  return promises.length ? Promise.allSettled(promises) : Promise.resolve([]);
}

function getLocation() {
  if (!navigator.geolocation) return Promise.resolve({ coords: null, reason: "当前浏览器不支持定位" });
  return new Promise((resolve) => navigator.geolocation.getCurrentPosition(
    (position) => resolve({ coords: position.coords, reason: null }),
    (error) => {
      const reason = error.code === error.PERMISSION_DENIED
        ? "定位权限未授权"
        : error.code === error.TIMEOUT
          ? "定位超时"
          : "暂时无法获取定位";
      resolve({ coords: null, reason });
    },
    { enableHighAccuracy: false, timeout: 8000, maximumAge: 300000 },
  ));
}

async function sampleAudio() {
  if (!navigator.mediaDevices?.getUserMedia) return null;
  let stream;
  let context;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    context = new AudioContext();
    const source = context.createMediaStreamSource(stream);
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    source.connect(analyser);
    const data = new Uint8Array(analyser.fftSize);
    await delay(1200);
    analyser.getByteTimeDomainData(data);
    const rms = Math.sqrt(data.reduce((sum, value) => sum + ((value - 128) / 128) ** 2, 0) / data.length);
    const db = Math.max(-80, Math.min(0, 20 * Math.log10(Math.max(rms, 0.0001))));
    return db;
  } catch {
    return null;
  } finally {
    stream?.getTracks().forEach((track) => track.stop());
    await context?.close();
  }
}

async function collectSensorState() {
  const motionValues = [];
  const motionHandler = (event) => {
    const a = event.accelerationIncludingGravity || event.acceleration;
    if (a && [a.x, a.y, a.z].every((value) => typeof value === "number")) {
      motionValues.push(Math.hypot(a.x, a.y, a.z));
    }
  };
  window.addEventListener("devicemotion", motionHandler);

  // These requests must be created synchronously within the button click flow for iOS Safari.
  const motionPermission = requestMotionPermission();
  const audioPromise = sampleAudio();
  const locationPromise = getLocation();
  setSensorStatus("#motionStatus", "正在采样");
  setSensorStatus("#audioStatus", "正在采样");
  setSensorStatus("#locationStatus", "正在定位");

  await motionPermission;
  const [noiseDb, locationResult] = await Promise.all([audioPromise, locationPromise, delay(900)]).then(([audio, geo]) => [audio, geo]);
  const location = locationResult.coords;
  window.removeEventListener("devicemotion", motionHandler);

  const mean = motionValues.length ? motionValues.reduce((sum, value) => sum + value, 0) / motionValues.length : null;
  const variance = mean == null ? null : motionValues.reduce((sum, value) => sum + (value - mean) ** 2, 0) / motionValues.length;
  const motionState = variance == null ? "unknown" : variance < 0.08 ? "still" : variance < 1 ? "moving" : "active";
  const noiseLevel = noiseDb == null ? "unknown" : noiseDb < -45 ? "quiet" : noiseDb < -25 ? "normal" : "noisy";

  setSensorStatus("#motionStatus", motionState === "unknown" ? "未获得数据" : "已感知", motionState === "unknown" ? "is-fallback" : "is-ready");
  setSensorStatus("#audioStatus", noiseDb == null ? "未获得数据" : "已感知", noiseDb == null ? "is-fallback" : "is-ready");
  setSensorStatus("#locationStatus", location ? "已感知" : locationResult.reason, location ? "is-ready" : "is-fallback");

  return {
    ...buildFallbackState(),
    motion_state: motionState,
    environment_noise: noiseLevel,
    activity_state: motionState === "still" ? "likely_resting" : "active",
    // Web Audio gives a relative negative dBFS value; convert to a non-negative
    // approximate level for the backend schema without uploading audio.
    noise_db: noiseDb == null ? undefined : Number(Math.max(0, 60 + noiseDb).toFixed(1)),
    location_accuracy_meter: location ? Number(location.accuracy.toFixed(0)) : undefined,
    _location: location ? { latitude: location.latitude, longitude: location.longitude } : null,
    _locationError: locationResult.reason,
  };
}

async function loadLocationContext(state) {
  if (!state._location) {
    document.querySelector("#contextCity").textContent = state._locationError || "未获取定位";
    document.querySelector("#contextWeather").textContent = "天气待定位";
    document.querySelector("#contextMotion").textContent = `动作：${displayMotion(state.motion_state)}`;
    document.querySelector("#contextNoise").textContent = `声音：${displayNoise(state.environment_noise)}`;
    const { _location, _locationError, ...safeState } = state;
    return safeState;
  }
  try {
    const context = await post("/location-context", state._location);
    document.querySelector("#contextCity").textContent = context.city || "当前位置";
    const weather = context.available
      ? `${context.weather || "天气"} ${context.temperature ? `${context.temperature}℃` : ""}`.trim()
      : "天气查询失败";
    document.querySelector("#contextWeather").textContent = weather;
    document.querySelector("#contextMotion").textContent = `动作：${displayMotion(state.motion_state)}`;
    document.querySelector("#contextNoise").textContent = `声音：${displayNoise(state.environment_noise)}`;
    const { _location, _locationError, ...safeState } = state;
    return { ...safeState, city: context.city || undefined, weather: context.weather || undefined, temperature: context.temperature || undefined, wind: context.wind || undefined };
  } catch {
    document.querySelector("#contextCity").textContent = "当前位置";
    document.querySelector("#contextWeather").textContent = "天气查询失败";
    document.querySelector("#contextMotion").textContent = `动作：${displayMotion(state.motion_state)}`;
    document.querySelector("#contextNoise").textContent = `声音：${displayNoise(state.environment_noise)}`;
    const { _location, _locationError, ...safeState } = state;
    return safeState;
  }
}

async function post(path, body) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`接口请求失败（${response.status}）`);
  return response.json();
}

async function loadQuestion() {
  const button = document.querySelector("#startButton");
  button.disabled = true;
  button.innerHTML = "正在感知 <span>…</span>";
  setStatus("正在连接", true);
  document.querySelector("#questionError").textContent = "";
  setScreen("sensing", 1);
  try {
    const sensedState = await collectSensorState();
    currentState = await loadLocationContext(sensedState);
    currentQuestion = await post("/profile-question", { state: currentState });
    document.querySelector("#questionText").textContent = currentQuestion.question;
    document.querySelector("#contextTime").textContent = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    setScreen("question", 2);
    setStatus("已感知", false);
  } catch (error) {
    setScreen("start", 1);
    document.querySelector("#questionError").textContent = `${error.message}。请确认后端已启动。`;
    setStatus("连接失败", false);
  } finally {
    button.disabled = false;
    button.innerHTML = "开始感知 <span>↗</span>";
  }
}

async function submitAnswer() {
  const button = document.querySelector("#submitButton");
  const answer = document.querySelector("#answerInput").value;
  button.disabled = true;
  button.innerHTML = "正在生成 <span>…</span>";
  setStatus("正在生成", true);
  document.querySelector("#taskError").textContent = "";
  try {
    const result = await post("/enrichment-task", { state: currentState || buildFallbackState(), question_id: currentQuestion.question_id, question: currentQuestion.question, answer });
    document.querySelector("#taskText").textContent = result.task;
    document.querySelector("#taskReason").textContent = result.reason;
    document.querySelector("#taskDuration").textContent = `${result.duration_minutes} 分钟`;
    setScreen("task", 3);
    setStatus("已生成", false);
  } catch (error) {
    document.querySelector("#taskError").textContent = `${error.message}。请稍后再试。`;
    setStatus("生成失败", false);
  } finally {
    button.disabled = false;
    button.innerHTML = "继续 <span>→</span>";
  }
}

document.querySelector("#startButton").addEventListener("click", loadQuestion);
document.querySelector("#submitButton").addEventListener("click", submitAnswer);
document.querySelector("#againButton").addEventListener("click", () => setScreen("question", 2));
document.querySelector("#doneButton").addEventListener("click", () => {
  setStatus("完成一小步", false);
  document.querySelector("#doneButton").textContent = "已接受 ✓";
  document.querySelector("#doneButton").disabled = true;
});
document.querySelector("#answerInput").addEventListener("input", (event) => {
  document.querySelector("#charCount").textContent = `${event.target.value.length} / 80`;
});

fetch(`${API_BASE}/health`).then((response) => {
  if (response.ok) apiStatus.textContent = "API · 已连接";
}).catch(() => { apiStatus.textContent = "API · 等待连接"; });
