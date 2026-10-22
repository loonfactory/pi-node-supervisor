import { useCallback, useEffect, useState } from "react";

import { api } from "../api-client.js";
import { friendlyError } from "../utils.js";

export function useCameraStream(nodeId, enabled) {
  const [state, setState] = useState({
    src: null,
    status: "대기 중",
    error: null,
  });
  const [generation, setGeneration] = useState(0);
  const reconnect = useCallback(() => setGeneration((value) => value + 1), []);

  useEffect(() => {
    if (!enabled || !nodeId) return undefined;
    let active = true;
    let retryTimer;
    setState({ src: null, status: "실시간 스트림 연결 중…", error: null });
    api("/api/camera-session", { method: "POST" })
      .then(() => {
        if (active)
          setState({
            src: `/api/camera/${encodeURIComponent(nodeId)}`,
            status: "실시간 스트림 연결됨",
            error: null,
          });
      })
      .catch((error) => {
        if (!active) return;
        setState({
          src: null,
          status: "연결 실패",
          error: friendlyError(error),
        });
        if (error.status !== 401) retryTimer = setTimeout(reconnect, 1500);
      });
    return () => {
      active = false;
      clearTimeout(retryTimer);
    };
  }, [enabled, generation, nodeId, reconnect]);

  const onError = useCallback(() => {
    setState({
      src: null,
      status: "연결 재시도 중…",
      error: "프리뷰 연결이 끊겼습니다.",
    });
  }, []);

  useEffect(() => {
    if (!state.error || state.status !== "연결 재시도 중…") return undefined;
    const timer = setTimeout(reconnect, 1500);
    return () => clearTimeout(timer);
  }, [state.error, state.status, reconnect]);

  return { ...state, generation, reconnect, onError };
}
