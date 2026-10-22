async function requiredJson(path, options, fallbackMessage) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || fallbackMessage);
  return body;
}

async function optionalJson(path) {
  const response = await fetch(path);
  return response.ok ? response.json() : null;
}

export const captureNodeApi = {
  config() {
    return requiredJson("/api/config", undefined, "Configuration is unavailable");
  },
  controls() {
    return optionalJson("/api/camera/controls");
  },
  devices() {
    return optionalJson("/api/camera/devices");
  },
  events(page) {
    return optionalJson(`/api/events?page=${page}&pageSize=10`);
  },
  health() {
    return requiredJson("/api/health", undefined, "Runtime is unavailable");
  },
  updateConfig(document) {
    return requiredJson(
      "/api/config",
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(document),
      },
      "Update failed",
    );
  },
  updateCamera(document) {
    return requiredJson(
      "/api/camera",
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(document),
      },
      "Camera connection failed",
    );
  },
  capture() {
    return requiredJson(
      "/api/capture",
      { method: "POST" },
      "Capture failed",
    );
  },
  storageTargets() {
    return requiredJson(
      "/api/storage/targets",
      undefined,
      "Storage is unavailable",
    );
  },
  storageFiles(target, page) {
    return requiredJson(
      `/api/storage/files?target=${encodeURIComponent(target)}&page=${page}&pageSize=12`,
      undefined,
      "Stored files are unavailable",
    );
  },
};
