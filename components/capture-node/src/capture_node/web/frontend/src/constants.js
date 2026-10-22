export const EVENT_LABELS = {
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

export const IMAGE_FORMAT_DEFAULTS = {
  png: { compression: 7, embedMetadata: true },
  jpeg: { quality: 90, embedMetadata: true },
  dng: { embedMetadata: true },
};
