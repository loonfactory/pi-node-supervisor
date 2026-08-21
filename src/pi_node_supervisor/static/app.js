const state = { dashboard: null, runs: [], events: [], eventsTotal: 0, eventsOffset: 0, eventsPageSize: 25, data: [], dataTotal: 0, dataOffset: 0, dataPageSize: 25, sensorData: [], sensorDelivery: { counts: {}, items: [] }, sensorSettings: [], sensorMirrorDraft: [], nodes: [], preview: null, configuration: null, settingsLoaded: false, cameraConfigs: {}, storageDraft: [], captureSchedule: null, sensorSchedule: null };
const previewUrls = new Map();
const previewStreams = new Map();
let dataViewUrl = null;
const DEFAULT_CAPTURE_RANGE = { start: "09:00", intervalMinutes: 10, end: "18:00" };
const DEFAULT_SENSOR_RANGE = { start: "00:00", intervalMinutes: 30, end: "23:30" };
const $ = (selector) => document.querySelector(selector);
const token = () => sessionStorage.getItem("supervisor-token") || "";

function friendlyError(error) {
  const message = error?.message || String(error);
  return error instanceof TypeError && /fetch|network/i.test(message)
    ? "장치 연결이 잠시 끊겼습니다. 잠시 후 다시 갱신해 주세요."
    : message;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (token()) headers["X-Supervisor-Token"] = token();
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(path, { ...options, headers });
  } catch (error) {
    throw new Error(friendlyError(error));
  }
  let body = null;
  try { body = await response.json(); } catch { body = {}; }
  if (!response.ok) {
    if (response.status === 401) $("#token-dialog").showModal();
    const detail = Array.isArray(body.detail)
      ? body.detail.map(item => `${(item.loc || []).slice(1).join(" › ")}: ${item.msg}`).join(" · ")
      : body.detail;
    const error = new Error(detail || body.code || `요청 오류 (HTTP ${response.status})`);
    error.status = response.status;
    throw error;
  }
  return body;
}

function toast(message, error = false) {
  if (error) console.warn("[Pi Node Supervisor]", message);
  const element = $("#toast");
  element.textContent = message;
  element.className = error ? "show error" : "show";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.className = "", 3200);
}

function fmt(value) {
  if (!value) return "—";
  const date = new Date(value);
  return esc(Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString());
}
function esc(value) { return String(value).replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]); }
function safe(value) { return value == null ? "—" : esc(value); }
function status(value) {
  const labels = {
    healthy: "정상", unhealthy: "확인 필요", running: "실행 중", succeeded: "완료",
    failed: "실패", interrupted: "중단됨", missed: "놓침", info: "안내",
    warning: "주의", error: "오류", pending: "대기", not_queued: "미전송",
    synchronized: "동기화", "local-only": "로컬만",
  };
  return `<span class="status ${value}">${safe(labels[value] || value)}</span>`;
}
function table(headers, rows) {
  if (!rows.length) return '<div class="empty">표시할 항목이 없습니다.</div>';
  return `<table><thead><tr>${headers.map(h => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table>`;
}

function powerWindowLabels(window, activities = []) {
  const activityById = new Map(activities.map(item => [item.activity_id, item]));
  const items = window.activity_ids.map(id => activityById.get(id)).filter(Boolean);
  const cameras = [...new Set(items.filter(item => item.activity_type === "capture-schedule").map(item => item.node_id))];
  const recurringCount = items.filter(item => item.activity_type === "capture-schedule").length;
  const oneShots = items.filter(item => item.activity_type !== "capture-schedule").map(item => item.activity_id);
  return [
    cameras.length ? `카메라 ${cameras.join(", ")}` : "",
    recurringCount ? `반복 촬영 ${recurringCount}건` : "",
    ...oneShots,
  ].filter(Boolean);
}

function isRecurringPowerWindow(window, activities = []) {
  const activityById = new Map(activities.map(item => [item.activity_id, item]));
  const items = window.activity_ids.map(id => activityById.get(id)).filter(Boolean);
  return items.length > 0 && items.every(item => item.activity_type === "capture-schedule");
}

function powerWindowStateLabel(window, activities = []) {
  if (isRecurringPowerWindow(window, activities)) return "반복 스케줄 · 계획 자동 연장";
  return window.automatic_shutdown_possible
    ? `RTC 다음 기동 ${fmt(window.next_power_on)}`
    : "다음 기동 없음 · 자동 종료 보류";
}

function koreanClock(value) {
  const [hour, minute, second = "00"] = String(value).split(":");
  const numericHour = Number(hour);
  const period = numericHour < 12 ? "오전" : "오후";
  const displayHour = numericHour % 12 || 12;
  return `${period} ${String(displayHour).padStart(2, "0")}:${minute}:${second}`;
}

function durationLabel(seconds) {
  const parts = splitCaptureInterval(seconds);
  return [[parts.days, "일"], [parts.hours, "시간"], [parts.minutes, "분"], [parts.seconds, "초"]]
    .filter(([value]) => Number(value) > 0)
    .map(([value, unit]) => `${value}${unit}`)
    .join(" ") || "0초";
}

function captureScheduleLines(schedule) {
  if (schedule.mode === "interval") return [`${durationLabel(schedule.interval_seconds)} 간격으로 계속 촬영`];
  if (!(schedule.daily_times || []).length) return ["촬영 시각 없음 · 촬영용 전원 계획 비활성화"];
  return partitionCaptureTimes(schedule.daily_times || []).map(block => {
    if (block.length === 1) return `${koreanClock(block[0])}에 촬영`;
    const interval = captureTimeSeconds(block[1]) - captureTimeSeconds(block[0]);
    return `${koreanClock(block[0])}부터 ${koreanClock(block.at(-1))}까지, ${durationLabel(interval)} 간격으로 촬영`;
  });
}

function sensorScheduleLines(schedule) {
  if (!(schedule.daily_times || []).length) return ["수집 시각 없음 · 센서용 전원 계획 비활성화"];
  return partitionCaptureTimes(schedule.daily_times || []).map(block => {
    if (block.length === 1) return `${koreanClock(block[0])}에 수집`;
    const interval = captureTimeSeconds(block[1]) - captureTimeSeconds(block[0]);
    return `${koreanClock(block[0])}부터 ${koreanClock(block.at(-1))}까지, ${durationLabel(interval)} 간격으로 수집`;
  });
}

function renderPowerTimeline(configuration, activePlan) {
  const schedules = (configuration?.capture_schedules || []).filter(schedule => schedule.mode !== "disabled");
  const groups = new Map();
  schedules.forEach(schedule => {
    const key = JSON.stringify({ mode: schedule.mode, interval_seconds: schedule.interval_seconds, daily_times: schedule.daily_times, timezone: schedule.timezone });
    const group = groups.get(key) || { schedule, cameras: [] };
    group.cameras.push(schedule.node);
    groups.set(key, group);
  });
  const policies = configuration?.policies || {};
  const captureCards = [...groups.values()].map(group => `
    <div class="window">
      <strong>사진 촬영</strong>
      ${captureScheduleLines(group.schedule).map(line => `<small class="window-detail">${safe(line)}</small>`).join("")}
      <div class="activity-tags">
        ${group.cameras.map(camera => `<span class="tag">${safe(camera)}</span>`).join("")}
        ${group.schedule.mode === "daily" && !(group.schedule.daily_times || []).length ? '<span class="tag warning-tag">촬영 계획 꺼짐</span>' : ""}
        <span class="tag">작동 전 ${safe(durationLabel(policies["boot-lead-seconds"] || 0))}</span>
        <span class="tag">작동 후 ${safe(durationLabel(policies["shutdown-grace-seconds"] || 0))}</span>
      </div>
    </div>`).join("");
  const sensorGroups = new Map();
  (configuration?.sensor_schedules || []).filter(schedule => schedule.mode !== "disabled").forEach(schedule => {
    const key = JSON.stringify({ mode: schedule.mode, daily_times: schedule.daily_times, timezone: schedule.timezone });
    const group = sensorGroups.get(key) || { schedule, sensors: [] };
    group.sensors.push(schedule.node);
    sensorGroups.set(key, group);
  });
  const sensorCards = [...sensorGroups.values()].map(group => `
    <div class="window">
      <strong>온습도 수집</strong>
      ${sensorScheduleLines(group.schedule).map(line => `<small class="window-detail">${safe(line)}</small>`).join("")}
      <div class="activity-tags">
        ${group.sensors.map(sensor => `<span class="tag">${safe(sensor)}</span>`).join("")}
        ${!(group.schedule.daily_times || []).length ? '<span class="tag warning-tag">수집 계획 꺼짐</span>' : ""}
        <span class="tag">작동 전 ${safe(durationLabel(policies["boot-lead-seconds"] || 0))}</span>
        <span class="tag">작동 후 ${safe(durationLabel(policies["shutdown-grace-seconds"] || 0))}</span>
      </div>
    </div>`).join("");
  const keepSchedules = configuration?.power_keep_schedules || [];
  const keepCard = keepSchedules.length ? `
    <div class="window">
      <strong>전원 유지</strong>
      ${keepSchedules.map(item => `<small class="window-detail">${safe(koreanClock(item.start))}부터 ${safe(koreanClock(item.end))}까지</small>`).join("")}
      <div class="activity-tags"><span class="tag">매일 반복</span></div>
    </div>` : "";
  const recurringScheduleIds = [
    ...(configuration?.capture_schedules || []),
    ...(configuration?.sensor_schedules || []),
    ...(configuration?.power_keep_schedules || []),
  ].map(schedule => schedule.id);
  const oneShotWindows = (activePlan?.power_windows || []).filter(window => {
    return window.activity_ids.some(activityId => !recurringScheduleIds.some(scheduleId =>
      activityId === scheduleId || activityId.startsWith(`${scheduleId}-`)
    ));
  }).map(window => `
    <div class="window"><strong>${fmt(window.power_on)} → ${fmt(window.power_down)}</strong>
    <small>${powerWindowStateLabel(window, activePlan.activities)}</small>
    <div class="activity-tags">${powerWindowLabels(window, activePlan.activities).map(label => `<span class="tag">${safe(label)}</span>`).join("")}</div></div>`).join("");
  return captureCards + sensorCards + keepCard + oneShotWindows;
}

function renderDashboard() {
  const dashboard = state.dashboard;
  if (!dashboard) return;
  const runtime = dashboard.runtime || { runs: 0, failed_runs: 0, events: 0, data: 0 };
  const active = dashboard.revision.active_revision;
  const cameraNodes = (dashboard.nodes || []).filter(node => node.node_id?.startsWith("camera"));
  const healthyCameras = cameraNodes.filter(node => node.healthy).length;
  $("#metric-grid").innerHTML = [
    ["적용 중인 설정", active ? `#${active.id}` : "없음", active ? "good" : "alert"],
    ["연결된 카메라", `${healthyCameras}/${cameraNodes.length}`, healthyCameras === cameraNodes.length ? "good" : "alert"],
    ["수집 데이터", runtime.data, ""],
    ["촬영 오류", runtime.failed_runs, runtime.failed_runs ? "alert" : "good"],
  ].map(([label, value, cls]) => `<article class="metric"><span>${label}</span><strong class="${cls}">${value}</strong></article>`).join("");

  const activePlan = dashboard.active_plan || { activities: [], power_windows: [] };
  const timeline = renderPowerTimeline(state.configuration, activePlan);
  $("#power-timeline").innerHTML = timeline || '<div class="empty">등록된 반복 일정이 없습니다.</div>';

  const nodes = dashboard.nodes || [];
  $("#node-summary").innerHTML = nodes.length ? nodes.map(node => `<div class="node-row"><div><strong>${safe(node.node_id)}</strong><small>${safe(node.camera_detail || node.error || "CaptureNode API")}</small></div>${status(node.healthy ? "healthy" : "unhealthy")}</div>`).join("") : '<div class="empty">실행 가능한 노드가 없습니다.</div>';
  renderRuns("#recent-runs", state.runs.slice(0, 5));
}

