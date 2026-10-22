import { AlertTriangle, Check, CircleStop } from "lucide-react";

import { EVENT_LABELS } from "../constants";

export default function EventRow({ item }) {
  const isError =
    item.eventType.includes("failed") || item.eventType.includes("discarded");
  const isWarning =
    item.eventType.includes("skipped") || item.eventType.includes("no_targets");
  const Icon = isError || isWarning
    ? AlertTriangle
    : item.eventType.includes("stopped")
      ? CircleStop
      : Check;
  const time = new Date(item.timestamp).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const detail =
    item.failures?.[0]?.cause?.message ||
    item.cause?.message ||
    item.captureId ||
    (item.targets ? `${item.targets.length} target(s)` : "");

  return (
    <div className={`event-row ${isError ? "error" : isWarning ? "warning" : ""}`}>
      <Icon size={17} aria-hidden="true" />
      <div className="event-name">
        <strong>{EVENT_LABELS[item.eventType] || item.eventType}</strong>
        <span>{detail}</span>
      </div>
      <time>{time}</time>
    </div>
  );
}
