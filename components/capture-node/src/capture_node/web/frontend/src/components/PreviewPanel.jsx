import {
  Activity,
  Camera,
  HardDrive,
  RefreshCw,
  Wifi,
  WifiOff,
} from "lucide-react";

import Metric from "./Metric";

export default function PreviewPanel({ model }) {
  return (
    <div className="preview-pane">
      <div className="section-heading">
        <div>
          <span className="eyebrow">Live source</span>
          <h1>Camera preview</h1>
        </div>
        <div className="heading-actions">
          <button
            className="icon-button"
            type="button"
            title="Capture now"
            aria-label="Capture now"
            disabled={model.capturing || !model.cameraOnline}
            onClick={model.captureNow}
          >
            {model.capturing ? (
              <RefreshCw className="spin" size={18} />
            ) : (
              <Camera size={18} />
            )}
          </button>
          <button
            className="icon-button"
            type="button"
            title="Reconnect preview"
            aria-label="Reconnect preview"
            disabled={!model.cameraOnline}
            onClick={model.reconnect}
          >
            <RefreshCw size={18} />
          </button>
        </div>
      </div>
      <div className="preview-frame">
        {model.cameraOnline ? (
          <img
            key={model.previewKey}
            src="/api/preview"
            alt="Live camera preview"
            className={`${
              model.flips.horizontal ? "software-flip-horizontal" : ""
            } ${model.flips.vertical ? "software-flip-vertical" : ""}`.trim()}
            onError={() => model.reportError("Preview stream is unavailable")}
          />
        ) : (
          <div className="preview-offline">
            <WifiOff size={36} aria-hidden="true" />
          </div>
        )}
        <div className={`preview-badge ${model.cameraOnline ? "" : "offline"}`}>
          {model.cameraOnline ? <Wifi size={14} /> : <WifiOff size={14} />}
          {model.cameraOnline ? "Live" : "Offline"}
        </div>
      </div>
      <div className="metrics-strip">
        <Metric icon={Activity} label="Schedule" value={model.scheduleSummary} />
        <Metric icon={HardDrive} label="Targets" value={String(model.storageCount)} />
        <Metric icon={Camera} label="Image" value={model.imagePolicy || "—"} />
      </div>
    </div>
  );
}