function renderRuns(target, items) {
  const rows = items.map(run => {
    const nativeCapture = run.activity_id.startsWith("native-");
    const label = nativeCapture ? "정기 촬영" : run.activity_id;
    const indexed = run.result?.indexed_data_count;
    const result = run.error || (run.status === "succeeded"
      ? `촬영 완료${Number.isInteger(indexed) ? ` · 파일 ${indexed}개` : ""}`
      : run.external_id || "—");
    return `<tr><td>${status(run.status)}</td><td><strong>${safe(label)}</strong><br><code>${safe(run.run_id.slice(0, 12))}</code></td><td>${safe(run.node_id)}</td><td>${fmt(run.scheduled_at)}</td><td>${safe(result)}</td></tr>`;
  });
  $(target).innerHTML = table(["상태", "작업 / run", "노드", "실행 시각", "결과"], rows);
}
function renderEvents() {
  const labels = {
    "configuration.capture_schedule_updated": "촬영 설정 변경",
    "configuration.power_settings_updated": "전원 설정 변경",
    "configuration.revision_applied": "고급 설정 변경",
    "power.schedule_disabled": "자동 전원 계획 비활성화",
    "power.schedule_enabled": "자동 전원 계획 활성화",
    "node.config.update_failed": "촬영 설정 적용 실패",
    "node.config.rollback_failed": "촬영 설정 복구 실패",
  };
  const displayPayload = event => {
    if (event.event_type === "node.config.update_failed") return { 안내: "카메라를 다시 시작하지 못해 기존 설정으로 복구했습니다." };
    if (event.event_type === "node.config.rollback_failed") return { 안내: "설정 적용과 기존 설정 복구에 실패했습니다. 장치 상태를 확인해 주세요." };
    return event.payload;
  };
  const rows = state.events.map(event => `<tr><td>${fmt(event.occurred_at)}</td><td>${safe(event.node_id || "—")}</td><td>${status(event.severity)}</td><td><strong>${safe(labels[event.event_type] || event.event_type)}</strong></td><td><code class="event-payload">${esc(JSON.stringify(displayPayload(event)))}</code></td></tr>`);
  $("#events-table").innerHTML = rows.length
    ? `<table class="events-table"><colgroup><col class="event-time" /><col class="event-node" /><col class="event-severity" /><col class="event-type" /><col class="event-content" /></colgroup><thead><tr><th>시각</th><th>노드</th><th>등급</th><th>유형</th><th>내용</th></tr></thead><tbody>${rows.join("")}</tbody></table>`
    : '<div class="empty">표시할 이벤트가 없습니다.</div>';
  const first = state.eventsTotal ? state.eventsOffset + 1 : 0;
  const last = Math.min(state.eventsOffset + state.events.length, state.eventsTotal);
  $("#events-page-info").textContent = `전체 ${state.eventsTotal.toLocaleString()}건 · ${first.toLocaleString()}–${last.toLocaleString()}건`;
  $("#events-prev").disabled = state.eventsOffset === 0;
  $("#events-next").disabled = state.eventsOffset + state.events.length >= state.eventsTotal;
  $("#events-page-size").value = String(state.eventsPageSize);
}

async function loadEvents() {
  const result = await api(`/api/events?limit=${state.eventsPageSize}&offset=${state.eventsOffset}`);
  state.events = result.items;
  state.eventsTotal = result.total ?? result.items.length;
  if (state.eventsOffset >= state.eventsTotal && state.eventsOffset > 0) {
    state.eventsOffset = Math.max(0, Math.floor((state.eventsTotal - 1) / state.eventsPageSize) * state.eventsPageSize);
    return loadEvents();
  }
  renderEvents();
}
function renderData() {
  const kindLabels = { image: "이미지", "soil-moisture": "토양수분" };
  const rows = state.data.map(item => {
    const location = item.storage_location || item.summary || "—";
    const isDng = item.media_type === "image/x-adobe-dng" || /\.dng$/i.test(location);
    const kind = isDng ? "<small>DNG RAW</small>" : item.kind !== "image" ? `<small>${safe(kindLabels[item.kind] || item.kind)}</small>` : "";
    const actions = item.original_available
      ? `<div class="data-actions">${isDng ? "" : `<button type="button" class="ghost" data-view-data="${safe(item.data_id)}">보기</button>`}<button type="button" class="ghost" data-download-data="${safe(item.data_id)}" data-data-name="${safe(location.split("/").at(-1) || "original")}">다운로드</button></div>`
      : '<span class="help">접근 불가</span>';
    return `<tr><td>${fmt(item.captured_at)}</td><td><strong>${safe(item.node_id)}</strong>${kind}</td><td>${formatBytes(item.size)}</td><td><code class="data-location">${safe(location)}</code></td><td>${actions}</td></tr>`;
  });
  $("#data-table").innerHTML = rows.length
    ? `<table class="data-table"><colgroup><col class="data-time" /><col class="data-node" /><col class="data-size" /><col class="data-location-column" /><col class="data-original" /></colgroup><thead><tr><th>생성 시각</th><th>노드</th><th>크기</th><th>저장 위치</th><th>원본</th></tr></thead><tbody>${rows.join("")}</tbody></table>`
    : '<div class="empty">표시할 수집 데이터가 없습니다.</div>';
  const first = state.dataTotal ? state.dataOffset + 1 : 0;
  const last = Math.min(state.dataOffset + state.data.length, state.dataTotal);
  $("#data-page-info").textContent = `전체 ${state.dataTotal.toLocaleString()}건 · ${first.toLocaleString()}–${last.toLocaleString()}건`;
  $("#data-prev").disabled = state.dataOffset === 0;
  $("#data-next").disabled = state.dataOffset + state.data.length >= state.dataTotal;
  $("#data-page-size").value = String(state.dataPageSize);
}

function formatBytes(value) {
  if (value == null || !Number.isFinite(Number(value)) || Number(value) < 0) return "—";
  const bytes = Number(value);
  const units = ["B", "KB", "MB", "GB", "TB"];
  if (bytes < 1024) return `${bytes.toLocaleString()} B`;
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const scaled = bytes / (1024 ** index);
  return `${scaled.toLocaleString(undefined, { maximumFractionDigits: scaled >= 10 ? 1 : 2 })} ${units[index]}`;
}

async function loadData() {
  const result = await api(`/api/data?limit=${state.dataPageSize}&offset=${state.dataOffset}`);
  state.data = result.items;
  state.dataTotal = result.total ?? result.items.length;
  if (state.dataOffset >= state.dataTotal && state.dataOffset > 0) {
    state.dataOffset = Math.max(0, Math.floor((state.dataTotal - 1) / state.dataPageSize) * state.dataPageSize);
    return loadData();
  }
  renderData();
}

function renderSensors() {
  const counts = state.sensorDelivery.counts || {};
  const latest = state.sensorData[0];
  const sensorNodes = state.nodes.filter(node => node.kind === "sensor-node");
  $("#sensor-metric-grid").innerHTML = [
    ["등록 센서", sensorNodes.length, sensorNodes.length ? "good" : "alert"],
    ["최근 온도", latest ? `${latest.temperature_c.toLocaleString()} °C` : "—", latest ? "good" : ""],
    ["최근 습도", latest ? `${latest.humidity_percent.toLocaleString()} %` : "—", latest ? "good" : ""],
    ["원격 재시도", (counts.pending || 0) + (counts.failed || 0), counts.failed ? "alert" : "good"],
  ].map(([label, value, cls]) => `<article class="metric"><span>${label}</span><strong class="${cls}">${value}</strong></article>`).join("");

  const measurementRows = state.sensorData.map(item => `<tr><td>${fmt(item.timestamp)}</td><td><strong>${safe(item.node_id)}</strong></td><td>${safe(item.temperature_c)}</td><td>${safe(item.humidity_percent)}</td><td>${status(item.sync_status)}<br><code>${safe(item.relative_path)}</code></td></tr>`);
  $("#sensor-data-table").innerHTML = measurementRows.length
    ? `<table class="sensor-data-table"><thead><tr><th>예약 시각</th><th>센서</th><th>온도 °C</th><th>습도 %</th><th>저장 상태</th></tr></thead><tbody>${measurementRows.join("")}</tbody></table>`
    : '<div class="empty">로컬 CSV에 저장된 온·습도 측정값이 없습니다.</div>';

  const deliveryRows = (state.sensorDelivery.items || []).map(item => `<tr><td>${fmt(item.updated_at)}</td><td><strong>${safe(item.node_id)}</strong><br><small>${safe(item.target)}</small></td><td>${status(item.status)}</td><td>${safe(item.attempts)}</td><td>${item.next_attempt_at ? fmt(item.next_attempt_at) : "—"}</td><td><code>${safe(item.relative_path)}</code>${item.last_error ? `<br><small class="danger-text">${safe(item.last_error)}</small>` : ""}</td></tr>`);
  $("#sensor-delivery-table").innerHTML = deliveryRows.length
    ? `<table class="sensor-delivery-table"><thead><tr><th>갱신 시각</th><th>센서 / 대상</th><th>상태</th><th>시도</th><th>다음 재시도</th><th>CSV / 오류</th></tr></thead><tbody>${deliveryRows.join("")}</tbody></table>`
    : '<div class="empty">설정된 원격 복제 이력이 없습니다. 로컬 CSV 수집은 독립적으로 계속됩니다.</div>';
}

async function loadSensors() {
  const [data, delivery, settings] = await Promise.all([
    api("/api/sensor-data?limit=100"),
    api("/api/sensor-storage"),
    api("/api/sensor-settings"),
  ]);
  state.sensorData = data.items;
  state.sensorDelivery = delivery;
  state.sensorSettings = settings.items;
  state.sensorRestartRequired = settings.restart_required;
  renderSensors();
  renderSensorSettingsNodeOptions();
}

