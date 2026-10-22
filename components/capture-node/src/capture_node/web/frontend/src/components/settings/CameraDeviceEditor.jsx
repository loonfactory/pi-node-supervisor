import { Check, RefreshCw, Video } from "lucide-react";

import {
  cameraDeviceLabel,
  cameraFormatFields,
  cameraOptionIdentifier,
  formatCameraOptions,
} from "../../lib/presentation";

export default function CameraDeviceEditor({ model }) {
  if (!model.cameraDraft) return null;

  const identifier = cameraOptionIdentifier(model.cameraDraft);
  return (
    <div className="field-group camera-device-group">
      <div className="field-heading">
        <span className="field-label">Camera device</span>
        <button
          className="small-icon-button"
          type="button"
          title="Refresh camera devices"
          aria-label="Refresh camera devices"
          onClick={model.loadDevices}
        >
          <RefreshCw size={16} />
        </button>
      </div>
      <select
        value={identifier}
        title={model.cameraDraft.type === "v4l2" ? model.cameraDraft.device : undefined}
        onChange={(event) => model.selectCamera(event.target.value)}
      >
        {model.devices.map((device) => (
          <option key={device.id} value={device.id}>
            {cameraDeviceLabel(device)}
          </option>
        ))}
        {!model.devices.some((device) => device.id === identifier) && (
          <option value={identifier}>
            Configured · {formatCameraOptions(model.cameraDraft)}
          </option>
        )}
      </select>
      <div className="camera-format-grid">
        {cameraFormatFields(model.cameraDraft.type).map((field) => (
          <label key={field.key}>
            <input
              type="number"
              min={field.min}
              max={field.max}
              step={field.step}
              value={model.cameraDraft[field.key] ?? ""}
              onChange={(event) =>
                model.updateCameraField(field.key, event.target.value)
              }
            />
            <span>{field.label}</span>
          </label>
        ))}
      </div>
      <button
        className="secondary-button"
        type="button"
        disabled={model.cameraSaving || model.cameraConnected}
        onClick={model.applyCamera}
      >
        {model.cameraSaving ? (
          <RefreshCw className="spin" size={17} />
        ) : model.cameraConnected ? (
          <Check size={17} />
        ) : (
          <Video size={17} />
        )}
        {model.cameraSaving
          ? "Connecting"
          : model.cameraConnected
            ? "Connected camera"
            : "Connect camera"}
      </button>
    </div>
  );
}
