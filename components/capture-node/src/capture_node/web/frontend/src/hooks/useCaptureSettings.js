import { useEffect, useMemo, useRef, useState } from "react";

import { captureNodeApi } from "../api/captureNodeApi";
import { IMAGE_FORMAT_DEFAULTS } from "../constants";
import {
  formatAppliedCamera,
  formatAppliedSchedule,
  isCameraDraftApplied,
  normalizeCameraOptions,
} from "../lib/presentation";

export function useCaptureSettings({ cameraOnline, reloadHealth }) {
  const [config, setConfig] = useState(null);
  const [capturesDraft, setCapturesDraft] = useState([]);
  const [imageDraft, setImageDraft] = useState({
    png: IMAGE_FORMAT_DEFAULTS.png,
  });
  const [params, setParams] = useState("{}");
  const [controls, setControls] = useState([]);
  const [controlsAvailable, setControlsAvailable] = useState(true);
  const [controlWarning, setControlWarning] = useState("");
  const controlWarningShown = useRef(false);
  const [controlValues, setControlValues] = useState({});
  const [dirtyControls, setDirtyControls] = useState([]);
  const dirtyControlsRef = useRef(new Set());
  const [devices, setDevices] = useState([]);
  const [cameraDraft, setCameraDraft] = useState(null);
  const [cameraSaving, setCameraSaving] = useState(false);
  const [cameraParametersOpen, setCameraParametersOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [previewKey, setPreviewKey] = useState(0);

  const loadConfig = async () => {
    const next = await captureNodeApi.config();
    setConfig(next);
    setCapturesDraft(next.captures);
    setImageDraft(next.images);
    setParams(JSON.stringify(next.camera.params, null, 2));
    setCameraDraft(next.camera);
  };

  const loadControls = async ({ preserveDirty = false } = {}) => {
    const body = await captureNodeApi.controls();
    if (!body) return;
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
    const body = await captureNodeApi.devices();
    if (body) setDevices(body.items);
  };

  useEffect(() => {
    loadConfig().catch((cause) => setError(cause.message));
    loadControls();
    loadDevices();
  }, []);

  const scheduleSummary = useMemo(
    () => formatAppliedSchedule(config?.captures),
    [config],
  );
  const cameraConnected = useMemo(
    () => isCameraDraftApplied(cameraDraft, config?.camera, cameraOnline),
    [cameraDraft, cameraOnline, config],
  );
  const flips = useMemo(
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
  const imagePolicy = useMemo(() => {
    if (!config) return "";
    return Object.entries(config.images).map(([format, options]) => {
      const value = format === "png"
        ? ` · Compression ${options.compression}`
        : format === "jpeg"
          ? ` · Q${options.quality}`
          : "";
      return `${format.toUpperCase()}${value} · ${
        options.embedMetadata ? "Metadata on" : "Metadata off"
      }`;
    }).join(" / ");
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
              { ...config.camera.params },
            )
          : JSON.parse(params);
      if (Object.keys(imageDraft).length === 0) {
        throw new Error("Select at least one image format");
      }
      const body = await captureNodeApi.updateConfig({
        ...config,
        images: imageDraft,
        captures: capturesDraft,
        camera: { ...config.camera, params: parsedParams },
      });
      setConfig(body);
      setCapturesDraft(body.captures);
      setImageDraft(body.images);
      setParams(JSON.stringify(body.camera.params, null, 2));
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

  const setImageFormatEnabled = (format, enabled) => {
    setImageDraft((current) =>
      enabled
        ? { ...current, [format]: current[format] || IMAGE_FORMAT_DEFAULTS[format] }
        : Object.fromEntries(
            Object.entries(current).filter(([key]) => key !== format),
          ),
    );
  };

  const updateImageFormat = (format, property, value) => {
    setImageDraft((current) => ({
      ...current,
      [format]: { ...current[format], [property]: value },
    }));
  };

  const updateCapture = (index, property, value) => {
    setCapturesDraft((current) =>
      current.map((capture, captureIndex) =>
        captureIndex === index ? { ...capture, [property]: value } : capture,
      ),
    );
  };

  const addCapture = (type) => {
    setCapturesDraft((current) => [
      ...current,
      type === "daily"
        ? { type: "daily", time: "12:00:00" }
        : { type: "interval", interval: 60 },
    ]);
  };

  const removeCapture = (index) => {
    setCapturesDraft((current) =>
      current.filter((_item, itemIndex) => itemIndex !== index),
    );
  };

  const setCaptureWindowEnabled = (index, enabled) => {
    setCapturesDraft((current) =>
      current.map((item, itemIndex) => {
        if (itemIndex !== index) return item;
        if (enabled) {
          return { ...item, startTime: "09:00:00", endTime: "18:00:00" };
        }
        const { startTime: _start, endTime: _end, ...continuous } = item;
        return continuous;
      }),
    );
  };

  const applyCamera = async () => {
    setError("");
    setCameraSaving(true);
    try {
      const body = await captureNodeApi.updateCamera(
        normalizeCameraOptions(cameraDraft),
      );
      setCameraDraft(body);
      setConfig((current) => ({ ...current, camera: body }));
      setPreviewKey((value) => value + 1);
      await loadControls();
      await loadDevices();
      await reloadHealth();
    } catch (cause) {
      setError(cause.message);
    } finally {
      setCameraSaving(false);
    }
  };

  const selectCamera = (identifier) => {
    const selected = devices.find((device) => device.id === identifier);
    if (selected) setCameraDraft(selected.options);
  };

  return {
    loadControls,
    reportError: setError,
    preview: {
      previewKey,
      reconnect: () => setPreviewKey((value) => value + 1),
      flips,
      reportError: setError,
      scheduleSummary,
      storageCount: config?.storages.length ?? 0,
      imagePolicy,
    },
    settings: {
      config,
      devices,
      cameraDraft,
      cameraSaving,
      cameraConnected,
      loadDevices,
      selectCamera,
      updateCameraField: (key, value) =>
        setCameraDraft((current) => ({ ...current, [key]: value })),
      applyCamera,
      imageDraft,
      setImageFormatEnabled,
      updateImageFormat,
      capturesDraft,
      addCapture,
      removeCapture,
      updateCapture,
      setCaptureWindowEnabled,
      controls,
      controlsAvailable,
      controlValues,
      changeControl,
      cameraParametersOpen,
      toggleCameraParameters: () => setCameraParametersOpen((current) => !current),
      params,
      setParams,
      controlWarning,
      dismissControlWarning: () => setControlWarning(""),
      error,
      saving,
      submit,
      imagePolicy,
      appliedCamera: formatAppliedCamera(config?.camera, devices),
    },
  };
}
