import { useCallback, useEffect, useRef, useState } from "react";

import { token } from "../api-client.js";
import { friendlyError } from "../utils.js";

function concat(left, right) {
  const value = new Uint8Array(left.length + right.length);
  value.set(left);
  value.set(right, left.length);
  return value;
}
function findBytes(source, target) {
  outer: for (
    let start = 0;
    start <= source.length - target.length;
    start += 1
  ) {
    for (let index = 0; index < target.length; index += 1)
      if (source[start + index] !== target[index]) continue outer;
    return start;
  }
  return -1;
}

export function useCameraStream(nodeId, enabled) {
  const [state, setState] = useState({
    src: null,
    status: "대기 중",
    error: null,
  });
  const [generation, setGeneration] = useState(0);
  const currentUrl = useRef(null);
  const reconnect = useCallback(() => setGeneration((value) => value + 1), []);
  useEffect(() => {
    if (!enabled || !nodeId || !token()) return undefined;
    const controller = new AbortController();
    let active = true;
    let retryTimer = null;
    setState((current) => ({
      ...current,
      status: "실시간 스트림 연결 중…",
      error: null,
    }));
    (async () => {
      try {
        const response = await fetch(
          `/api/nodes/${encodeURIComponent(nodeId)}/preview/stream`,
          {
            headers: { Authorization: `Bearer ${token()}` },
            cache: "no-store",
            signal: controller.signal,
          },
        );
        if (!response.ok || !response.body)
          throw new Error(`HTTP ${response.status}`);
        const reader = response.body.getReader();
        const separator = new Uint8Array([13, 10, 13, 10]);
        const decoder = new TextDecoder("ascii");
        let buffer = new Uint8Array(0);
        let frames = 0;
        let started = performance.now();
        while (active) {
          const { done, value } = await reader.read();
          if (done) throw new Error("카메라 스트림이 종료되었습니다.");
          buffer = concat(buffer, value);
          if (buffer.length > 16 * 1024 * 1024)
            throw new Error("프리뷰 버퍼가 제한을 초과했습니다.");
          for (;;) {
            const headerEnd = findBytes(buffer, separator);
            if (headerEnd < 0) break;
            const header = decoder.decode(buffer.slice(0, headerEnd));
            const length = Number(
              header.match(/content-length:\s*(\d+)/i)?.[1],
            );
            const mediaType = header.match(
              /content-type:\s*(image\/(?:jpeg|png))/i,
            )?.[1];
            if (!Number.isSafeInteger(length) || !mediaType)
              throw new Error("잘못된 MJPEG 프레임입니다.");
            const bodyStart = headerEnd + separator.length;
            if (buffer.length < bodyStart + length) break;
            const url = URL.createObjectURL(
              new Blob([buffer.slice(bodyStart, bodyStart + length)], {
                type: mediaType,
              }),
            );
            buffer = buffer.slice(bodyStart + length);
            if (currentUrl.current) URL.revokeObjectURL(currentUrl.current);
            currentUrl.current = url;
            frames += 1;
            const now = performance.now();
            const status =
              now - started >= 1000
                ? `실시간 · ${((frames * 1000) / (now - started)).toFixed(1)} fps`
                : "실시간";
            if (now - started >= 1000) {
              frames = 0;
              started = now;
            }
            if (active) setState({ src: url, status, error: null });
          }
        }
      } catch (error) {
        if (active && error.name !== "AbortError") {
          setState((current) => ({
            ...current,
            status: `연결 재시도 중 · ${friendlyError(error)}`,
            error: friendlyError(error),
          }));
          retryTimer = setTimeout(
            () => setGeneration((value) => value + 1),
            1500,
          );
        }
      }
    })();
    return () => {
      active = false;
      if (retryTimer) clearTimeout(retryTimer);
      controller.abort();
      if (currentUrl.current) URL.revokeObjectURL(currentUrl.current);
      currentUrl.current = null;
    };
  }, [enabled, generation, nodeId]);
  return { ...state, reconnect };
}
