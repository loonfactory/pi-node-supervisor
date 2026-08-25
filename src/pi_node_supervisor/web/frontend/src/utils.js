export const DEFAULT_CONFIGURATION = {
  schema_version: 1,
  timezone: "Asia/Seoul",
  policies: {
    "boot-lead-seconds": 120,
    "shutdown-grace-seconds": 60,
    "merge-power-gaps-shorter-than-seconds": 300,
    "minimum-lead-seconds": 60,
    "clock-tolerance-seconds": 5,
    "recurrence-horizon-hours": 168,
  },
  activities: [],
  capture_schedules: [],
  sensor_schedules: [],
  power_keep_schedules: [],
  capture_node_settings: { common: {}, nodes: {} },
};

export const DEFAULT_CAPTURE_RANGE = {
  start: "05:00",
  intervalMinutes: 30,
  end: "20:00",
};
export const DEFAULT_SENSOR_RANGE = {
  start: "00:00",
  intervalMinutes: 30,
  end: "23:30",
};
export const DEFAULT_POWER_KEEP_SCHEDULES = [
  {
    id: "default-morning-power",
    start: "08:50:00",
    end: "09:30:00",
    timezone: "Asia/Seoul",
  },
  {
    id: "default-evening-power",
    start: "17:20:00",
    end: "18:10:00",
    timezone: "Asia/Seoul",
  },
];

export function friendlyError(error) {
  const message = error?.message || String(error);
  return error instanceof TypeError && /fetch|network/i.test(message)
    ? "장치 연결이 잠시 끊겼습니다. 잠시 후 다시 갱신해 주세요."
    : message;
}

export function fmt(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString();
}

export function formatBytes(value) {
  if (value == null || !Number.isFinite(Number(value)) || Number(value) < 0)
    return "—";
  const bytes = Number(value);
  const units = ["B", "KB", "MB", "GB", "TB"];
  if (bytes < 1024) return `${bytes.toLocaleString()} B`;
  const index = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  const scaled = bytes / 1024 ** index;
  return `${scaled.toLocaleString(undefined, { maximumFractionDigits: scaled >= 10 ? 1 : 2 })} ${units[index]}`;
}

export function captureTimeSeconds(value) {
  const parts = String(value).split(":").map(Number);
  const [hours, minutes, seconds = 0] = parts;
  if (
    ![hours, minutes, seconds].every(Number.isInteger) ||
    hours < 0 ||
    hours > 23 ||
    minutes < 0 ||
    minutes > 59 ||
    seconds < 0 ||
    seconds > 59
  ) {
    throw new Error("올바른 시각을 입력해 주세요.");
  }
  return hours * 3600 + minutes * 60 + seconds;
}

export function splitInterval(totalSeconds) {
  let remaining = Number(totalSeconds || 0);
  const days = Math.floor(remaining / 86400);
  remaining -= days * 86400;
  const hours = Math.floor(remaining / 3600);
  remaining -= hours * 3600;
  const minutes = Math.floor(remaining / 60);
  const seconds = Number((remaining - minutes * 60).toFixed(2));
  return { days, hours, minutes, seconds };
}

export function intervalSeconds(parts) {
  return (
    Number(parts.days || 0) * 86400 +
    Number(parts.hours || 0) * 3600 +
    Number(parts.minutes || 0) * 60 +
    Number(parts.seconds || 0)
  );
}

export function minutesToTime(value) {
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}

export function generateTimes(range, label = "촬영") {
  const start = Math.floor(captureTimeSeconds(range.start) / 60);
  const end = Math.floor(captureTimeSeconds(range.end) / 60);
  const interval = Number(range.intervalMinutes);
  if (!Number.isInteger(interval) || interval <= 0)
    throw new Error(`${label} 간격은 1분 이상의 정수여야 합니다.`);
  if (end < start)
    throw new Error("종료 시각은 시작 시각보다 빠를 수 없습니다.");
  const times = [];
  for (let minute = start; minute <= end; minute += interval)
    times.push(`${minutesToTime(minute)}:00`);
  return times;
}

export function rangesFromTimes(times, fallback) {
  if (!times?.length) return [{ ...fallback }];
  const seconds = [...new Set(times)]
    .map(captureTimeSeconds)
    .sort((a, b) => a - b);
  const groups = [];
  let group = [];
  let interval = null;
  for (const value of seconds) {
    const delta = group.length ? value - group.at(-1) : null;
    if (group.length > 1 && delta !== interval) {
      groups.push(group);
      group = [];
      interval = null;
    }
    if (group.length === 1) interval = delta;
    group.push(value);
  }
  if (group.length) groups.push(group);
  const ranges = groups.map((values) => {
    const step = values.length > 1 ? values[1] - values[0] : null;
    if (!step || values.some((value) => value % 60) || step % 60) return null;
    return {
      start: minutesToTime(values[0] / 60),
      intervalMinutes: step / 60,
      end: minutesToTime(values.at(-1) / 60),
    };
  });
  return ranges.every(Boolean) ? ranges : [{ ...fallback }];
}