async function accessDataOriginal(dataId, { download = false, filename = "original" } = {}) {
  try {
    const response = await fetch(`/api/data/content?data_id=${encodeURIComponent(dataId)}&download=${download}`, {
      headers: { "X-Supervisor-Token": token() },
      cache: "no-store",
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try { detail = (await response.json()).detail || detail; } catch {}
      throw new Error(detail);
    }
    const objectUrl = URL.createObjectURL(await response.blob());
    if (download) {
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = filename;
      link.click();
    } else {
      if (dataViewUrl) URL.revokeObjectURL(dataViewUrl);
      dataViewUrl = objectUrl;
      $("#data-view-title").textContent = filename;
      $("#data-view-image").src = objectUrl;
      $("#data-view-dialog").showModal();
      return;
    }
    setTimeout(() => URL.revokeObjectURL(objectUrl), 60000);
  } catch (error) {
    toast(friendlyError(error), true);
  }
}

function closeDataViewer() {
  const dialog = $("#data-view-dialog");
  if (dialog.open) dialog.close();
}
function renderNodes() {
  $("#node-grid").innerHTML = state.nodes.length ? state.nodes.map(node => `<article class="node-card"><div class="panel-title"><h2>${safe(node.id)}</h2>${status(node.health?.healthy ? "healthy" : "unhealthy")}</div><dl><dt>종류</dt><dd>${safe(node.kind)}</dd><dt>Endpoint</dt><dd>${safe(node.endpoint || "—")}</dd><dt>업무</dt><dd>${safe(node.activity_types.join(", "))}</dd><dt>프로필</dt><dd>${safe(node.profiles.join(", ") || "—")}</dd><dt>상태</dt><dd>${safe(node.health?.error || node.health?.status || "어댑터 없음")}</dd></dl></article>`).join("") : '<div class="empty">등록된 노드가 없습니다.</div>';
}
function renderCameras() {
  stopCameraStreams();
  previewUrls.forEach(url => URL.revokeObjectURL(url));
  previewUrls.clear();
  const cameras = state.nodes.filter(node => node.kind === "capture-node");
  $("#camera-grid").innerHTML = cameras.length ? cameras.map(node => `
    <article class="camera-card" data-camera="${safe(node.id)}">
      <div class="panel-title"><div><h2>${safe(node.id)}</h2><span>${safe(node.endpoint || "loopback")}</span></div>${status(node.health?.healthy ? "healthy" : "unhealthy")}</div>
      <div class="camera-frame">
        <img alt="${safe(node.id)} 카메라 프리뷰" hidden />
        <div class="camera-placeholder">프레임을 불러오는 중입니다.</div>
      </div>
      <div class="camera-footer"><span data-preview-status>대기 중</span><button class="ghost" data-preview-refresh="${safe(node.id)}">스트림 재연결</button></div>
    </article>`).join("") : '<div class="empty">등록된 CaptureNode가 없습니다.</div>';
}

function loadConfigurationIntoForm(configuration) {
  state.configuration = structuredClone(configuration);
  state.settingsLoaded = true;
  $("#config-editor").value = JSON.stringify(configuration, null, 2);
  loadPowerSettingsIntoForm(configuration);
}

function renderPowerKeepRows(items = []) {
  $("#power-keep-list").innerHTML = items.length ? items.map((item, index) => `
    <div class="power-keep-row" data-power-keep-id="${safe(item.id)}">
      <label class="field"><span>시작 시각</span><input type="time" step="1" data-power-keep-start value="${safe(item.start)}" required /></label>
      <span class="power-keep-separator">부터</span>
      <label class="field"><span>종료 시각</span><input type="time" step="1" data-power-keep-end value="${safe(item.end)}" required /></label>
      <button type="button" class="text-button danger-text" data-remove-power-keep="${index}">삭제</button>
    </div>`).join("") : '<div class="friendly-empty"><strong>별도 전원 유지 시간이 없습니다.</strong><span>필요할 때 ‘전원 유지 추가’를 눌러 주세요.</span></div>';
}

function loadPowerSettingsIntoForm(configuration) {
  const policies = configuration.policies || {};
  $("#power-warmup-seconds").value = policies["boot-lead-seconds"] ?? 120;
  $("#power-cleanup-seconds").value = policies["shutdown-grace-seconds"] ?? 60;
  renderPowerKeepRows(configuration.power_keep_schedules || []);
}

function powerSettingsFromForm() {
  const warmup_seconds = Number($("#power-warmup-seconds").value);
  const cleanup_seconds = Number($("#power-cleanup-seconds").value);
  if (![warmup_seconds, cleanup_seconds].every(value => Number.isInteger(value) && value >= 0 && value <= 86400)) {
    throw new Error("작동 전후 여유 시간은 0~86400초 사이의 정수로 입력해 주세요.");
  }
  const keep_schedules = [...$("#power-keep-list").querySelectorAll("[data-power-keep-id]")].map((row, index) => ({
    id: row.dataset.powerKeepId || `daily-power-keep-${index + 1}`,
    start: row.querySelector("[data-power-keep-start]").value,
    end: row.querySelector("[data-power-keep-end]").value,
    timezone: state.configuration?.timezone || "Asia/Seoul",
  }));
  keep_schedules.forEach(item => {
    if (!item.start || !item.end || captureTimeSeconds(item.end) <= captureTimeSeconds(item.start)) {
      throw new Error("전원 유지 종료 시각은 시작 시각보다 늦어야 합니다.");
    }
  });
  return { warmup_seconds, cleanup_seconds, keep_schedules };
}

function renderCaptureCameraOptions() {
  const cameras = state.nodes.filter(node => node.kind === "capture-node");
  const previous = $("#capture-schedule-camera").value || "common";
  $("#capture-schedule-camera").innerHTML = `<option value="common">모든 카메라 · 공통</option>${cameras.map(node => `<option value="${safe(node.id)}">${safe(node.id)} · 개별</option>`).join("")}`;
  if (previous === "common" || cameras.some(node => node.id === previous)) $("#capture-schedule-camera").value = previous;
}

function updateCaptureScheduleVisibility() {
  const mode = $("#capture-schedule-mode").value;
  document.querySelectorAll(".capture-interval-field").forEach(item => item.hidden = mode !== "interval");
  document.querySelectorAll(".capture-daily-field").forEach(item => item.hidden = mode !== "daily");
  updateCaptureScheduleWarning();
}

function updateCaptureScheduleWarning() {
  const warning = $("#capture-schedule-warning");
  const hasTimes = $("#daily-time-list").querySelector('input[type="time"]') !== null;
  const hasPowerKeep = (state.configuration?.power_keep_schedules || []).length > 0;
  warning.textContent = hasPowerKeep
    ? "촬영 시각이 없습니다. 촬영용 전원 계획은 꺼지지만 별도 전원 유지 일정은 계속 작동합니다."
    : "촬영 시각이 없습니다. 촬영과 자동 전원 계획이 모두 비활성화됩니다.";
  warning.hidden = $("#capture-schedule-mode").value !== "daily" || hasTimes;
}

function splitCaptureInterval(totalSeconds) {
  let remaining = Number(totalSeconds || 0);
  const days = Math.floor(remaining / 86400);
  remaining -= days * 86400;
  const hours = Math.floor(remaining / 3600);
  remaining -= hours * 3600;
  const minutes = Math.floor(remaining / 60);
  const seconds = Number((remaining - minutes * 60).toFixed(2));
  return { days, hours, minutes, seconds };
}

function captureIntervalSeconds() {
  return Number($("#capture-interval-days").value || 0) * 86400
    + Number($("#capture-interval-hours").value || 0) * 3600
    + Number($("#capture-interval-minutes").value || 0) * 60
    + Number($("#capture-interval-seconds").value || 0);
}

function captureTimeSeconds(value) {
  const parts = String(value).split(":").map(Number);
  const hours = parts[0], minutes = parts[1], seconds = parts[2] ?? 0;
  if (![hours, minutes, seconds].every(Number.isInteger) || hours < 0 || hours > 23 || minutes < 0 || minutes > 59 || seconds < 0 || seconds > 59) {
    throw new Error("올바른 시각을 입력해 주세요.");
  }
  return hours * 3600 + minutes * 60 + seconds;
}

function captureTimeMinutes(value) {
  return Math.floor(captureTimeSeconds(value) / 60);
}

function minutesToCaptureTime(value) {
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}

function secondsToCaptureTime(value, includeSeconds = true) {
  const base = `${String(Math.floor(value / 3600)).padStart(2, "0")}:${String(Math.floor((value % 3600) / 60)).padStart(2, "0")}`;
  return includeSeconds ? `${base}:${String(value % 60).padStart(2, "0")}` : base;
}

function partitionCaptureTimes(times) {
  const sorted = [...new Set(times)].map(value => ({ value, seconds: captureTimeSeconds(value) })).sort((a, b) => a.seconds - b.seconds);
  const count = Array(sorted.length + 1).fill(Number.POSITIVE_INFINITY);
  const next = Array(sorted.length + 1).fill(sorted.length);
  const length = Array(sorted.length + 1).fill(0);
  count[sorted.length] = 0;
  for (let start = sorted.length - 1; start >= 0; start -= 1) {
    let interval = null;
    for (let end = start; end < sorted.length; end += 1) {
      if (end === start + 1) interval = sorted[end].seconds - sorted[start].seconds;
      else if (end > start + 1 && sorted[end].seconds - sorted[end - 1].seconds !== interval) break;
      const candidate = 1 + count[end + 1];
      const candidateLength = end - start + 1;
      if (candidate < count[start] || (candidate === count[start] && candidateLength > length[start])) {
        count[start] = candidate;
        next[start] = end + 1;
        length[start] = candidateLength;
      }
    }
  }
  const blocks = [];
  for (let start = 0; start < sorted.length; start = next[start]) blocks.push(sorted.slice(start, next[start]).map(item => item.value));
  return blocks;
}

function captureRangesFromTimes(times) {
  if (!times?.length) return [DEFAULT_CAPTURE_RANGE];
  const ranges = partitionCaptureTimes(times).map(block => {
    const start = captureTimeSeconds(block[0]);
    const end = captureTimeSeconds(block.at(-1));
    const interval = block.length > 1 ? captureTimeSeconds(block[1]) - start : null;
    if (block.length < 2 || start % 60 !== 0 || end % 60 !== 0 || interval % 60 !== 0) return null;
    return { start: secondsToCaptureTime(start, false), intervalMinutes: interval / 60, end: secondsToCaptureTime(end, false) };
  });
  return ranges.every(Boolean) ? ranges : [DEFAULT_CAPTURE_RANGE];
}

function renderCaptureRanges(ranges) {
  $("#capture-range-list").innerHTML = ranges.map((range, index) => `
    <div class="capture-range-block">
      <div class="capture-range-heading"><span>블록 ${index + 1}</span><button type="button" class="text-button danger-text" data-remove-capture-range="${index}" ${ranges.length === 1 ? "disabled" : ""}>삭제</button></div>
      <div class="capture-range-grid">
        <label class="field"><input type="time" value="${safe(range.start)}" required /><span>시작</span></label>
        <label class="field"><input type="number" min="1" max="1440" step="1" value="${safe(range.intervalMinutes)}" required /><span>간격(분)</span></label>
        <label class="field"><input type="time" value="${safe(range.end)}" required /><span>종료</span></label>
      </div>
    </div>`).join("");
}

function readCaptureRanges() {
  return [...$("#capture-range-list").querySelectorAll(".capture-range-block")].map(block => {
    const inputs = block.querySelectorAll("input");
    return { start: inputs[0].value, intervalMinutes: Number(inputs[1].value), end: inputs[2].value };
  });
}

function generateCaptureTimes(range) {
  const start = captureTimeMinutes(range.start), end = captureTimeMinutes(range.end), interval = Number(range.intervalMinutes);
  if (!Number.isInteger(interval) || interval <= 0) throw new Error("촬영 간격은 1분 이상의 정수여야 합니다.");
  if (end < start) throw new Error("종료 시각은 시작 시각보다 빠를 수 없습니다.");
  const times = [];
  for (let minute = start; minute <= end; minute += interval) times.push(`${minutesToCaptureTime(minute)}:00`);
  return times;
}

function formatCaptureSchedule(schedule) {
  if (!schedule || schedule.mode === "disabled") return "자동 촬영 사용 안 함";
  if (schedule.mode === "interval") {
    const parts = splitCaptureInterval(schedule.interval_seconds);
    const values = [[parts.days, "일"], [parts.hours, "시간"], [parts.minutes, "분"], [parts.seconds, "초"]].filter(([value]) => Number(value) > 0).map(([value, unit]) => `${value}${unit}`);
    return `${values.join(" ") || "0초"}마다 촬영`;
  }
  if (!schedule.daily_times?.length) return `촬영 시각 없음 · ${schedule.timezone}`;
  const blocks = partitionCaptureTimes(schedule.daily_times).map(block => {
    if (block.length === 1) return secondsToCaptureTime(captureTimeSeconds(block[0]), captureTimeSeconds(block[0]) % 60 !== 0);
    const interval = captureTimeSeconds(block[1]) - captureTimeSeconds(block[0]);
    const intervalLabel = interval % 3600 === 0 ? `${interval / 3600}시간` : interval % 60 === 0 ? `${interval / 60}분` : `${interval}초`;
    return `${secondsToCaptureTime(captureTimeSeconds(block[0]), false)}–${secondsToCaptureTime(captureTimeSeconds(block.at(-1)), false)} · ${intervalLabel}마다`;
  });
  return blocks.join(" / ");
}

function renderDailyTimes(times = []) {
  $("#daily-time-list").innerHTML = times.length ? times.map((value, index) => `
    <div class="daily-time-row">
      <input type="time" step="1" value="${safe(value)}" aria-label="매일 촬영 시각 ${index + 1}" />
      <button type="button" class="text-button danger-text" data-remove-daily-time="${index}">삭제</button>
    </div>`).join("") : '<div class="friendly-empty"><strong>등록된 시각이 없습니다.</strong><span>‘시각 추가’를 눌러 매일 촬영할 시각을 입력하세요.</span></div>';
  updateCaptureScheduleWarning();
}

function addDailyTime(value = "09:00:00") {
  const values = [...$("#daily-time-list").querySelectorAll('input[type="time"]')].map(input => input.value);
  values.push(value);
  renderDailyTimes(values);
}

function loadCaptureScheduleIntoForm(capture) {
  const schedule = capture || { mode: "disabled", interval_seconds: null, daily_times: [], timezone: "Asia/Seoul" };
  state.captureSchedule = structuredClone(schedule);
  $("#capture-schedule-mode").value = schedule.mode || "disabled";
  const interval = splitCaptureInterval(schedule.interval_seconds || 0);
  $("#capture-interval-days").value = interval.days;
  $("#capture-interval-hours").value = interval.hours;
  $("#capture-interval-minutes").value = interval.minutes;
  $("#capture-interval-seconds").value = interval.seconds;
  $("#capture-schedule-timezone").value = schedule.timezone || "Asia/Seoul";
  renderCaptureRanges(captureRangesFromTimes(schedule.daily_times || []));
  renderDailyTimes(schedule.daily_times || []);
  $("#capture-schedule-summary").textContent = formatCaptureSchedule(schedule);
  updateCaptureScheduleVisibility();
}

async function loadCaptureSchedule(nodeId = $("#capture-schedule-camera").value) {
  if (!nodeId) return;
  $("#capture-schedule-note").textContent = "촬영 일정을 불러오는 중입니다…";
  try {
    const response = await api("/api/capture-schedules");
    if ($("#capture-schedule-camera").value !== nodeId) return;
    const schedules = new Map(response.items.map(item => [item.node, item]));
    let schedule = schedules.get(nodeId);
    let mixed = false;
    if (nodeId === "common") {
      const values = response.camera_ids.map(cameraId => schedules.get(cameraId) || { mode: "disabled", interval_seconds: null, daily_times: [], timezone: "Asia/Seoul" });
      schedule = values[0];
      const comparable = value => JSON.stringify({ mode: value.mode, interval_seconds: value.interval_seconds, daily_times: value.daily_times || [], timezone: value.timezone });
      mixed = values.some(value => comparable(value) !== comparable(schedule));
    }
    loadCaptureScheduleIntoForm(schedule);
    $("#capture-schedule-note").textContent = mixed
      ? "개별 설정이 서로 다릅니다. 표시된 값을 적용하면 모든 카메라가 공통 설정으로 통일됩니다."
      : nodeId === "common"
        ? "모든 카메라에 같은 촬영 일정을 적용합니다."
        : `${nodeId}에만 이 촬영 일정을 적용합니다.`;
  } catch (error) {
    if ($("#capture-schedule-camera").value === nodeId) {
      $("#capture-schedule-note").textContent = `설정을 불러오지 못했습니다: ${error.message}`;
    }
  }
}

function captureScheduleFromForm() {
  const mode = $("#capture-schedule-mode").value;
  const timezone = $("#capture-schedule-timezone").value.trim() || "Asia/Seoul";
  if (mode === "disabled") return { mode, interval_seconds: null, daily_times: [], timezone };
  if (mode === "interval") {
    const interval_seconds = captureIntervalSeconds();
    if (!Number.isFinite(interval_seconds) || interval_seconds <= 0) throw new Error("0보다 큰 촬영 간격을 입력해 주세요.");
    return { mode, interval_seconds, daily_times: [], timezone };
  }
  const daily_times = [...$("#daily-time-list").querySelectorAll('input[type="time"]')].map(input => input.value).filter(Boolean);
  if (new Set(daily_times).size !== daily_times.length) throw new Error("같은 촬영 시각을 두 번 등록할 수 없습니다.");
  return { mode, interval_seconds: null, daily_times, timezone };
}

function renderSensorNodeOptions() {
  const sensors = state.nodes.filter(node => (node.activity_types || []).includes("sensor-collection"));
  const previous = $("#sensor-schedule-node").value || "common";
  $("#sensor-schedule-node").innerHTML = `<option value="common">모든 센서 · 공통</option>${sensors.map(node => `<option value="${safe(node.id)}">${safe(node.id)} · 개별</option>`).join("")}`;
  if (previous === "common" || sensors.some(node => node.id === previous)) $("#sensor-schedule-node").value = previous;
}

function updateSensorScheduleVisibility() {
  const daily = $("#sensor-schedule-mode").value === "daily";
  document.querySelectorAll(".sensor-daily-field").forEach(item => item.hidden = !daily);
  const hasTimes = $("#sensor-time-list").querySelector('input[type="time"]') !== null;
  $("#sensor-schedule-warning").hidden = !daily || hasTimes;
}

function sensorRangesFromTimes(times) {
  if (!times?.length) return [DEFAULT_SENSOR_RANGE];
  const ranges = partitionCaptureTimes(times).map(block => {
    const start = captureTimeSeconds(block[0]);
    const end = captureTimeSeconds(block.at(-1));
    const interval = block.length > 1 ? captureTimeSeconds(block[1]) - start : null;
    if (block.length < 2 || start % 60 !== 0 || end % 60 !== 0 || interval % 60 !== 0) return null;
    return { start: secondsToCaptureTime(start, false), intervalMinutes: interval / 60, end: secondsToCaptureTime(end, false) };
  });
  return ranges.every(Boolean) ? ranges : [DEFAULT_SENSOR_RANGE];
}

function renderSensorRanges(ranges) {
  $("#sensor-range-list").innerHTML = ranges.map((range, index) => `
    <div class="capture-range-block">
      <div class="capture-range-heading"><span>블록 ${index + 1}</span><button type="button" class="text-button danger-text" data-remove-sensor-range="${index}" ${ranges.length === 1 ? "disabled" : ""}>삭제</button></div>
      <div class="capture-range-grid">
        <label class="field"><input type="time" value="${safe(range.start)}" required /><span>시작</span></label>
        <label class="field"><input type="number" min="1" max="1440" step="1" value="${safe(range.intervalMinutes)}" required /><span>간격(분)</span></label>
        <label class="field"><input type="time" value="${safe(range.end)}" required /><span>종료</span></label>
      </div>
    </div>`).join("");
}

function readSensorRanges() {
  return [...$("#sensor-range-list").querySelectorAll(".capture-range-block")].map(block => {
    const inputs = block.querySelectorAll("input");
    return { start: inputs[0].value, intervalMinutes: Number(inputs[1].value), end: inputs[2].value };
  });
}

function generateSensorTimes(range) {
  const start = captureTimeMinutes(range.start), end = captureTimeMinutes(range.end), interval = Number(range.intervalMinutes);
  if (!Number.isInteger(interval) || interval <= 0) throw new Error("수집 간격은 1분 이상의 정수여야 합니다.");
  if (end < start) throw new Error("종료 시각은 시작 시각보다 빠를 수 없습니다.");
  const times = [];
  for (let minute = start; minute <= end; minute += interval) times.push(`${minutesToCaptureTime(minute)}:00`);
  return times;
}

function renderSensorTimes(times = []) {
  $("#sensor-time-list").innerHTML = times.length ? times.map((value, index) => `
    <div class="daily-time-row">
      <input type="time" step="1" value="${safe(value)}" aria-label="매일 센서 수집 시각 ${index + 1}" />
      <button type="button" class="text-button danger-text" data-remove-sensor-time="${index}">삭제</button>
    </div>`).join("") : '<div class="friendly-empty"><strong>등록된 시각이 없습니다.</strong><span>‘시각 추가’를 눌러 매일 수집할 시각을 입력하세요.</span></div>';
  updateSensorScheduleVisibility();
}

function formatSensorSchedule(schedule) {
  if (!schedule || schedule.mode === "disabled") return "자동 센서 수집 사용 안 함";
  if (!schedule.daily_times?.length) return `수집 시각 없음 · ${schedule.timezone}`;
  return `${schedule.daily_times.length}개 저장 시각 · ${schedule.timezone}`;
}

function loadSensorScheduleIntoForm(value) {
  const defaultTimes = generateSensorTimes(DEFAULT_SENSOR_RANGE);
  const schedule = value || { mode: "daily", daily_times: defaultTimes, timezone: "Asia/Seoul" };
  state.sensorSchedule = structuredClone(schedule);
  $("#sensor-schedule-mode").value = schedule.mode || "daily";
  $("#sensor-schedule-timezone").value = schedule.timezone || "Asia/Seoul";
  renderSensorRanges(sensorRangesFromTimes(schedule.daily_times || []));
  renderSensorTimes(schedule.daily_times || []);
  $("#sensor-schedule-summary").textContent = formatSensorSchedule(schedule);
  updateSensorScheduleVisibility();
}

async function loadSensorSchedule(nodeId = $("#sensor-schedule-node").value) {
  if (!nodeId) return;
  $("#sensor-schedule-note").textContent = "센서 일정을 불러오는 중입니다…";
  try {
    const response = await api("/api/sensor-schedules");
    if ($("#sensor-schedule-node").value !== nodeId) return;
    const schedules = new Map(response.items.map(item => [item.node, item]));
    let schedule = schedules.get(nodeId);
    let mixed = false;
    if (nodeId === "common") {
      const values = response.sensor_ids.map(sensorId => schedules.get(sensorId));
      schedule = values[0];
      const comparable = value => JSON.stringify({ mode: value?.mode || "daily", daily_times: value?.daily_times || generateSensorTimes(DEFAULT_SENSOR_RANGE), timezone: value?.timezone || "Asia/Seoul" });
      mixed = values.some(value => comparable(value) !== comparable(schedule));
    }
    loadSensorScheduleIntoForm(schedule);
    $("#sensor-schedule-note").textContent = mixed
      ? "개별 설정이 서로 다릅니다. 적용하면 모든 센서가 공통 설정으로 통일됩니다."
      : nodeId === "common"
        ? "모든 센서에 같은 저장 시각을 적용합니다."
        : `${nodeId}에만 이 수집 일정을 적용합니다.`;
  } catch (error) {
    if ($("#sensor-schedule-node").value === nodeId) $("#sensor-schedule-note").textContent = `설정을 불러오지 못했습니다: ${error.message}`;
  }
}

function sensorScheduleFromForm() {
  const mode = $("#sensor-schedule-mode").value;
  const timezone = $("#sensor-schedule-timezone").value.trim() || "Asia/Seoul";
  if (mode === "disabled") return { mode, daily_times: [], timezone };
  const daily_times = [...$("#sensor-time-list").querySelectorAll('input[type="time"]')].map(input => input.value).filter(Boolean);
  if (new Set(daily_times).size !== daily_times.length) throw new Error("같은 센서 수집 시각을 두 번 등록할 수 없습니다.");
  return { mode, daily_times, timezone };
}

function renderSensorSettingsNodeOptions() {
  const select = $("#sensor-settings-node");
  const previous = select.value;
  select.innerHTML = state.sensorSettings.map(item => `<option value="${safe(item.node_id)}">${safe(item.node_id)}</option>`).join("");
  if (state.sensorSettings.some(item => item.node_id === previous)) select.value = previous;
  if (select.value) loadSensorSettingsIntoForm(select.value);
}

function loadSensorSettingsIntoForm(nodeId = $("#sensor-settings-node").value) {
  const item = state.sensorSettings.find(candidate => candidate.node_id === nodeId);
  if (!item) {
    $("#sensor-settings-note").textContent = "설정 가능한 CWT 센서가 없습니다.";
    return;
  }
  const serial = item.sensor_serial;
  const storage = item.sensor_storage;
  $("#sensor-driver").value = serial.driver;
  $("#sensor-port").value = serial.port;
  $("#sensor-baudrate").value = String(serial.baudrate);
  $("#sensor-slave-id").value = String(serial.slave_id);
  $("#sensor-timeout").value = String(serial.timeout_seconds);
  $("#sensor-local-directory").value = storage.local_directory;
  $("#sensor-retry-base").value = String(storage.retry_base_seconds);
  $("#sensor-retry-max").value = String(storage.retry_max_seconds);
  state.sensorMirrorDraft = structuredClone(storage.mirrors || []);
  $("#sensor-mirror-editor").hidden = true;
  renderSensorMirrorList();
  const health = item.health?.healthy ? "직렬 연결 정상" : item.health?.error || "재시작 후 연결 상태를 확인하세요.";
  $("#sensor-settings-note").textContent = state.sensorRestartRequired
    ? "nodes.json 저장 완료 · 서비스 재시작 전까지 기존 설정으로 실행됩니다."
    : `${health} · 온도와 습도만 수집합니다.`;
}

function sensorMirrorDescription(item) {
  return item.type === "file"
    ? item.directory
    : `${item.username}@${item.host}:${item.port}${item.remote_directory}`;
}

function renderSensorMirrorList() {
  const list = $("#sensor-mirror-list");
  list.className = `storage-list${state.sensorMirrorDraft.length ? "" : " empty"}`;
  list.innerHTML = state.sensorMirrorDraft.length ? state.sensorMirrorDraft.map((item, index) => `
    <div class="storage-item">
      <div class="storage-icon">${safe(item.type.toUpperCase())}</div>
      <div><strong>${safe(item.name)}</strong><span>${safe(sensorMirrorDescription(item))}</span><small>전체 CSV 임시 업로드 후 최종 파일 교체</small></div>
      <div class="storage-item-actions"><button type="button" class="ghost" data-edit-sensor-mirror="${index}">수정</button><button type="button" class="text-button danger-text" data-remove-sensor-mirror="${index}">삭제</button></div>
    </div>`).join("") : '<div class="friendly-empty"><strong>원격 복제 대상이 없습니다.</strong><span>로컬 CSV만 원본으로 저장됩니다.</span></div>';
}

function updateSensorMirrorFields() {
  const type = $("#sensor-mirror-type").value;
  document.querySelectorAll(".sensor-mirror-file-field").forEach(item => item.hidden = type !== "file");
  document.querySelectorAll(".sensor-mirror-remote-field").forEach(item => item.hidden = type === "file");
  document.querySelectorAll(".sensor-mirror-sftp-field").forEach(item => item.hidden = type !== "sftp");
  document.querySelectorAll(".sensor-mirror-secret-field").forEach(item => item.hidden = type === "file");
  document.querySelectorAll(".sensor-mirror-ftps-field").forEach(item => item.hidden = type !== "ftps");
}

function openSensorMirrorEditor(index = -1) {
  const item = index >= 0 ? state.sensorMirrorDraft[index] : { type: "file", name: "", directory: "" };
  $("#sensor-mirror-edit-index").value = String(index);
  $("#sensor-mirror-type").value = item.type;
  $("#sensor-mirror-name").value = item.name || "";
  $("#sensor-mirror-directory").value = item.directory || "";
  $("#sensor-mirror-host").value = item.host || "";
  $("#sensor-mirror-port").value = item.port || (item.type === "ftps" ? 21 : 22);
  $("#sensor-mirror-username").value = item.username || "";
  $("#sensor-mirror-remote-directory").value = item.remote_directory || "";
  $("#sensor-mirror-private-key").value = item.private_key || "";
  $("#sensor-mirror-known-hosts").value = item.known_hosts || "~/.ssh/known_hosts";
  $("#sensor-mirror-password-env").value = item.password_env || "";
  $("#sensor-mirror-verify-tls").checked = item.verify_tls !== false;
  $("#sensor-mirror-editor").hidden = false;
  updateSensorMirrorFields();
}

function sensorMirrorFromEditor() {
  const type = $("#sensor-mirror-type").value;
  const name = $("#sensor-mirror-name").value.trim();
  const index = Number($("#sensor-mirror-edit-index").value);
  const current = index >= 0 && state.sensorMirrorDraft[index]?.type === type ? state.sensorMirrorDraft[index] : {};
  if (!name) throw new Error("복제 대상 이름을 입력해 주세요.");
  if (type === "file") {
    const directory = $("#sensor-mirror-directory").value.trim();
    if (!directory) throw new Error("복제 폴더를 입력해 주세요.");
    return { ...current, type, name, directory, layout: "daily" };
  }
  const host = $("#sensor-mirror-host").value.trim();
  const username = $("#sensor-mirror-username").value.trim();
  const remote_directory = $("#sensor-mirror-remote-directory").value.trim();
  const password_env = $("#sensor-mirror-password-env").value.trim();
  if (!host || !username || !remote_directory) throw new Error("서버 주소, 사용자 이름, 원격 폴더를 입력해 주세요.");
  const common = {
    ...current, type, name, host, port: Number($("#sensor-mirror-port").value), username,
    remote_directory, layout: "daily",
    connect_timeout_seconds: current.connect_timeout_seconds || 10,
    operation_timeout_seconds: current.operation_timeout_seconds || 30,
  };
  if (type === "sftp") {
    const private_key = $("#sensor-mirror-private-key").value.trim();
    if (!private_key && !password_env) throw new Error("SSH 개인 키 또는 비밀번호 환경변수 이름이 필요합니다.");
    return {
      ...common,
      private_key: private_key || undefined,
      password_env: password_env || undefined,
      known_hosts: $("#sensor-mirror-known-hosts").value.trim() || "~/.ssh/known_hosts",
      private_key_passphrase_env: private_key ? current.private_key_passphrase_env : undefined,
    };
  }
  if (!password_env) throw new Error("FTPS 비밀번호 환경변수 이름이 필요합니다.");
  return { ...common, password_env, verify_tls: $("#sensor-mirror-verify-tls").checked, ca_file: current.ca_file };
}

function sensorSettingsFromForm() {
  const serial = {
    driver: "cwt-soil-thc-s",
    port: $("#sensor-port").value.trim(),
    baudrate: Number($("#sensor-baudrate").value),
    timeout_seconds: Number($("#sensor-timeout").value),
    slave_id: Number($("#sensor-slave-id").value),
  };
  const storage = {
    local_directory: $("#sensor-local-directory").value.trim(),
    mirrors: state.sensorMirrorDraft,
    retry_base_seconds: Number($("#sensor-retry-base").value),
    retry_max_seconds: Number($("#sensor-retry-max").value),
  };
  if (!serial.port || !storage.local_directory) throw new Error("직렬 포트와 로컬 CSV 폴더를 입력해 주세요.");
  if (storage.retry_max_seconds < storage.retry_base_seconds) throw new Error("재시도 최대 시간은 시작 시간보다 작을 수 없습니다.");
  return { sensor_serial: serial, sensor_storage: storage };
}

function renderStorageCameraOptions() {
  const cameras = state.nodes.filter(node => node.kind === "capture-node");
  const previous = $("#storage-camera").value || "common";
  $("#storage-camera").innerHTML = `<option value="common">모든 카메라 · 공통</option>${cameras.map(node => `<option value="${safe(node.id)}">${safe(node.id)} · 개별</option>`).join("")}`;
  if (previous === "common" || cameras.some(node => node.id === previous)) $("#storage-camera").value = previous;
}

async function loadCameraConfiguration(nodeId = $("#storage-camera").value) {
  if (!nodeId) return;
  $("#storage-list").innerHTML = '<div class="empty">저장소 설정을 불러오는 중입니다.</div>';
  try {
    const cameraIds = state.nodes.filter(node => node.kind === "capture-node").map(node => node.id);
    const selectedIds = nodeId === "common" ? cameraIds : [nodeId];
    const configurations = await Promise.all(selectedIds.map(cameraId => api(`/api/nodes/${encodeURIComponent(cameraId)}/configuration`)));
    configurations.forEach(configuration => { state.cameraConfigs[configuration.node_id] = configuration; });
    if ($("#storage-camera").value !== nodeId) return;
    const first = configurations[0] || { storages: [] };
    const withRetention = configuration => {
      const retention = new Map((configuration.maintenance?.retention || []).map(rule => [rule.target, rule.max_age_days]));
      return (configuration.storages || []).map(item => ({ ...item, retention_days: retention.get(item.name) || null }));
    };
    state.storageDraft = structuredClone(withRetention(first));
    if (nodeId === "common") {
      state.storageDraft = state.storageDraft.map(item => {
        const value = { ...item };
        if (typeof value.directory === "string") value.directory = value.directory.replaceAll(first.node_id, "{camera}");
        if (typeof value.remote_directory === "string") value.remote_directory = value.remote_directory.replaceAll(first.node_id, "{camera}");
        return value;
      });
      const normalized = configuration => JSON.stringify(withRetention(configuration).map(item => {
        const value = { ...item };
        if (typeof value.directory === "string") value.directory = value.directory.replaceAll(configuration.node_id, "{camera}");
        if (typeof value.remote_directory === "string") value.remote_directory = value.remote_directory.replaceAll(configuration.node_id, "{camera}");
        return value;
      }));
      const mixed = configurations.some(configuration => normalized(configuration) !== JSON.stringify(state.storageDraft));
      $("#storage-restart-note").textContent = mixed
        ? "개별 저장소 설정이 서로 다릅니다. 적용하면 모든 카메라가 표시된 공통 설정으로 통일됩니다."
        : "모든 카메라에 공통 적용됩니다. {camera}는 각 카메라 이름으로 바뀝니다.";
    } else {
      $("#storage-restart-note").textContent = `${nodeId}에만 개별 적용됩니다.`;
    }
    renderStorageList();
  } catch (error) {
    if ($("#storage-camera").value === nodeId) {
      $("#storage-list").innerHTML = `<div class="friendly-empty error-copy"><strong>설정을 불러오지 못했습니다.</strong><span>${safe(error.message)}</span></div>`;
    }
  }
}

function storageDescription(item) {
  if (item.type === "file") return item.directory;
  return `${item.username}@${item.host}:${item.port}${item.remote_directory}`;
}

function renderStorageList() {
  $("#storage-list").classList.toggle("empty", state.storageDraft.length === 0);
  $("#storage-list").innerHTML = state.storageDraft.length ? state.storageDraft.map((item, index) => `
    <div class="storage-item">
      <div class="storage-icon">${item.type === "file" ? "DIR" : item.type.toUpperCase()}</div>
      <div><strong>${safe(item.name)}</strong><span>${safe(storageDescription(item))}</span><small>${item.layout === "daily" ? "날짜별 폴더" : "단일 폴더"} · ${item.retention_days ? `${safe(item.retention_days)}일 후 자동 삭제` : "자동 삭제 사용 안 함"}</small></div>
      <div class="storage-item-actions"><button class="ghost" data-edit-storage="${index}">수정</button><button class="text-button danger-text" data-remove-storage="${index}">삭제</button></div>
    </div>`).join("") : '<div class="friendly-empty"><strong>저장소가 없습니다.</strong><span>촬영 결과는 저장되지 않습니다. 저장소를 하나 이상 추가하세요.</span></div>';
}

function updateStorageFieldVisibility() {
  const type = $("#storage-type").value;
  document.querySelectorAll(".storage-file-field").forEach(item => item.hidden = type !== "file");
  document.querySelectorAll(".storage-remote-field").forEach(item => item.hidden = type === "file");
  document.querySelectorAll(".storage-sftp-field").forEach(item => item.hidden = type !== "sftp");
  document.querySelectorAll(".storage-ftps-field").forEach(item => item.hidden = type !== "ftps");
  document.querySelectorAll(".storage-secret-field").forEach(item => item.hidden = type === "file");
  $("#storage-retention-days").disabled = type !== "file" || !$("#storage-retention-enabled").checked;
  if (type === "sftp" && !$("#storage-port").value) $("#storage-port").value = "22";
  if (type === "ftps" && !$("#storage-port").value) $("#storage-port").value = "21";
}

function openStorageEditor(index = -1) {
  const target = $("#storage-camera").value;
  const item = index >= 0 ? state.storageDraft[index] : { type: "file", name: "", directory: target === "common" ? "captures/{camera}" : `captures/${target}`, layout: "daily", retention_days: 7 };
  $("#storage-edit-index").value = String(index);
  $("#storage-type").value = item.type;
  $("#storage-name").value = item.name || "";
  $("#storage-layout").value = item.layout || "daily";
  $("#storage-directory").value = item.directory || "";
  $("#storage-retention-enabled").checked = Number.isInteger(item.retention_days) && item.retention_days > 0;
  $("#storage-retention-days").value = item.retention_days || 7;
  $("#storage-host").value = item.host || "";
  $("#storage-port").value = item.port || "";
  $("#storage-username").value = item.username || "";
  $("#storage-remote-directory").value = item.remote_directory || "";
  $("#storage-private-key").value = item.private_key || "";
  $("#storage-password-env").value = item.password_env || "";
  $("#storage-verify-tls").checked = item.verify_tls !== false;
  $("#storage-editor").hidden = false;
  updateStorageFieldVisibility();
  $("#storage-name").focus();
}

function storageFromEditor() {
  const type = $("#storage-type").value;
  const name = $("#storage-name").value.trim();
  const layout = $("#storage-layout").value;
  const index = Number($("#storage-edit-index").value);
  const current = index >= 0 && state.storageDraft[index]?.type === type ? state.storageDraft[index] : {};
  if (!name) throw new Error("저장소 이름을 입력해 주세요.");
  if (type === "file") {
    const directory = $("#storage-directory").value.trim();
    if (!directory) throw new Error("저장 폴더를 입력해 주세요.");
    const retention_days = $("#storage-retention-enabled").checked ? Number($("#storage-retention-days").value) : null;
    if (retention_days !== null && (!Number.isInteger(retention_days) || retention_days <= 0)) throw new Error("보존 기간은 1일 이상의 정수여야 합니다.");
    return { ...current, type, name, directory, layout, retention_days };
  }
  const host = $("#storage-host").value.trim();
  const username = $("#storage-username").value.trim();
  const remote_directory = $("#storage-remote-directory").value.trim();
  const password_env = $("#storage-password-env").value.trim();
  if (!host || !username || !remote_directory) throw new Error("서버 주소, 사용자 이름, 원격 폴더를 입력해 주세요.");
  const common = { ...current, type, name, host, port: Number($("#storage-port").value), username, remote_directory, layout };
  if (type === "sftp") {
    const private_key = $("#storage-private-key").value.trim();
    if (!private_key && !password_env) throw new Error("SSH 개인 키 또는 비밀번호 환경변수 이름이 필요합니다.");
    return {
      ...common,
      private_key: private_key || undefined,
      password_env: password_env || undefined,
      private_key_passphrase_env: private_key ? current.private_key_passphrase_env : undefined,
    };
  }
  if (!password_env) throw new Error("FTPS 비밀번호 환경변수 이름이 필요합니다.");
  return { ...common, password_env, verify_tls: $("#storage-verify-tls").checked };
}

function cameraCard(nodeId) {
  return [...document.querySelectorAll("[data-camera]")].find(card => card.dataset.camera === nodeId);
}

function appendBytes(left, right) {
  const combined = new Uint8Array(left.length + right.length);
  combined.set(left);
  combined.set(right, left.length);
  return combined;
}

function findBytes(source, pattern) {
  outer: for (let index = 0; index <= source.length - pattern.length; index += 1) {
    for (let offset = 0; offset < pattern.length; offset += 1) {
      if (source[index + offset] !== pattern[offset]) continue outer;
    }
    return index;
  }
  return -1;
}

function showCameraFrame(nodeId, bytes, mediaType) {
  const card = cameraCard(nodeId);
  if (!card) return;
  const objectUrl = URL.createObjectURL(new Blob([bytes], { type: mediaType }));
  const previous = previewUrls.get(nodeId);
  const image = card.querySelector("img");
  image.src = objectUrl;
  image.hidden = false;
  card.querySelector(".camera-placeholder").hidden = true;
  previewUrls.set(nodeId, objectUrl);
  if (previous) URL.revokeObjectURL(previous);
}

function stopCameraStream(nodeId) {
  const controller = previewStreams.get(nodeId);
  if (controller) controller.abort();
  previewStreams.delete(nodeId);
}

function stopCameraStreams() {
  [...previewStreams.keys()].forEach(stopCameraStream);
}

async function startCameraStream(nodeId) {
  if (!token()) return;
  stopCameraStream(nodeId);
  const card = cameraCard(nodeId);
  if (!card) return;
  const statusElement = card.querySelector("[data-preview-status]");
  const controller = new AbortController();
  previewStreams.set(nodeId, controller);
  statusElement.textContent = "실시간 스트림 연결 중…";
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/preview/stream`, {
      headers: { "X-Supervisor-Token": token() },
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) {
      if (response.status === 401) $("#token-dialog").showModal();
      let detail = `HTTP ${response.status}`;
      try { detail = (await response.json()).detail || detail; } catch {}
      throw new Error(detail);
    }
    if (!response.body) throw new Error("브라우저가 스트리밍 응답을 지원하지 않습니다.");
    const reader = response.body.getReader();
    const separator = new Uint8Array([13, 10, 13, 10]);
    const decoder = new TextDecoder("ascii");
    let buffer = new Uint8Array(0);
    let frameCount = 0;
    let statusAt = performance.now();
    while (true) {
      const { done, value } = await reader.read();
      if (done) throw new Error("카메라 스트림이 종료되었습니다.");
      buffer = appendBytes(buffer, value);
      while (true) {
        const headerEnd = findBytes(buffer, separator);
        if (headerEnd < 0) {
          if (buffer.length > 32768) throw new Error("잘못된 MJPEG 헤더입니다.");
          break;
        }
        const headers = decoder.decode(buffer.slice(0, headerEnd));
        const lengthMatch = headers.match(/content-length:\s*(\d+)/i);
        const typeMatch = headers.match(/content-type:\s*(image\/(?:jpeg|png))/i);
        if (!lengthMatch || !typeMatch) throw new Error("프레임 메타데이터가 없습니다.");
        const frameLength = Number(lengthMatch[1]);
        if (!Number.isSafeInteger(frameLength) || frameLength < 1 || frameLength > 20 * 1024 * 1024) throw new Error("프레임 크기가 올바르지 않습니다.");
        const frameStart = headerEnd + separator.length;
        const frameEnd = frameStart + frameLength;
        if (buffer.length < frameEnd) break;
        showCameraFrame(nodeId, buffer.slice(frameStart, frameEnd), typeMatch[1].toLowerCase());
        buffer = buffer.slice(frameEnd);
        frameCount += 1;
        const now = performance.now();
        if (now - statusAt >= 1000) {
          statusElement.textContent = `실시간 · ${(frameCount * 1000 / (now - statusAt)).toFixed(1)} fps`;
          frameCount = 0;
          statusAt = now;
        }
      }
    }
  } catch (error) {
    if (error.name !== "AbortError") {
      console.warn(`[Pi Node Supervisor] ${nodeId} preview stream`, error);
      statusElement.textContent = `스트림 실패 · ${friendlyError(error)}`;
      card.querySelector(".camera-placeholder").textContent = "카메라 스트림을 가져올 수 없습니다.";
    }
  } finally {
    if (previewStreams.get(nodeId) === controller) previewStreams.delete(nodeId);
  }
}

function startCameraStreams() {
  if (!$("#cameras").classList.contains("active")) return;
  state.nodes.filter(node => node.kind === "capture-node").forEach(node => startCameraStream(node.id));
}
async function refresh() {
  try {
    const health = await api("/api/health");
    $("#connection").textContent = "장치 연결됨";
    $("#connection").className = "pill ok";
    if (!token()) { $("#token-dialog").showModal(); return; }
    const [dashboard, runs, events, data, nodes, configuration] = await Promise.all([
      api("/api/dashboard"), api("/api/runs?limit=100"), api(`/api/events?limit=${state.eventsPageSize}&offset=${state.eventsOffset}`), api(`/api/data?limit=${state.dataPageSize}&offset=${state.dataOffset}`), api("/api/nodes"), api("/api/config/active")
    ]);
    Object.assign(state, { dashboard, runs: runs.items, events: events.items, eventsTotal: events.total ?? events.items.length, data: data.items, dataTotal: data.total ?? data.items.length, nodes: nodes.items });
    loadConfigurationIntoForm(configuration);
    renderCaptureCameraOptions();
    renderSensorNodeOptions();
    renderStorageCameraOptions();
    renderDashboard(); renderEvents(); renderData(); renderNodes(); renderCameras(); startCameraStreams();
    try { await loadSensors(); } catch (error) {
      $("#sensor-data-table").innerHTML = `<div class="friendly-empty error-copy"><strong>센서 데이터를 불러오지 못했습니다.</strong><span>${safe(error.message)}</span></div>`;
      $("#sensor-delivery-table").innerHTML = '<div class="empty">동기화 상태를 확인할 수 없습니다.</div>';
    }
  } catch (error) {
    $("#connection").textContent = "연결 실패"; $("#connection").className = "pill bad";
    toast(error.message, true);
  }
}

document.querySelectorAll(".nav-item").forEach(button => button.addEventListener("click", () => {
  document.querySelectorAll(".nav-item, .view").forEach(item => item.classList.remove("active"));
  button.classList.add("active"); $(`#${button.dataset.view}`).classList.add("active");
  if (button.dataset.view === "cameras") startCameraStreams(); else stopCameraStreams();
  if (["data", "events"].includes(button.dataset.view)) refresh();
  if (button.dataset.view === "sensors") loadSensors().catch(error => toast(error.message, true));
  if (button.dataset.view === "settings") {
    renderCaptureCameraOptions();
    renderSensorNodeOptions();
    renderSensorSettingsNodeOptions();
    renderStorageCameraOptions();
    loadCaptureSchedule();
    loadSensorSchedule();
    loadCameraConfiguration();
  }
}));
document.querySelectorAll("[data-go]").forEach(button => button.addEventListener("click", () => document.querySelector(`[data-view="${button.dataset.go}"]`).click()));
$("#refresh-button").addEventListener("click", refresh);
$("#token-button").addEventListener("click", () => $("#token-dialog").showModal());
$("#save-token").addEventListener("click", event => { event.preventDefault(); sessionStorage.setItem("supervisor-token", $("#token-input").value); $("#token-dialog").close(); refresh(); });
$("#preview-plan").addEventListener("click", async () => { try { state.preview = await api("/api/plan/preview", { method: "POST", body: $("#config-editor").value }); toast("계획 검증이 완료되었습니다."); } catch (error) { toast(error.message, true); } });
$("#save-draft").addEventListener("click", async () => { try { await api("/api/config/draft", { method: "PUT", body: $("#config-editor").value }); toast("Draft를 저장했습니다."); } catch (error) { toast(error.message, true); } });
$("#load-draft").addEventListener("click", async () => { try { $("#config-editor").value = JSON.stringify(await api("/api/config/draft"), null, 2); } catch (error) { toast(error.message, true); } });
$("#load-capture-node-settings").addEventListener("click", async () => {
  try {
    const configuration = JSON.parse($("#config-editor").value);
    configuration.capture_node_settings = await api("/api/settings/capture-nodes");
    $("#config-editor").value = JSON.stringify(configuration, null, 2);
    toast("현재 CaptureNode 설정을 편집기에 가져왔습니다. 저장하거나 적용하기 전 내용을 확인하세요.");
  } catch (error) {
    toast(error instanceof SyntaxError ? "먼저 편집기의 JSON 형식을 확인하세요." : error.message, true);
  }
});
$("#apply-draft").addEventListener("click", async () => { if (!confirm("검증된 Draft를 새 Active revision으로 적용할까요?")) return; try { const result = await api("/api/revisions/apply", { method: "POST" }); toast(`Revision #${result.id}을 적용했습니다.`); refresh(); } catch (error) { toast(error.message, true); } });
$("#refresh-dashboard").addEventListener("click", async () => { await refresh(); toast("노드 상태를 갱신했습니다."); });
$("#sync-data").addEventListener("click", async () => { try { const result = await api("/api/data/sync", { method: "POST" }); toast(result.new_data ? `새 데이터 ${result.new_data}개를 반영했습니다.` : "이미 최신 상태입니다."); refresh(); } catch (error) { toast(error.message, true); } });
$("#refresh-previews").addEventListener("click", startCameraStreams);
$("#events-page-size").addEventListener("change", async event => {
  state.eventsPageSize = Number(event.target.value);
  state.eventsOffset = 0;
  try { await loadEvents(); } catch (error) { toast(error.message, true); }
});
$("#events-prev").addEventListener("click", async () => {
  state.eventsOffset = Math.max(0, state.eventsOffset - state.eventsPageSize);
  try { await loadEvents(); } catch (error) { toast(error.message, true); }
});
$("#events-next").addEventListener("click", async () => {
  if (state.eventsOffset + state.events.length >= state.eventsTotal) return;
  state.eventsOffset += state.eventsPageSize;
  try { await loadEvents(); } catch (error) { toast(error.message, true); }
});
$("#data-page-size").addEventListener("change", async event => {
  state.dataPageSize = Number(event.target.value);
  state.dataOffset = 0;
  try { await loadData(); } catch (error) { toast(error.message, true); }
});
$("#data-prev").addEventListener("click", async () => {
  state.dataOffset = Math.max(0, state.dataOffset - state.dataPageSize);
  try { await loadData(); } catch (error) { toast(error.message, true); }
});
$("#data-next").addEventListener("click", async () => {
  if (state.dataOffset + state.data.length >= state.dataTotal) return;
  state.dataOffset += state.dataPageSize;
  try { await loadData(); } catch (error) { toast(error.message, true); }
});
$("#data-table").addEventListener("click", event => {
  const view = event.target.closest("[data-view-data]");
  if (view) {
    const item = state.data.find(candidate => candidate.data_id === view.dataset.viewData);
    const location = item?.storage_location || item?.summary || "원본 이미지";
    accessDataOriginal(view.dataset.viewData, { filename: location.split("/").at(-1) || "원본 이미지" });
    return;
  }
  const download = event.target.closest("[data-download-data]");
  if (download) accessDataOriginal(download.dataset.downloadData, { download: true, filename: download.dataset.dataName });
});
$("#close-data-view").addEventListener("click", closeDataViewer);
$("#data-view-dialog").addEventListener("click", event => {
  if (event.target === event.currentTarget) closeDataViewer();
});
$("#data-view-dialog").addEventListener("close", () => {
  $("#data-view-image").removeAttribute("src");
  if (dataViewUrl) URL.revokeObjectURL(dataViewUrl);
  dataViewUrl = null;
});
$("#camera-grid").addEventListener("click", event => {
  const button = event.target.closest("[data-preview-refresh]");
  if (button) startCameraStream(button.dataset.previewRefresh);
});

