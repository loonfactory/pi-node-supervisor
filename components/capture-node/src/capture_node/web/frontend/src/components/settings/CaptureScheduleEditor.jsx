import { Clock3, RefreshCw, Trash2 } from "lucide-react";

export default function CaptureScheduleEditor({ model }) {
  return (
    <div className="field-group">
      <div className="field-heading">
        <span className="field-label">Capture schedules</span>
        <div className="field-actions">
          <button
            className="secondary-button"
            type="button"
            onClick={() => model.addCapture("daily")}
          >
            <Clock3 size={15} />
            Once daily
          </button>
          <button
            className="secondary-button"
            type="button"
            onClick={() => model.addCapture("interval")}
          >
            <RefreshCw size={15} />
            Interval
          </button>
        </div>
      </div>
      {model.capturesDraft.length === 0 && (
        <span className="muted-copy">Preview only</span>
      )}
      <div className="capture-range-list">
        {model.capturesDraft.map((capture, index) => (
          <div className="capture-range-block" key={index}>
            <div className="range-block-heading">
              <strong>{capture.type === "daily" ? "Daily" : "Interval"}</strong>
              <button
                className="small-icon-button danger"
                type="button"
                title="Remove capture schedule"
                aria-label="Remove capture schedule"
                onClick={() => model.removeCapture(index)}
              >
                <Trash2 size={15} />
              </button>
            </div>
            {capture.type === "daily" ? (
              <label>
                Time
                <input
                  type="time"
                  step="1"
                  value={capture.time}
                  onChange={(event) =>
                    model.updateCapture(index, "time", event.target.value)
                  }
                  required
                />
              </label>
            ) : (
              <>
                <label>
                  Interval (seconds)
                  <input
                    type="number"
                    min="0.01"
                    step="0.01"
                    value={capture.interval}
                    onChange={(event) =>
                      model.updateCapture(index, "interval", Number(event.target.value))
                    }
                    required
                  />
                </label>
                <label className="image-metadata-control">
                  <span>Daily time window</span>
                  <input
                    type="checkbox"
                    checked={capture.startTime !== undefined}
                    onChange={(event) =>
                      model.setCaptureWindowEnabled(index, event.target.checked)
                    }
                  />
                </label>
                {capture.startTime !== undefined && (
                  <div className="capture-range-grid">
                    <label>
                      <input
                        type="time"
                        step="1"
                        value={capture.startTime}
                        onChange={(event) =>
                          model.updateCapture(index, "startTime", event.target.value)
                        }
                        required
                      />
                      <span>Start</span>
                    </label>
                    <label>
                      <input
                        type="time"
                        step="1"
                        value={capture.endTime}
                        onChange={(event) =>
                          model.updateCapture(index, "endTime", event.target.value)
                        }
                        required
                      />
                      <span>End</span>
                    </label>
                  </div>
                )}
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
