import { state } from "./state.js";
import { $, friendlyError, token } from "./presentation.js";

const previewUrls = new Map();
const previewStreams = new Map();

function cameraCard(nodeId) {
  return [...document.querySelectorAll("[data-camera]")]
    .find(card => card.dataset.camera === nodeId);
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

export function stopCameraStreams() {
  [...previewStreams.keys()].forEach(stopCameraStream);
}

export async function startCameraStream(nodeId) {
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
        if (!Number.isSafeInteger(frameLength) || frameLength < 1 || frameLength > 20 * 1024 * 1024) {
          throw new Error("프레임 크기가 올바르지 않습니다.");
        }
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

export function startCameraStreams() {
  if (!$("#cameras").classList.contains("active")) return;
  state.nodes
    .filter(node => node.kind === "capture-node")
    .forEach(node => startCameraStream(node.id));
}
