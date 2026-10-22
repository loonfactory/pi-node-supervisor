function cameraControlLabel(control) {
  return control.application === "software"
    ? `${control.label} (Software)`
    : control.label;
}

export default function CameraControlField({ control, value, onChange }) {
  if (control.controlType === "bool") {
    return (
      <label className="camera-control boolean-control">
        <span>{cameraControlLabel(control)}</span>
        <input
          type="checkbox"
          checked={Boolean(value)}
          disabled={control.inactive}
          onChange={(event) => onChange(event.target.checked)}
        />
      </label>
    );
  }
  if (Object.keys(control.options).length > 0) {
    return (
      <label className="camera-control menu-control">
        <span>{cameraControlLabel(control)}</span>
        <select
          value={value}
          disabled={control.inactive}
          onChange={(event) => onChange(Number(event.target.value))}
        >
          {Object.entries(control.options).map(([optionValue, label]) => (
            <option key={optionValue} value={optionValue}>
              {label}
            </option>
          ))}
        </select>
      </label>
    );
  }
  return (
    <label className="camera-control numeric-control">
      <span>{cameraControlLabel(control)}</span>
      <div>
        <input
          type="range"
          min={control.minimum}
          max={control.maximum}
          step={control.step || 1}
          value={value}
          disabled={control.inactive}
          onChange={(event) => onChange(Number(event.target.value))}
        />
        <input
          type="number"
          min={control.minimum}
          max={control.maximum}
          step={control.step || 1}
          value={value}
          disabled={control.inactive}
          onChange={(event) => onChange(Number(event.target.value))}
        />
      </div>
    </label>
  );
}
