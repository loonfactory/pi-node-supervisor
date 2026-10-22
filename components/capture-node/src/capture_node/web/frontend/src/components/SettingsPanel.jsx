import {
  AlertTriangle,
  FolderOpen,
  RefreshCw,
  Save,
  Settings2,
  X,
} from "lucide-react";

import CameraDeviceEditor from "./settings/CameraDeviceEditor";
import CameraParametersEditor from "./settings/CameraParametersEditor";
import CaptureScheduleEditor from "./settings/CaptureScheduleEditor";
import ImagePolicyEditor from "./settings/ImagePolicyEditor";

export default function SettingsPanel({ model }) {
  return (
    <aside className="settings-pane">
      <div className="section-heading compact">
        <div>
          <span className="eyebrow">Runtime</span>
          <h2>Capture settings</h2>
        </div>
        <Settings2 size={19} aria-hidden="true" />
      </div>
      <form onSubmit={model.submit}>
        <CameraDeviceEditor model={model} />
        <ImagePolicyEditor model={model} />
        <CaptureScheduleEditor model={model} />
        <CameraParametersEditor model={model} />
        {model.controlWarning && (
          <div className="warning-message" role="status">
            <AlertTriangle size={16} />
            <span>{model.controlWarning}</span>
            <button
              className="warning-dismiss"
              type="button"
              title="Dismiss warning"
              aria-label="Dismiss warning"
              onClick={model.dismissControlWarning}
            >
              <X size={15} />
            </button>
          </div>
        )}
        {model.error && (
          <div className="error-message" role="alert">
            <AlertTriangle size={16} />
            <span>{model.error}</span>
          </div>
        )}
        <button
          className="primary-button"
          type="submit"
          disabled={!model.config || model.saving}
        >
          {model.saving ? (
            <RefreshCw className="spin" size={17} />
          ) : (
            <Save size={17} />
          )}
          {model.saving ? "Applying" : "Apply changes"}
        </button>
      </form>

      <div className="fixed-config">
        <div>
          <span>Camera</span>
          <strong
            title={
              model.config?.camera.type === "v4l2"
                ? model.config.camera.device
                : undefined
            }
          >
            {model.appliedCamera}
          </strong>
        </div>
        <div>
          <span>Image formats</span>
          <strong>{model.imagePolicy || "—"}</strong>
        </div>
        <div>
          <span>Storage</span>
          <div className="fixed-action">
            <strong>
              {model.config?.storages.map((storage) => storage.name).join(", ") ||
                "None"}
            </strong>
            <button
              className="storage-button"
              type="button"
              title="Open saved captures"
              aria-label="Open saved captures"
              disabled={!model.config?.storages.length}
              onClick={model.openStorage}
            >
              <FolderOpen size={16} />
              Open captures
            </button>
          </div>
        </div>
      </div>
    </aside>
  );
}
