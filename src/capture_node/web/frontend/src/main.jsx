import React, {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  AlertTriangle,
  Camera,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleStop,
  Clock3,
  Download,
  FolderOpen,
  HardDrive,
  Image as ImageIcon,
  Plus,
  RefreshCw,
  Save,
  Settings2,
  SlidersHorizontal,
  Trash2,
  Video,
  Wifi,
  WifiOff,
  X,
} from "lucide-react";
import "./styles.css";

const labels = {
  "lifecycle.started": "Started",
  "lifecycle.stopped": "Stopped",
  "storage.no_targets": "No storage targets",
  "capture.succeeded": "Capture succeeded",
  "capture.failed": "Capture failed",
  "capture.skipped": "Capture skipped",
  "terminal.completed": "Storage completed",
  "terminal.discarded": "Capture discarded",
  "config.changed": "Configuration changed",
};

const DEFAULT_CAPTURE_RANGE = {
  start: "09:00",
  intervalMinutes: 10,
  end: "18:00",
};

function App() {
  const [config, setConfig] = useState(null);
  const [events, setEvents] = useState([]);
  const [eventPage, setEventPage] = useState(1);
  const [eventMeta, setEventMeta] = useState({ total: 0, pages: 1 });
  const [scheduleMode, setScheduleMode] = useState("interval");
  const [intervalParts, setIntervalParts] = useState({
    days: 0,
    hours: 0,
    minutes: 0,
    seconds: 10,
  });
  const [dailyTimes, setDailyTimes] = useState([]);
  const [captureRanges, setCaptureRanges] = useState([DEFAULT_CAPTURE_RANGE]);
  const [timezone, setTimezone] = useState("Asia/Seoul");
  const [imageDraft, setImageDraft] = useState({
    format: "png",
    compression: 7,
    embed_metadata: true,
    raw: { enabled: false, format: "dng" },
  });
  const [params, setParams] = useState("{}");
  const [controls, setControls] = useState([]);
  const [controlsAvailable, setControlsAvailable] = useState(true);
  const [controlWarning, setControlWarning] = useState("");
  const controlWarningShown = useRef(false);
  const [controlValues, setControlValues] = useState({});
  const [dirtyControls, setDirtyControls] = useState([]);
  const dirtyControlsRef = useRef(new Set());
  const lastCaptureIdRef = useRef(null);
  const eventsLoadedRef = useRef(false);
  const [devices, setDevices] = useState([]);
  const [cameraDraft, setCameraDraft] = useState(null);
  const [cameraSaving, setCameraSaving] = useState(false);
  const [cameraParametersOpen, setCameraParametersOpen] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [storageOpen, setStorageOpen] = useState(false);
  const [storageTargets, setStorageTargets] = useState([]);
  const [storageTarget, setStorageTarget] = useState("");
  const [storedFiles, setStoredFiles] = useState([]);
  const [storagePage, setStoragePage] = useState(1);
  const [storageMeta, setStorageMeta] = useState({ total: 0, pages: 1 });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [previewKey, setPreviewKey] = useState(0);
  const [runtimeStatus, setRuntimeStatus] = useState({
    status: "connecting",
    camera_connected: false,
    camera_detail: null,
  });

  const loadConfig = async () => {
    const response = await fetch("/api/config");
    if (!response.ok) throw new Error("Configuration is unavailable");
    const next = await response.json();
    setConfig(next);
    setScheduleMode(scheduleEditorMode(next.capture.mode));
    setIntervalParts(splitInterval(next.capture.interval_seconds || 0));
    setDailyTimes(next.capture.daily_times);
    setCaptureRanges((current) =>
      captureRangesFromTimes(next.capture.daily_times, current),
    );
    setTimezone(next.capture.timezone);
    setImageDraft(next.image);
    setParams(JSON.stringify(next.capture.params, null, 2));
    setCameraDraft(next.camera);
  };

  const loadControls = async ({ preserveDirty = false } = {}) => {
    const response = await fetch("/api/camera/controls");
    if (!response.ok) return;
    const body = await response.json();
    const items = body.items;
    const available = body.available !== false;
    setControlsAvailable(available);
    if (body.warning?.message) {
      if (!controlWarningShown.current) {
        controlWarningShown.current = true;
        setControlWarning(body.warning.message);
      }
    } else if (available) {
      setControlWarning("");
    }
    setControls(items);
    setControlValues((current) =>
      Object.fromEntries(
        items.map((control) => [
          control.name,
          preserveDirty && dirtyControlsRef.current.has(control.name)
            ? current[control.name]
            : control.value,
        ]),
      ),
    );
    if (!preserveDirty) {
      dirtyControlsRef.current = new Set();
      setDirtyControls([]);
    }
  };

  const loadDevices = async () => {
    const response = await fetch("/api/camera/devices");
    if (!response.ok) return;
    setDevices((await response.json()).items);
  };

  const loadEvents = async (page) => {
    const response = await fetch(`/api/events?page=${page}&page_size=10`);
    if (!response.ok) return;
    const body = await response.json();
    setEvents(body.items);
    setEventMeta({ total: body.total, pages: body.pages });
    if (page > body.pages) setEventPage(body.pages);
    const latestCaptureId = body.latest_capture_id || null;
    const captureChanged =
      eventsLoadedRef.current &&
      latestCaptureId &&
      latestCaptureId !== lastCaptureIdRef.current;
    lastCaptureIdRef.current = latestCaptureId;
    eventsLoadedRef.current = true;
    if (captureChanged) await loadControls({ preserveDirty: true });
  };

  const loadHealth = async () => {
    try {
      const response = await fetch("/api/health");
      if (!response.ok) throw new Error("Runtime is unavailable");
      setRuntimeStatus(await response.json());
    } catch {
      setRuntimeStatus({
        status: "offline",
        camera_connected: false,
        camera_detail: "Runtime is unavailable",
      });
    }
  };

  useEffect(() => {
    loadConfig().catch((cause) => setError(cause.message));
    loadControls();
    loadDevices();
  }, []);

  useEffect(() => {
    loadEvents(eventPage);
    const timer = window.setInterval(() => loadEvents(eventPage), 1500);
    return () => window.clearInterval(timer);
  }, [eventPage]);

  useEffect(() => {
    loadHealth();
    const timer = window.setInterval(loadHealth, 1500);
    return () => window.clearInterval(timer);
  }, []);

  const serverOnline = runtimeStatus.status === "running";
  const cameraOnline = serverOnline && runtimeStatus.camera_connected;
  const status =
    runtimeStatus.status === "connecting"
      ? "Connecting"
      : !serverOnline
        ? "Disconnected"
        : cameraOnline
          ? "Running"
          : "Camera disconnected";
  const storageCount = config?.storages.length ?? 0;
  const appliedScheduleSummary = useMemo(
    () => formatAppliedSchedule(config?.capture),
    [config],
  );
  const cameraConnected = useMemo(
    () => isCameraDraftApplied(cameraDraft, config?.camera, cameraOnline),
    [cameraDraft, cameraOnline, config],
  );
  const softwarePreviewFlips = useMemo(
    () => ({
      horizontal: controls.some(
        (control) =>
          control.name === "horizontal_flip" &&
          control.application === "software" &&
          Boolean(control.value),
      ),
      vertical: controls.some(
        (control) =>
          control.name === "vertical_flip" &&
          control.application === "software" &&
          Boolean(control.value),
      ),
    }),
    [controls],
  );
  const imagePolicyValue =
    imageDraft.format === "png"
      ? imageDraft.compression
      : imageDraft.quality;
  const imagePolicy = useMemo(() => {
    if (!config) return "";
    const value = config.image.format === "png"
      ? `Compression ${config.image.compression}`
      : `Q${config.image.quality}`;
    return `${config.image.format.toUpperCase()} · ${value} · ${
      config.image.embed_metadata ? "Metadata on" : "Metadata off"
    }${config.image.raw?.enabled ? " · DNG RAW" : ""}`;
  }, [config]);

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    setSaving(true);
    try {
      const parsedParams =
        controls.length > 0
          ? dirtyControls.reduce(
              (values, name) => ({ ...values, [name]: controlValues[name] }),
              { ...config.capture.params },
            )
          : JSON.parse(params);
      const intervalSeconds = intervalToSeconds(intervalParts);
      if (scheduleMode === "interval" && intervalSeconds <= 0) {
        throw new Error("Capture interval must be greater than zero");
      }
      const captureMode = scheduleMode;
      const next = {
        ...config,
        image:
          imageDraft.format === "png"
            ? {
                format: "png",
                compression: Number(imageDraft.compression),
                embed_metadata: imageDraft.embed_metadata,
                raw: imageDraft.raw,
              }
            : {
                format: "jpeg",
                quality: Number(imageDraft.quality),
                embed_metadata: imageDraft.embed_metadata,
                raw: imageDraft.raw,
              },
        capture: {
          mode: captureMode,
          interval_seconds: captureMode === "interval" ? intervalSeconds : null,
          daily_times: captureMode === "daily" ? dailyTimes : [],
          timezone,
          params: parsedParams,
        },
      };
      const response = await fetch("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(next),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Update failed");
      setConfig(body);
      setScheduleMode(scheduleEditorMode(body.capture.mode));
      setIntervalParts(splitInterval(body.capture.interval_seconds || 0));
      setDailyTimes(body.capture.daily_times);
      setCaptureRanges((current) =>
        captureRangesFromTimes(body.capture.daily_times, current),
      );
      setTimezone(body.capture.timezone);
      setImageDraft(body.image);
      setParams(JSON.stringify(body.capture.params, null, 2));
      await loadControls();
    } catch (cause) {
      setError(cause.message);
    } finally {
      setSaving(false);
    }
  };

  const changeControl = (name, value) => {
    setControlValues((current) => ({ ...current, [name]: value }));
    setDirtyControls((current) => {
      const next = current.includes(name) ? current : [...current, name];
      dirtyControlsRef.current = new Set(next);
      return next;
    });
  };

  const changeImagePolicyValue = (value) => {
    setImageDraft((current) => ({
      ...current,
      [current.format === "png" ? "compression" : "quality"]: Number(value),
    }));
  };

  const buildCaptureTimes = () => {
    setError("");
    try {
      const generated = captureRanges.flatMap(generateCaptureTimes);
      const unique = new Map(
        generated.map((value) => [timeToSeconds(value), value]),
      );
      if (unique.size !== generated.length) {
        throw new Error("Capture time blocks must not overlap");
      }
      setDailyTimes(
        [...unique.entries()]
          .sort(([left], [right]) => left - right)
          .map(([_seconds, value]) => value),
      );
    } catch (cause) {
      setError(cause.message);
    }
  };

  const changeCaptureRange = (index, key, value) => {
    setCaptureRanges((current) =>
      current.map((range, rangeIndex) =>
        rangeIndex === index ? { ...range, [key]: value } : range,
      ),
    );
  };

  const addCaptureRange = () => {
    setCaptureRanges((current) => [...current, nextCaptureRange(current)]);
  };

  const removeCaptureRange = (index) => {
    setCaptureRanges((current) =>
      current.length === 1
        ? current
        : current.filter((_range, rangeIndex) => rangeIndex !== index),
    );
  };

  const applyCamera = async () => {
    setError("");
    setCameraSaving(true);
    try {
      const document = normalizeCameraOptions(cameraDraft);
      const response = await fetch("/api/camera", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(document),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Camera connection failed");
      setCameraDraft(body);
      setConfig((current) => ({ ...current, camera: body }));
      setPreviewKey((value) => value + 1);
      await loadControls();
      await loadDevices();
      await loadHealth();
    } catch (cause) {
      setError(cause.message);
    } finally {
      setCameraSaving(false);
    }
  };

  const captureNow = async () => {
    setError("");
    setCapturing(true);
    try {
      const response = await fetch("/api/capture", { method: "POST" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Capture failed");
      lastCaptureIdRef.current = body.capture_id;
      await loadControls({ preserveDirty: true });
      setEventPage(1);
      await loadEvents(1);
    } catch (cause) {
      setError(cause.message);
    } finally {
      setCapturing(false);
    }
  };

  const openStorage = async () => {
    setError("");
    try {
      const response = await fetch("/api/storage/targets");
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Storage is unavailable");
      if (body.items.length === 0) throw new Error("No storage target is configured");
      setStorageTargets(body.items);
      const target = body.items[0].name;
      setStorageTarget(target);
      setStoragePage(1);
      setStorageOpen(true);
      await loadStoredFiles(target, 1);
    } catch (cause) {
      setError(cause.message);
    }
  };

  const loadStoredFiles = async (target, page) => {
    const response = await fetch(
      `/api/storage/files?target=${encodeURIComponent(target)}&page=${page}&page_size=12`,
    );
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Stored files are unavailable");
    setStoredFiles(body.items);
    setStorageMeta({ total: body.total, pages: body.pages });
  };

  const changeStoragePage = async (page) => {
    setStoragePage(page);
    try {
      await loadStoredFiles(storageTarget, page);
    } catch (cause) {
      setError(cause.message);
    }
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <Camera size={22} aria-hidden="true" />
          <strong>CaptureNode</strong>
        </div>
        <div className="runtime-state">
          <span
            className={`status-dot ${
              cameraOnline ? "online" : serverOnline ? "error" : ""
            }`}
          />
          {status}
        </div>
      </header>

      <main>
        <section className="workspace">
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
                  disabled={capturing || !cameraOnline}
                  onClick={captureNow}
                >
                  {capturing ? (
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
                  disabled={!cameraOnline}
                  onClick={() => setPreviewKey((value) => value + 1)}
                >
                  <RefreshCw size={18} />
                </button>
              </div>
            </div>
            <div className="preview-frame">
              {cameraOnline ? (
                <img
                  key={previewKey}
                  src="/api/preview"
                  alt="Live camera preview"
                  className={`${
                    softwarePreviewFlips.horizontal ? "software-flip-horizontal" : ""
                  } ${
                    softwarePreviewFlips.vertical ? "software-flip-vertical" : ""
                  }`.trim()}
                  onError={() => setError("Preview stream is unavailable")}
                />
              ) : (
                <div className="preview-offline">
                  <WifiOff size={36} aria-hidden="true" />
                </div>
              )}
              <div className={`preview-badge ${cameraOnline ? "" : "offline"}`}>
                {cameraOnline ? <Wifi size={14} /> : <WifiOff size={14} />}
                {cameraOnline ? "Live" : "Offline"}
              </div>
            </div>
            <div className="metrics-strip">
              <Metric
                icon={Activity}
                label="Schedule"
                value={appliedScheduleSummary}
              />
              <Metric icon={HardDrive} label="Targets" value={String(storageCount)} />
              <Metric icon={Camera} label="Image" value={imagePolicy || "—"} />
            </div>
          </div>

          <aside className="settings-pane">
            <div className="section-heading compact">
              <div>
                <span className="eyebrow">Runtime</span>
                <h2>Capture settings</h2>
              </div>
              <Settings2 size={19} aria-hidden="true" />
            </div>
            <form onSubmit={submit}>
              {cameraDraft && (
                <div className="field-group camera-device-group">
                  <div className="field-heading">
                    <span className="field-label">Camera device</span>
                    <button
                      className="small-icon-button"
                      type="button"
                      title="Refresh camera devices"
                      aria-label="Refresh camera devices"
                      onClick={loadDevices}
                    >
                      <RefreshCw size={16} />
                    </button>
                  </div>
                  <select
                    value={cameraOptionIdentifier(cameraDraft)}
                    title={
                      cameraDraft.type === "v4l2" ? cameraDraft.device : undefined
                    }
                    onChange={(event) => {
                      const selected = devices.find(
                        (device) => device.id === event.target.value,
                      );
                      if (selected) setCameraDraft(selected.options);
                    }}
                  >
                    {devices.map((device) => (
                      <option key={device.id} value={device.id}>
                        {cameraDeviceLabel(device)}
                      </option>
                    ))}
                    {!devices.some(
                      (device) =>
                        device.id === cameraOptionIdentifier(cameraDraft),
                    ) && (
                      <option value={cameraOptionIdentifier(cameraDraft)}>
                        Configured · {formatCameraOptions(cameraDraft)}
                      </option>
                    )}
                  </select>
                  <div className="camera-format-grid">
                    {cameraFormatFields(cameraDraft.type).map((field) => (
                      <label key={field.key}>
                        <input
                          type="number"
                          min={field.min}
                          max={field.max}
                          step={field.step}
                          value={cameraDraft[field.key] ?? ""}
                          onChange={(event) =>
                            setCameraDraft((current) => ({
                              ...current,
                              [field.key]: event.target.value,
                            }))
                          }
                        />
                        <span>{field.label}</span>
                      </label>
                    ))}
                  </div>
                  <button
                    className="secondary-button"
                    type="button"
                    disabled={cameraSaving || cameraConnected}
                    onClick={applyCamera}
                  >
                    {cameraSaving ? (
                      <RefreshCw className="spin" size={17} />
                    ) : cameraConnected ? (
                      <Check size={17} />
                    ) : (
                      <Video size={17} />
                    )}
                    {cameraSaving
                      ? "Connecting"
                      : cameraConnected
                        ? "Connected camera"
                        : "Connect camera"}
                  </button>
                </div>
              )}
              <div className="field-group image-policy-editor">
                <span className="field-label">Image policy</span>
                <label>
                  Format
                  <select
                    value={imageDraft.format}
                    onChange={(event) =>
                      setImageDraft((current) =>
                        event.target.value === "png"
                          ? {
                              format: "png",
                              compression: 7,
                              embed_metadata: current.embed_metadata,
                              raw: current.raw,
                            }
                          : {
                              format: "jpeg",
                              quality: 90,
                              embed_metadata: current.embed_metadata,
                              raw: current.raw,
                            },
                      )
                    }
                  >
                    <option value="jpeg">JPEG</option>
                    <option value="png">PNG</option>
                  </select>
                </label>
                <label className="image-quality-control">
                  <span>
                    {imageDraft.format === "png" ? "Compression" : "Quality"}
                  </span>
                  <div>
                    <input
                      type="range"
                      min={imageDraft.format === "png" ? "0" : "1"}
                      max={imageDraft.format === "png" ? "9" : "100"}
                      step="1"
                      value={imagePolicyValue}
                      onChange={(event) => changeImagePolicyValue(event.target.value)}
                    />
                    <input
                      type="number"
                      min={imageDraft.format === "png" ? "0" : "1"}
                      max={imageDraft.format === "png" ? "9" : "100"}
                      step="1"
                      value={imagePolicyValue}
                      onChange={(event) => changeImagePolicyValue(event.target.value)}
                    />
                  </div>
                </label>
                <label className="image-metadata-control">
                  <span>Embed metadata</span>
                  <input
                    type="checkbox"
                    checked={imageDraft.embed_metadata}
                    onChange={(event) =>
                      setImageDraft((current) => ({
                        ...current,
                        embed_metadata: event.target.checked,
                      }))
                    }
                  />
                </label>
                {config?.camera?.type === "picamera2" && (
                  <label className="image-metadata-control">
                    <span>Save DNG RAW</span>
                    <input
                      type="checkbox"
                      checked={Boolean(imageDraft.raw?.enabled)}
                      onChange={(event) =>
                        setImageDraft((current) => ({
                          ...current,
                          raw: { enabled: event.target.checked, format: "dng" },
                        }))
                      }
                    />
                  </label>
                )}
              </div>
              <div className="field-group">
                <span className="field-label">Schedule mode</span>
                <div className="segmented-control">
                  <button
                    type="button"
                    className={scheduleMode === "interval" ? "active" : ""}
                    onClick={() => setScheduleMode("interval")}
                  >
                    <RefreshCw size={15} />
                    Interval
                  </button>
                  <button
                    type="button"
                    className={scheduleMode === "daily" ? "active" : ""}
                    onClick={() => setScheduleMode("daily")}
                  >
                    <Clock3 size={15} />
                    Daily times
                  </button>
                </div>
              </div>
              {scheduleMode === "interval" ? (
                <div className="field-group">
                  <span className="field-label">Capture interval</span>
                  <div className="duration-grid">
                    {[
                      ["days", "Days", 9999],
                      ["hours", "Hours", 23],
                      ["minutes", "Minutes", 59],
                      ["seconds", "Seconds", 59.99],
                    ].map(([key, label, max]) => (
                      <label key={key}>
                        <input
                          type="number"
                          min="0"
                          max={max}
                          step={key === "seconds" ? "0.01" : "1"}
                          value={intervalParts[key]}
                          onChange={(event) =>
                            setIntervalParts((current) => ({
                              ...current,
                              [key]: Number(event.target.value),
                            }))
                          }
                        />
                        <span>{label}</span>
                      </label>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="field-group">
                  <div className="field-heading">
                    <span className="field-label">Capture times</span>
                    <div className="field-actions">
                      <button
                        className="small-icon-button danger"
                        type="button"
                        title="Clear all capture times"
                        aria-label="Clear all capture times"
                        disabled={dailyTimes.length === 0}
                        onClick={() => setDailyTimes([])}
                      >
                        <Trash2 size={16} />
                      </button>
                      <button
                        className="small-icon-button"
                        type="button"
                        title="Add capture block"
                        aria-label="Add capture block"
                        onClick={addCaptureRange}
                      >
                        <Plus size={16} />
                      </button>
                    </div>
                  </div>
                  <div className="capture-range-list">
                    {captureRanges.map((range, index) => (
                      <div className="capture-range-block" key={index}>
                        <div className="range-block-heading">
                          <span>Block {index + 1}</span>
                          <button
                            className="small-icon-button danger"
                            type="button"
                            title={`Remove capture block ${index + 1}`}
                            aria-label={`Remove capture block ${index + 1}`}
                            disabled={captureRanges.length === 1}
                            onClick={() => removeCaptureRange(index)}
                          >
                            <Trash2 size={15} />
                          </button>
                        </div>
                        <div className="capture-range-grid">
                          <label>
                            <input
                              type="time"
                              value={range.start}
                              onChange={(event) =>
                                changeCaptureRange(index, "start", event.target.value)
                              }
                              required
                            />
                            <span>Start</span>
                          </label>
                          <label>
                            <input
                              type="number"
                              min="1"
                              max="1440"
                              step="1"
                              value={range.intervalMinutes}
                              onChange={(event) =>
                                changeCaptureRange(
                                  index,
                                  "intervalMinutes",
                                  Number(event.target.value),
                                )
                              }
                              required
                            />
                            <span>Every (min)</span>
                          </label>
                          <label>
                            <input
                              type="time"
                              value={range.end}
                              onChange={(event) =>
                                changeCaptureRange(index, "end", event.target.value)
                              }
                              required
                            />
                            <span>End</span>
                          </label>
                        </div>
                      </div>
                    ))}
                  </div>
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={buildCaptureTimes}
                  >
                    <Clock3 size={17} />
                    Generate capture times
                  </button>
                  <div className="field-heading generated-times-heading">
                    <span className="field-label">Generated times</span>
                    <button
                      className="small-icon-button"
                      type="button"
                      title="Add capture time"
                      aria-label="Add capture time"
                      onClick={() =>
                        setDailyTimes((current) => [...current, "12:00:00"])
                      }
                    >
                      <Plus size={16} />
                    </button>
                  </div>
                  <div className="time-list">
                    {dailyTimes.map((value, index) => (
                      <div className="time-row" key={`${index}-${value}`}>
                        <input
                          type="time"
                          step="1"
                          value={value}
                          onChange={(event) =>
                            setDailyTimes((current) =>
                              current.map((item, itemIndex) =>
                                itemIndex === index ? event.target.value : item,
                              ),
                            )
                          }
                        />
                        <button
                          className="small-icon-button danger"
                          type="button"
                          title="Remove capture time"
                          aria-label="Remove capture time"
                          onClick={() =>
                            setDailyTimes((current) =>
                              current.filter((_item, itemIndex) => itemIndex !== index),
                            )
                          }
                        >
                          <Trash2 size={16} />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div className="field-group">
                <div className="field-heading">
                  <span className="field-label field-label-status">
                    Camera parameters
                    {!controlsAvailable && (
                      <AlertTriangle
                        size={14}
                        aria-label="Camera controls unavailable"
                      />
                    )}
                  </span>
                  <button
                    className={`small-icon-button parameter-toggle ${
                      cameraParametersOpen ? "active" : ""
                    }`}
                    type="button"
                    title={
                      cameraParametersOpen
                        ? "Hide camera parameters"
                        : "Show camera parameters"
                    }
                    aria-label={
                      cameraParametersOpen
                        ? "Hide camera parameters"
                        : "Show camera parameters"
                    }
                    aria-expanded={cameraParametersOpen}
                    disabled={!controlsAvailable}
                    onClick={() => setCameraParametersOpen((current) => !current)}
                  >
                    <SlidersHorizontal size={16} />
                  </button>
                </div>
                {cameraParametersOpen && (
                  <>
                    {controls.length > 0 ? (
                      <div className="camera-controls">
                        {controls.map((control) => (
                          <CameraControlField
                            key={control.name}
                            control={control}
                            value={controlValues[control.name]}
                            onChange={(value) => changeControl(control.name, value)}
                          />
                        ))}
                      </div>
                    ) : (
                      <textarea
                        value={params}
                        onChange={(event) => setParams(event.target.value)}
                        spellCheck="false"
                        rows="6"
                      />
                    )}
                  </>
                )}
              </div>
              {controlWarning && (
                <div className="warning-message" role="status">
                  <AlertTriangle size={16} />
                  <span>{controlWarning}</span>
                  <button
                    className="warning-dismiss"
                    type="button"
                    title="Dismiss warning"
                    aria-label="Dismiss warning"
                    onClick={() => setControlWarning("")}
                  >
                    <X size={15} />
                  </button>
                </div>
              )}
              {error && (
                <div className="error-message" role="alert">
                  <AlertTriangle size={16} />
                  <span>{error}</span>
                </div>
              )}
              <button className="primary-button" type="submit" disabled={!config || saving}>
                {saving ? <RefreshCw className="spin" size={17} /> : <Save size={17} />}
                {saving ? "Applying" : "Apply changes"}
              </button>
            </form>

            <div className="fixed-config">
              <div>
                <span>Camera</span>
                <strong title={config?.camera.type === "v4l2" ? config.camera.device : undefined}>
                  {formatAppliedCamera(config?.camera, devices)}
                </strong>
              </div>
              <div>
                <span>Image policy</span>
                <strong>{imagePolicy || "—"}</strong>
              </div>
              <div>
                <span>Storage</span>
                <div className="fixed-action">
                  <strong>
                    {config?.storages.map((storage) => storage.name).join(", ") || "None"}
                  </strong>
                  <button
                    className="storage-button"
                    type="button"
                    title="Open saved captures"
                    aria-label="Open saved captures"
                    disabled={!config?.storages.length}
                    onClick={openStorage}
                  >
                    <FolderOpen size={16} />
                    Open captures
                  </button>
                </div>
              </div>
            </div>
          </aside>
        </section>

        <section className="events-section">
          <div className="section-heading compact">
            <div>
              <span className="eyebrow">Current process</span>
              <h2>Recent activity</h2>
            </div>
            <span className="event-count">{eventMeta.total}</span>
          </div>
          <div className="event-table">
            {events.length === 0 ? (
              <div className="empty-state">No events recorded</div>
            ) : (
              events.map((item, index) => <EventRow key={`${item.timestamp}-${index}`} item={item} />)
            )}
          </div>
          <div className="pagination">
            <button
              className="icon-button"
              type="button"
              title="Previous activity page"
              aria-label="Previous activity page"
              disabled={eventPage <= 1}
              onClick={() => setEventPage((page) => Math.max(1, page - 1))}
            >
              <ChevronLeft size={18} />
            </button>
            <span>
              Page {eventPage} of {eventMeta.pages}
            </span>
            <button
              className="icon-button"
              type="button"
              title="Next activity page"
              aria-label="Next activity page"
              disabled={eventPage >= eventMeta.pages}
              onClick={() =>
                setEventPage((page) => Math.min(eventMeta.pages, page + 1))
              }
            >
              <ChevronRight size={18} />
            </button>
          </div>
        </section>
      </main>
      {storageOpen && (
        <div className="modal-backdrop" role="presentation">
          <section className="storage-modal" role="dialog" aria-modal="true">
            <div className="section-heading compact">
              <div>
                <span className="eyebrow">Storage</span>
                <h2>Saved captures</h2>
              </div>
              <button
                className="icon-button"
                type="button"
                title="Close saved captures"
                aria-label="Close saved captures"
                onClick={() => setStorageOpen(false)}
              >
                <X size={18} />
              </button>
            </div>
            <div className="storage-toolbar">
              <select
                value={storageTarget}
                onChange={async (event) => {
                  const target = event.target.value;
                  setStorageTarget(target);
                  setStoragePage(1);
                  await loadStoredFiles(target, 1);
                }}
              >
                {storageTargets.map((target) => (
                  <option key={target.name} value={target.name}>
                    {target.name}
                  </option>
                ))}
              </select>
              <span>{storageMeta.total} files</span>
            </div>
            {storedFiles.length === 0 ? (
              <div className="empty-state">No captures stored</div>
            ) : (
              <div className="storage-grid">
                {storedFiles.map((file) => {
                  const url = storageFileUrl(storageTarget, file.name);
                  const previewable = /\.(jpe?g|png)$/i.test(file.name);
                  return (
                    <article className="stored-file" key={file.name}>
                      <a href={url} target="_blank" rel="noreferrer">
                        {previewable ? (
                          <img src={url} alt={file.name} loading="lazy" />
                        ) : (
                          <ImageIcon size={30} />
                        )}
                      </a>
                      <div>
                        <strong title={file.name}>{file.name}</strong>
                        <span>{formatBytes(file.size)}</span>
                      </div>
                      <a
                        className="small-icon-button"
                        href={url}
                        download={file.name}
                        title="Download capture"
                        aria-label={`Download ${file.name}`}
                      >
                        <Download size={16} />
                      </a>
                    </article>
                  );
                })}
              </div>
            )}
            <div className="pagination">
              <button
                className="icon-button"
                type="button"
                title="Previous storage page"
                aria-label="Previous storage page"
                disabled={storagePage <= 1}
                onClick={() => changeStoragePage(Math.max(1, storagePage - 1))}
              >
                <ChevronLeft size={18} />
              </button>
              <span>
                Page {storagePage} of {storageMeta.pages}
              </span>
              <button
                className="icon-button"
                type="button"
                title="Next storage page"
                aria-label="Next storage page"
                disabled={storagePage >= storageMeta.pages}
                onClick={() =>
                  changeStoragePage(Math.min(storageMeta.pages, storagePage + 1))
                }
              >
                <ChevronRight size={18} />
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

function intervalToSeconds(parts) {
  return (
    Number(parts.days || 0) * 86400 +
    Number(parts.hours || 0) * 3600 +
    Number(parts.minutes || 0) * 60 +
    Number(parts.seconds || 0)
  );
}

function splitInterval(totalSeconds) {
  let remaining = Number(totalSeconds || 0);
  const days = Math.floor(remaining / 86400);
  remaining -= days * 86400;
  const hours = Math.floor(remaining / 3600);
  remaining -= hours * 3600;
  const minutes = Math.floor(remaining / 60);
  const seconds = Number((remaining - minutes * 60).toFixed(2));
  return { days, hours, minutes, seconds };
}

function formatDuration(parts) {
  const values = [
    [parts.days, "d"],
    [parts.hours, "h"],
    [parts.minutes, "m"],
    [parts.seconds, "s"],
  ]
    .filter(([value]) => Number(value) > 0)
    .map(([value, unit]) => `${value}${unit}`);
  return values.join(" ") || "0s";
}

function formatAppliedSchedule(capture) {
  if (!capture) return "Schedule unavailable";
  if (capture.mode === "disabled") return "Preview only";
  if (capture.mode === "daily") {
    if (capture.daily_times.length === 0) {
      return `Daily · Preview only · ${capture.timezone}`;
    }
    const blocks = partitionCaptureTimes(capture.daily_times);
    const intervals = blocks.map(captureBlockInterval);
    const commonInterval =
      intervals[0] &&
      intervals.every((interval) => interval === intervals[0])
        ? intervals[0]
        : null;
    const summary = commonInterval
      ? `${blocks.map(formatCaptureBlockRange).join(" / ")} · Every ${formatScheduleInterval(
          commonInterval,
        )}`
      : blocks.map(formatCaptureBlock).join(" / ");
    return `${summary} · ${capture.timezone}`;
  }
  return `Every ${formatDuration(splitInterval(capture.interval_seconds))}`;
}

function scheduleEditorMode(mode) {
  return mode === "disabled" ? "daily" : mode;
}

function generateCaptureTimes(range) {
  const start = timeToMinutes(range.start);
  const end = timeToMinutes(range.end);
  const interval = Number(range.intervalMinutes);
  if (!Number.isInteger(interval) || interval <= 0) {
    throw new Error("Capture time interval must be a positive whole minute");
  }
  if (end < start) {
    throw new Error("Capture end time must not be earlier than start time");
  }
  const times = [];
  for (let minute = start; minute <= end; minute += interval) {
    times.push(`${minutesToTime(minute)}:00`);
  }
  return times;
}

function captureRangesFromTimes(times, fallback = [DEFAULT_CAPTURE_RANGE]) {
  if (!times?.length) return fallback;
  const ranges = partitionCaptureTimes(times).map((block) => {
    const start = timeToSeconds(block[0]);
    const end = timeToSeconds(block.at(-1));
    const interval = block.length > 1 ? timeToSeconds(block[1]) - start : null;
    if (
      block.length < 2 ||
      start % 60 !== 0 ||
      end % 60 !== 0 ||
      interval % 60 !== 0
    ) {
      return null;
    }
    return {
      start: secondsToTime(start, false),
      intervalMinutes: interval / 60,
      end: secondsToTime(end, false),
    };
  });
  return ranges.every(Boolean) ? ranges : fallback;
}

function partitionCaptureTimes(times) {
  const sorted = [...new Set(times)]
    .map((value) => ({ value, seconds: timeToSeconds(value) }))
    .sort((left, right) => left.seconds - right.seconds);
  const bestCount = Array(sorted.length + 1).fill(Number.POSITIVE_INFINITY);
  const bestNext = Array(sorted.length + 1).fill(sorted.length);
  const bestLength = Array(sorted.length + 1).fill(0);
  bestCount[sorted.length] = 0;

  for (let start = sorted.length - 1; start >= 0; start -= 1) {
    let interval = null;
    for (let end = start; end < sorted.length; end += 1) {
      if (end === start + 1) {
        interval = sorted[end].seconds - sorted[start].seconds;
      } else if (
        end > start + 1 &&
        sorted[end].seconds - sorted[end - 1].seconds !== interval
      ) {
        break;
      }
      const count = 1 + bestCount[end + 1];
      const length = end - start + 1;
      if (
        count < bestCount[start] ||
        (count === bestCount[start] && length > bestLength[start])
      ) {
        bestCount[start] = count;
        bestNext[start] = end + 1;
        bestLength[start] = length;
      }
    }
  }

  const blocks = [];
  for (let start = 0; start < sorted.length; start = bestNext[start]) {
    blocks.push(
      sorted.slice(start, bestNext[start]).map((item) => item.value),
    );
  }
  return blocks;
}

function formatCaptureBlock(block) {
  if (block.length === 1) return formatScheduleTime(block[0]);
  return `${formatCaptureBlockRange(block)} · Every ${formatScheduleInterval(
    captureBlockInterval(block),
  )}`;
}

function formatCaptureBlockRange(block) {
  if (block.length === 1) return formatScheduleTime(block[0]);
  return `${formatScheduleTime(block[0])}-${formatScheduleTime(block.at(-1))}`;
}

function captureBlockInterval(block) {
  return block.length > 1
    ? timeToSeconds(block[1]) - timeToSeconds(block[0])
    : null;
}

function formatScheduleTime(value) {
  const seconds = timeToSeconds(value);
  return secondsToTime(seconds, seconds % 60 !== 0);
}

function formatScheduleInterval(seconds) {
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds % 60 === 0) return `${seconds / 60}m`;
  return `${seconds}s`;
}

function nextCaptureRange(ranges) {
  const previous = ranges.at(-1) || DEFAULT_CAPTURE_RANGE;
  const start = Math.min(timeToMinutes(previous.end) + 60, 23 * 60 + 59);
  const end = Math.min(start + 60, 23 * 60 + 59);
  return {
    start: minutesToTime(start),
    intervalMinutes: Number(previous.intervalMinutes) || 10,
    end: minutesToTime(end),
  };
}

function timeToMinutes(value) {
  const [hours, minutes] = String(value).split(":").map(Number);
  if (
    !Number.isInteger(hours) ||
    !Number.isInteger(minutes) ||
    hours < 0 ||
    hours > 23 ||
    minutes < 0 ||
    minutes > 59
  ) {
    throw new Error("Capture time must be a valid time");
  }
  return hours * 60 + minutes;
}

function timeToSeconds(value) {
  const parts = String(value).split(":").map(Number);
  const hours = parts[0];
  const minutes = parts[1];
  const seconds = parts[2] ?? 0;
  if (
    !Number.isInteger(hours) ||
    !Number.isInteger(minutes) ||
    !Number.isInteger(seconds) ||
    hours < 0 ||
    hours > 23 ||
    minutes < 0 ||
    minutes > 59 ||
    seconds < 0 ||
    seconds > 59
  ) {
    throw new Error("Capture time must be a valid time");
  }
  return hours * 3600 + minutes * 60 + seconds;
}

function minutesToTime(value) {
  const hours = Math.floor(value / 60);
  const minutes = value % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

function secondsToTime(value, includeSeconds = true) {
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const seconds = value % 60;
  const base = `${String(hours).padStart(2, "0")}:${String(minutes).padStart(
    2,
    "0",
  )}`;
  return includeSeconds ? `${base}:${String(seconds).padStart(2, "0")}` : base;
}

function isCameraDraftApplied(draft, applied, connected) {
  if (!connected || !draft || !applied || draft.type !== applied.type) return false;
  if (draft.type === "v4l2") {
    return (
      draft.device === applied.device &&
      Number(draft.width) === Number(applied.width) &&
      Number(draft.height) === Number(applied.height) &&
      Number(draft.frame_rate) === Number(applied.frame_rate)
    );
  }
  return (
    Number(draft.camera_num) === Number(applied.camera_num) &&
    optionalNumber(draft.width) === optionalNumber(applied.width) &&
    optionalNumber(draft.height) === optionalNumber(applied.height) &&
    Number(draft.preview_fps) === Number(applied.preview_fps) &&
    Number(draft.preview_quality) === Number(applied.preview_quality)
  );
}

function cameraOptionIdentifier(options) {
  if (!options) return "";
  return options.type === "picamera2"
    ? `picamera2:${Number(options.camera_num ?? 0)}`
    : `v4l2:${options.device}`;
}

function cameraDeviceLabel(device) {
  if (device.type === "picamera2") {
    return `Pi CSI · ${device.name} · Camera ${device.options.camera_num}`;
  }
  return `USB/UVC · ${device.name} · ${v4l2DeviceLabel(device.options.device)}`;
}

function formatCameraOptions(options) {
  if (options.type === "picamera2") {
    return `Pi CSI · Camera ${options.camera_num ?? 0}`;
  }
  return `USB/UVC · ${v4l2DeviceLabel(options.device)}`;
}

function formatAppliedCamera(options, devices) {
  if (!options) return "—";
  if (options.type === "picamera2") {
    return `Picamera2 · Camera ${options.camera_num ?? 0} · ${
      options.width ?? "Auto"
    }×${options.height ?? "Auto"}`;
  }
  const discovered = devices.find(
    (device) => device.id === cameraOptionIdentifier(options),
  );
  const label = discovered
    ? cameraDeviceLabel(discovered)
    : formatCameraOptions(options);
  return `${label} · ${options.width}×${options.height}`;
}

function v4l2DeviceLabel(path) {
  const basename = String(path).split("/").filter(Boolean).at(-1) || String(path);
  if (!basename.startsWith("usb-")) return basename;
  return basename
    .replace(/^usb-/, "")
    .replace(/-video-index\d+$/, "")
    .replaceAll("_", " ")
    .replace(/\s+/g, " ")
    .trim();
}

function cameraFormatFields(type) {
  if (type === "picamera2") {
    return [
      { key: "width", label: "Width", min: 1, step: 1 },
      { key: "height", label: "Height", min: 1, step: 1 },
      { key: "preview_fps", label: "Preview FPS", min: 0.1, step: 0.1 },
      {
        key: "preview_quality",
        label: "Preview quality",
        min: 1,
        max: 100,
        step: 1,
      },
    ];
  }
  return [
    { key: "width", label: "Width", min: 1, step: 1 },
    { key: "height", label: "Height", min: 1, step: 1 },
    { key: "frame_rate", label: "FPS", min: 0.1, step: 0.1 },
  ];
}

function normalizeCameraOptions(options) {
  if (options.type === "picamera2") {
    return {
      ...options,
      camera_num: Number(options.camera_num),
      width: optionalNumber(options.width),
      height: optionalNumber(options.height),
      preview_fps: Number(options.preview_fps),
      preview_quality: Number(options.preview_quality),
    };
  }
  return {
    ...options,
    width: Number(options.width),
    height: Number(options.height),
    frame_rate: Number(options.frame_rate),
  };
}

function optionalNumber(value) {
  return value === "" || value === null || value === undefined
    ? null
    : Number(value);
}

function storageFileUrl(target, filename) {
  return `/api/storage/files/${encodeURIComponent(target)}/${encodeURIComponent(filename)}`;
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function Metric({ icon: Icon, label, value }) {
  return (
    <div className="metric">
      <Icon size={17} aria-hidden="true" />
      <span>{label}</span>
      <strong title={value}>{value}</strong>
    </div>
  );
}

function CameraControlField({ control, value, onChange }) {
  if (control.control_type === "bool") {
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

function cameraControlLabel(control) {
  return control.application === "software"
    ? `${control.label} (Software)`
    : control.label;
}

function EventRow({ item }) {
  const isError = item.event_type.includes("failed") || item.event_type.includes("discarded");
  const isWarning = item.event_type.includes("skipped") || item.event_type.includes("no_targets");
  const Icon = isError || isWarning ? AlertTriangle : item.event_type.includes("stopped") ? CircleStop : Check;
  const time = new Date(item.timestamp).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const detail =
    item.failures?.[0]?.cause?.message ||
    item.cause?.message ||
    item.capture_id ||
    (item.targets ? `${item.targets.length} target(s)` : "");

  return (
    <div className={`event-row ${isError ? "error" : isWarning ? "warning" : ""}`}>
      <Icon size={17} aria-hidden="true" />
      <div className="event-name">
        <strong>{labels[item.event_type] || item.event_type}</strong>
        <span>{detail}</span>
      </div>
      <time>{time}</time>
    </div>
  );
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
