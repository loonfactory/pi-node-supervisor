import { useEffect, useState } from "react";

import { captureNodeApi } from "../api/captureNodeApi";

export function useRuntimeStatus() {
  const [runtimeStatus, setRuntimeStatus] = useState({
    status: "connecting",
    cameraConnected: false,
    cameraDetail: null,
  });

  const reload = async () => {
    try {
      setRuntimeStatus(await captureNodeApi.health());
    } catch {
      setRuntimeStatus({
        status: "offline",
        cameraConnected: false,
        cameraDetail: "Runtime is unavailable",
      });
    }
  };

  useEffect(() => {
    reload();
    const timer = window.setInterval(reload, 1500);
    return () => window.clearInterval(timer);
  }, []);

  const serverOnline = runtimeStatus.status === "running";
  const cameraOnline = serverOnline && runtimeStatus.cameraConnected;
  const label =
    runtimeStatus.status === "connecting"
      ? "Connecting"
      : !serverOnline
        ? "Disconnected"
        : cameraOnline
          ? "Running"
          : "Camera disconnected";

  return {
    status: { serverOnline, cameraOnline, label },
    cameraOnline,
    reload,
  };
}