$("#capture-schedule-camera").addEventListener("change", () => loadCaptureSchedule());
$("#capture-schedule-mode").addEventListener("change", updateCaptureScheduleVisibility);
$("#add-capture-range").addEventListener("click", () => {
  const ranges = readCaptureRanges();
  const previous = ranges.at(-1) || DEFAULT_CAPTURE_RANGE;
  const start = Math.min(captureTimeMinutes(previous.end) + 60, 1439);
  const end = Math.min(start + 60, 1439);
  ranges.push({ start: minutesToCaptureTime(start), intervalMinutes: Number(previous.intervalMinutes) || 10, end: minutesToCaptureTime(end) });
  renderCaptureRanges(ranges);
});
$("#capture-range-list").addEventListener("click", event => {
  const button = event.target.closest("[data-remove-capture-range]");
  if (!button) return;
  const ranges = readCaptureRanges();
  ranges.splice(Number(button.dataset.removeCaptureRange), 1);
  renderCaptureRanges(ranges.length ? ranges : [DEFAULT_CAPTURE_RANGE]);
});
$("#generate-daily-times").addEventListener("click", () => {
  try {
    const times = [...new Set(readCaptureRanges().flatMap(generateCaptureTimes))]
      .sort((left, right) => captureTimeSeconds(left) - captureTimeSeconds(right));
    renderDailyTimes(times);
  } catch (error) { toast(error.message, true); }
});
$("#clear-daily-times").addEventListener("click", () => renderDailyTimes([]));
$("#add-daily-time").addEventListener("click", () => addDailyTime());
$("#daily-time-list").addEventListener("click", event => {
  const button = event.target.closest("[data-remove-daily-time]");
  if (!button) return;
  const values = [...$("#daily-time-list").querySelectorAll('input[type="time"]')].map(input => input.value);
  values.splice(Number(button.dataset.removeDailyTime), 1);
  renderDailyTimes(values);
});
$("#capture-schedule-form").addEventListener("submit", async event => {
  event.preventDefault();
  const nodeId = $("#capture-schedule-camera").value;
  let schedule;
  try { schedule = captureScheduleFromForm(); } catch (error) { toast(error.message, true); return; }
  const targetLabel = nodeId === "common" ? "모든 카메라" : nodeId;
  const powerPlanDisabled = schedule.mode === "daily" && schedule.daily_times.length === 0;
  const hasPowerKeep = (state.configuration?.power_keep_schedules || []).length > 0;
  const confirmation = powerPlanDisabled
    ? hasPowerKeep
      ? "촬영 시각이 비어 있습니다. 촬영용 전원 계획은 비활성화되지만 별도 전원 유지 일정은 계속 작동합니다. 계속할까요?"
      : "촬영 시각이 비어 있습니다. 적용하면 촬영과 자동 전원 계획이 모두 비활성화됩니다. 계속할까요?"
    : `${targetLabel}의 촬영 일정을 적용할까요?`;
  if (!nodeId || !confirm(confirmation)) return;
  const button = $("#apply-capture-schedule");
  button.disabled = true;
  $("#capture-schedule-note").textContent = "촬영 일정을 확인하는 중입니다…";
  try {
    const result = await api("/api/capture-schedules", { method: "PUT", body: JSON.stringify({ target: nodeId, settings: schedule }) });
    $("#capture-schedule-note").textContent = `촬영 일정 적용 완료 · 설정 #${result.revision.id}`;
    toast(`${targetLabel} 촬영 일정을 적용했습니다.`);
    await refresh();
  } catch (error) {
    $("#capture-schedule-note").textContent = "적용하지 못했습니다. 기존 촬영 일정은 유지됩니다.";
    toast(error.message, true);
  } finally { button.disabled = false; }
});

