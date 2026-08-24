export const $ = selector => document.querySelector(selector);
export const token = () => sessionStorage.getItem("supervisor-token") || "";

export function friendlyError(error) {
  const message = error?.message || String(error);
  return error instanceof TypeError && /fetch|network/i.test(message)
    ? "장치 연결이 잠시 끊겼습니다. 잠시 후 다시 갱신해 주세요."
    : message;
}

export function toast(message, error = false) {
  if (error) console.warn("[Pi Node Supervisor]", message);
  const element = $("#toast");
  element.textContent = message;
  element.className = error ? "show error" : "show";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.className = "", 3200);
}

export function esc(value) {
  return String(value).replace(
    /[&<>"']/g,
    char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char],
  );
}

export function safe(value) {
  return value == null ? "—" : esc(value);
}

export function fmt(value) {
  if (!value) return "—";
  const date = new Date(value);
  return esc(Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString());
}

export function status(value) {
  const labels = {
    healthy: "정상", unhealthy: "확인 필요", running: "실행 중", succeeded: "완료",
    failed: "실패", interrupted: "중단됨", missed: "놓침", info: "안내",
    warning: "주의", error: "오류", pending: "대기", not_queued: "미전송",
    synchronized: "동기화", "local-only": "로컬만",
  };
  return `<span class="status ${value}">${safe(labels[value] || value)}</span>`;
}

export function table(headers, rows) {
  if (!rows.length) return '<div class="empty">표시할 항목이 없습니다.</div>';
  return `<table><thead><tr>${headers.map(header => `<th>${header}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table>`;
}
