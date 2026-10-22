import { useActivityFeed } from "./useActivityFeed";
import { useCaptureSettings } from "./useCaptureSettings";
import { useManualCapture } from "./useManualCapture";
import { useRuntimeStatus } from "./useRuntimeStatus";
import { useStorageBrowser } from "./useStorageBrowser";

export function useCaptureNode() {
  const runtime = useRuntimeStatus();
  const configuration = useCaptureSettings({
    cameraOnline: runtime.cameraOnline,
    reloadHealth: runtime.reload,
  });
  const activity = useActivityFeed(() =>
    configuration.loadControls({ preserveDirty: true }),
  );
  const manualCapture = useManualCapture({
    loadControls: configuration.loadControls,
    refreshActivity: activity.refreshAfterCapture,
    reportError: configuration.reportError,
  });
  const storage = useStorageBrowser(configuration.reportError);

  return {
    status: runtime.status,
    preview: {
      ...configuration.preview,
      cameraOnline: runtime.cameraOnline,
      ...manualCapture,
    },
    settings: {
      ...configuration.settings,
      openStorage: storage.openBrowser,
    },
    events: activity.model,
    storage: storage.model,
  };
}