$("#sensor-schedule-node").addEventListener("change", () => loadSensorSchedule());
$("#sensor-schedule-mode").addEventListener("change", updateSensorScheduleVisibility);
$("#add-sensor-range").addEventListener("click", () => {
  const ranges = readSensorRanges();
  const previous = ranges.at(-1) || DEFAULT_SENSOR_RANGE;
  const start = Math.min(captureTimeMinutes(previous.end) + 30, 1439);
  const end = Math.min(start + 30, 1439);
  ranges.push({ start: minutesToCaptureTime(start), intervalMinutes: Number(previous.intervalMinutes) || 30, end: minutesToCaptureTime(end) });
  renderSensorRanges(ranges);
});
$("#sensor-range-list").addEventListener("click", event => {
  const button = event.target.closest("[data-remove-sensor-range]");
  if (!button) return;
  const ranges = readSensorRanges();
  ranges.splice(Number(button.dataset.removeSensorRange), 1);
  renderSensorRanges(ranges.length ? ranges : [DEFAULT_SENSOR_RANGE]);
});
$("#generate-sensor-times").addEventListener("click", () => {
  try {
    const times = [...new Set(readSensorRanges().flatMap(generateSensorTimes))]
      .sort((left, right) => captureTimeSeconds(left) - captureTimeSeconds(right));
    renderSensorTimes(times);
  } catch (error) { toast(error.message, true); }
});
$("#clear-sensor-times").addEventListener("click", () => renderSensorTimes([]));
$("#add-sensor-time").addEventListener("click", () => {
  const values = [...$("#sensor-time-list").querySelectorAll('input[type="time"]')].map(input => input.value);
  values.push("00:00:00");
  renderSensorTimes(values);
});
$("#sensor-time-list").addEventListener("click", event => {
  const button = event.target.closest("[data-remove-sensor-time]");
  if (!button) return;
  const values = [...$("#sensor-time-list").querySelectorAll('input[type="time"]')].map(input => input.value);
  values.splice(Number(button.dataset.removeSensorTime), 1);
  renderSensorTimes(values);
});
$("#sensor-schedule-form").addEventListener("submit", async event => {
  event.preventDefault();
  const nodeId = $("#sensor-schedule-node").value;
  let schedule;
  try { schedule = sensorScheduleFromForm(); } catch (error) { toast(error.message, true); return; }
  const targetLabel = nodeId === "common" ? "모든 센서" : nodeId;
  const empty = schedule.mode === "daily" && schedule.daily_times.length === 0;
  const confirmation = empty
    ? "수집 시각이 비어 있습니다. 센서 수집과 해당 전원 계획이 비활성화됩니다. 계속할까요?"
    : `${targetLabel}의 온·습도 수집 일정을 적용할까요?`;
  if (!nodeId || !confirm(confirmation)) return;
  const button = $("#apply-sensor-schedule");
  button.disabled = true;
  $("#sensor-schedule-note").textContent = "센서 일정을 확인하는 중입니다…";
  try {
    const result = await api("/api/sensor-schedules", { method: "PUT", body: JSON.stringify({ target: nodeId, settings: schedule }) });
    $("#sensor-schedule-note").textContent = `센서 일정 적용 완료 · 설정 #${result.revision.id}`;
    toast(`${targetLabel} 수집 일정을 적용했습니다.`);
    await refresh();
  } catch (error) {
    $("#sensor-schedule-note").textContent = "적용하지 못했습니다. 기존 센서 일정은 유지됩니다.";
    toast(error.message, true);
  } finally { button.disabled = false; }
});

