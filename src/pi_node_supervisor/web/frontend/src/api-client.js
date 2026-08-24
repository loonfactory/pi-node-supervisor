import { $, friendlyError, token } from "./presentation.js";

export async function api(path, options = {}) {
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
