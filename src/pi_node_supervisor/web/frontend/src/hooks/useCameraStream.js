import { useCallback, useEffect, useState } from "react";

import { api } from "../api-client.js";
import { friendlyError } from "../utils.js";

const STATUS = {
  idle: "대기 중",
  connecting: "실시간 스트림 연결 중…",
  streaming: "실시간 스트림 연결됨",
  retrying: "연결 재시도 중…",
  unauthorized: "인증이 필요합니다.",
};

export function useCameraStream(nodeId, enabled, authenticationVersion) {
  const [state, setState] = useState({ src: null, phase: "idle", error: null });
  const [generation, setGeneration] = useState(0);
  const reconnect = useCallback(() => setGeneration((value) => value + 1), []);

  useEffect(() => {
    if (!enabled || !nodeId) {
      setState({ src: null, phase: "idle", error: null });
      return undefined;
    }
    let active = true;
    const controller = new AbortController();
    setState({ src: null, phase: "connecting", error: null });
    api("/api/camera-session", { method: "POST", signal: controller.signal })
      .then(() => {
        if (active)
          setState({
            src: `/api/camera/${encodeURIComponent(nodeId)}`,
            phase: "connecting",
            error: null,
          });
      })
      .catch((error) => {
        if (active)
          setState({
            src: null,
            phase: error.status === 401 ? "unauthorized" : "retrying",
            error: friendlyError(error),
          });
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [enabled, generation, nodeId, authenticationVersion]);

  const onError = useCallback(() => {
    setState({
      src: null,
      phase: "retrying",
      error: "프리뷰 연결이 끊겼습니다.",
    });
  }, []);
  const onLoad = useCallback(() => {
    setState((current) =>
      current.src ? { ...current, phase: "streaming" } : current,
    );
  }, []);

  useEffect(() => {
    if (!enabled || state.phase !== "retrying") return undefined;
    const timer = setTimeout(reconnect, 1500);
    return () => clearTimeout(timer);
  }, [enabled, state.phase, reconnect]);

  return {
    ...state,
    status: STATUS[state.phase],
    generation,
    reconnect,
    onError,
    onLoad,
  };
}