$("#add-power-keep").addEventListener("click", () => {
  const items = [...$("#power-keep-list").querySelectorAll("[data-power-keep-id]")].map(row => ({
    id: row.dataset.powerKeepId,
    start: row.querySelector("[data-power-keep-start]").value,
    end: row.querySelector("[data-power-keep-end]").value,
    timezone: state.configuration?.timezone || "Asia/Seoul",
  }));
  const usedIds = new Set(items.map(item => item.id));
  let suffix = 1;
  while (usedIds.has(`daily-power-keep-${suffix}`)) suffix += 1;
  items.push({ id: `daily-power-keep-${suffix}`, start: "09:00:00", end: "10:00:00", timezone: state.configuration?.timezone || "Asia/Seoul" });
  renderPowerKeepRows(items);
});
$("#power-keep-list").addEventListener("click", event => {
  const button = event.target.closest("[data-remove-power-keep]");
  if (!button) return;
  const rows = [...$("#power-keep-list").querySelectorAll("[data-power-keep-id]")];
  const items = rows.map(row => ({
    id: row.dataset.powerKeepId,
    start: row.querySelector("[data-power-keep-start]").value,
    end: row.querySelector("[data-power-keep-end]").value,
    timezone: state.configuration?.timezone || "Asia/Seoul",
  }));
  items.splice(Number(button.dataset.removePowerKeep), 1);
  renderPowerKeepRows(items);
});
$("#power-settings-form").addEventListener("submit", async event => {
  event.preventDefault();
  let document;
  try { document = powerSettingsFromForm(); } catch (error) { toast(error.message, true); return; }
  if (!confirm("전원 설정을 적용할까요?")) return;
  const button = event.submitter || event.currentTarget.querySelector('button[type="submit"]');
  button.disabled = true;
  $("#power-settings-note").textContent = "전원 설정을 확인하는 중입니다…";
  try {
    const result = await api("/api/power-settings", { method: "PUT", body: JSON.stringify(document) });
    $("#power-settings-note").textContent = `전원 설정 적용 완료 · 설정 #${result.revision.id}`;
    toast("전원 설정을 적용했습니다.");
    await refresh();
  } catch (error) {
    $("#power-settings-note").textContent = "적용하지 못했습니다. 기존 전원 설정은 유지됩니다.";
    toast(error.message, true);
  } finally { button.disabled = false; }
});

