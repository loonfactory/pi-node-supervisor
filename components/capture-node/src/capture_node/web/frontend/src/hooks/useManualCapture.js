import { useState } from "react";

import { captureNodeApi } from "../api/captureNodeApi";

export function useManualCapture({ loadControls, refreshActivity, reportError }) {
  const [capturing, setCapturing] = useState(false);

  const captureNow = async () => {
    reportError("");
    setCapturing(true);
    try {
      const body = await captureNodeApi.capture();
      await loadControls({ preserveDirty: true });
      await refreshActivity(body.captureId);
    } catch (cause) {
      reportError(cause.message);
    } finally {
      setCapturing(false);
    }
  };

  return { capturing, captureNow };
}
