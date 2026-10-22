import { AlertTriangle, SlidersHorizontal } from "lucide-react";

import CameraControlField from "./CameraControlField";

export default function CameraParametersEditor({ model }) {
  return (
    <div className="field-group">
      <div className="field-heading">
        <span className="field-label field-label-status">
          Camera parameters
          {!model.controlsAvailable && (
            <AlertTriangle size={14} aria-label="Camera controls unavailable" />
          )}
        </span>
        <button
          className={`small-icon-button parameter-toggle ${
            model.cameraParametersOpen ? "active" : ""
          }`}
          type="button"
          title={
            model.cameraParametersOpen
              ? "Hide camera parameters"
              : "Show camera parameters"
          }
          aria-label={
            model.cameraParametersOpen
              ? "Hide camera parameters"
              : "Show camera parameters"
          }
          aria-expanded={model.cameraParametersOpen}
          disabled={!model.controlsAvailable}
          onClick={model.toggleCameraParameters}
        >
          <SlidersHorizontal size={16} />
        </button>
      </div>
      {model.cameraParametersOpen && (
        <>
          {model.controls.length > 0 ? (
            <div className="camera-controls">
              {model.controls.map((control) => (
                <CameraControlField
                  key={control.name}
                  control={control}
                  value={model.controlValues[control.name]}
                  onChange={(value) => model.changeControl(control.name, value)}
                />
              ))}
            </div>
          ) : (
            <textarea
              value={model.params}
              onChange={(event) => model.setParams(event.target.value)}
              spellCheck="false"
              rows="6"
            />
          )}
        </>
      )}
    </div>
  );
}