$("#refresh-sensors").addEventListener("click", async () => {
  try { await loadSensors(); toast("센서 상태를 갱신했습니다."); } catch (error) { toast(error.message, true); }
});
$("#sync-sensor-storage").addEventListener("click", async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await api("/api/sensor-storage/sync", { method: "POST" });
    await loadSensors();
    toast("전송 가능한 CSV의 원격 복제를 다시 시도했습니다.");
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});
$("#sensor-settings-node").addEventListener("change", () => loadSensorSettingsIntoForm());
$("#add-sensor-mirror").addEventListener("click", () => openSensorMirrorEditor());
$("#sensor-mirror-type").addEventListener("change", () => {
  $("#sensor-mirror-port").value = $("#sensor-mirror-type").value === "ftps" ? "21" : $("#sensor-mirror-type").value === "sftp" ? "22" : "";
  updateSensorMirrorFields();
});
$("#cancel-sensor-mirror").addEventListener("click", () => { $("#sensor-mirror-editor").hidden = true; });
$("#save-sensor-mirror").addEventListener("click", () => {
  try {
    const item = sensorMirrorFromEditor();
    const index = Number($("#sensor-mirror-edit-index").value);
    if (state.sensorMirrorDraft.some((stored, storedIndex) => stored.name === item.name && storedIndex !== index)) throw new Error("같은 이름의 복제 대상이 이미 있습니다.");
    if (index >= 0) state.sensorMirrorDraft[index] = item; else state.sensorMirrorDraft.push(item);
    $("#sensor-mirror-editor").hidden = true;
    renderSensorMirrorList();
  } catch (error) { toast(error.message, true); }
});
$("#sensor-mirror-list").addEventListener("click", event => {
  const edit = event.target.closest("[data-edit-sensor-mirror]");
  if (edit) { openSensorMirrorEditor(Number(edit.dataset.editSensorMirror)); return; }
  const remove = event.target.closest("[data-remove-sensor-mirror]");
  if (!remove) return;
  const item = state.sensorMirrorDraft[Number(remove.dataset.removeSensorMirror)];
  if (!confirm(`‘${item.name}’ 복제 대상을 삭제할까요?`)) return;
  state.sensorMirrorDraft.splice(Number(remove.dataset.removeSensorMirror), 1);
  renderSensorMirrorList();
});
$("#sensor-settings-form").addEventListener("submit", async event => {
  event.preventDefault();
  const nodeId = $("#sensor-settings-node").value;
  let document;
  try { document = sensorSettingsFromForm(); } catch (error) { toast(error.message, true); return; }
  if (!nodeId || !confirm(`${nodeId}의 CWT·CSV 설정을 저장할까요?\n현재 수집 프로세스에는 서비스 재시작 후 반영됩니다.`)) return;
  const button = $("#apply-sensor-settings");
  button.disabled = true;
  $("#sensor-settings-note").textContent = "설정을 검증하고 nodes.json에 원자적으로 저장하는 중입니다…";
  try {
    const result = await api(`/api/sensor-settings/${encodeURIComponent(nodeId)}`, { method: "PUT", body: JSON.stringify(document) });
    await loadSensors();
    $("#sensor-settings-note").textContent = `저장 완료 · ${result.backup}에서 복구 가능 · 서비스 재시작 필요`;
    toast("센서 설정을 저장했습니다. 운영 확인 후 서비스를 재시작하세요.");
  } catch (error) {
    $("#sensor-settings-note").textContent = "저장하지 못했습니다. 기존 nodes.json은 유지됩니다.";
    toast(error.message, true);
  } finally { button.disabled = false; }
});

$("#storage-camera").addEventListener("change", () => {
  $("#storage-editor").hidden = true;
  loadCameraConfiguration();
});
$("#add-storage").addEventListener("click", () => openStorageEditor());
$("#storage-type").addEventListener("change", () => {
  $("#storage-port").value = $("#storage-type").value === "sftp" ? "22" : $("#storage-type").value === "ftps" ? "21" : "";
  updateStorageFieldVisibility();
});
$("#storage-retention-enabled").addEventListener("change", updateStorageFieldVisibility);
$("#cancel-storage").addEventListener("click", () => { $("#storage-editor").hidden = true; });
$("#storage-editor").addEventListener("submit", event => {
  event.preventDefault();
  try {
    const item = storageFromEditor();
    const index = Number($("#storage-edit-index").value);
    if (state.storageDraft.some((stored, storedIndex) => stored.name === item.name && storedIndex !== index)) throw new Error("같은 이름의 저장소가 이미 있습니다.");
    if (index >= 0) state.storageDraft[index] = item; else state.storageDraft.push(item);
    $("#storage-editor").hidden = true;
    renderStorageList();
  } catch (error) { toast(error.message, true); }
});
$("#storage-list").addEventListener("click", event => {
  const edit = event.target.closest("[data-edit-storage]");
  if (edit) { openStorageEditor(Number(edit.dataset.editStorage)); return; }
  const remove = event.target.closest("[data-remove-storage]");
  if (!remove) return;
  const item = state.storageDraft[Number(remove.dataset.removeStorage)];
  if (!confirm(`‘${item.name}’ 저장소를 목록에서 삭제할까요?\n‘저장소 적용’을 눌러야 장치에 반영됩니다.`)) return;
  state.storageDraft.splice(Number(remove.dataset.removeStorage), 1);
  renderStorageList();
});
$("#apply-storages").addEventListener("click", async () => {
  const nodeId = $("#storage-camera").value;
  const targetLabel = nodeId === "common" ? "모든 카메라" : nodeId;
  if (!nodeId || !confirm(`${targetLabel}의 저장소 설정을 적용할까요?\n연결 확인을 위해 관리 중인 카메라가 잠시 재시작됩니다.`)) return;
  const button = $("#apply-storages");
  button.disabled = true;
  $("#storage-restart-note").textContent = "설정을 검증하고 카메라를 재시작하는 중입니다…";
  try {
    const path = nodeId === "common" ? "/api/settings/cameras/storages" : `/api/nodes/${encodeURIComponent(nodeId)}/storages`;
    const items = state.storageDraft.map(({ retention_days, ...item }) => item);
    const retention = state.storageDraft
      .filter(item => item.type === "file" && item.retention_days)
      .map(item => ({ target: item.name, max_age_days: item.retention_days }));
    const result = await api(path, { method: "PUT", body: JSON.stringify({ items, retention }) });
    await loadCameraConfiguration(nodeId);
    $("#storage-restart-note").textContent = `적용 완료 · ${result.restarted_nodes.join(", ")} 정상 재시작`;
    toast(`${targetLabel} 저장소를 안전하게 적용했습니다.`);
    await refresh();
  } catch (error) {
    $("#storage-restart-note").textContent = "적용하지 못했습니다. 기존 설정은 자동으로 복원됩니다.";
    toast(error.message, true);
  } finally { button.disabled = false; }
});

$("#config-editor").value = JSON.stringify({ schema_version: 1, timezone: "Asia/Seoul", policies: { "boot-lead-seconds": 120, "shutdown-grace-seconds": 60, "merge-power-gaps-shorter-than-seconds": 300 }, activities: [] }, null, 2);
updateCaptureScheduleVisibility();
refresh();
